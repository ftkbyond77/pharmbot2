"""
agent/nodes/clinical_reason.py
-------------------------------
Node 4: Clinical reasoning — Augmented Generation

Improvements:
- Passes conversation history to prompt (not just user_message)
- Prompt now supports grounding (guideline) + augmentation (general knowledge)
- knowledge_gaps extracted → can trigger Phase 2 web-search hook
- Structured parse with graceful degradation per field

Input  : state.user_message, state.history, state.retrieved_chunks
Output : state.symptom_summary, state.differential_diagnosis,
         state.clinical_rationale, state.red_flags_found, state.next_action
"""

from __future__ import annotations

import json

from langchain_google_genai import ChatGoogleGenerativeAI
from loguru import logger

from api.agent.state import AgentState, DDxItem
from api.config import get_settings
from api.knowledge.retriever import get_retriever
from api.prompts.pharmacist import (
    SYSTEM_PROMPT,
    clinical_reason_prompt,
    _format_history_full,
    strip_fences,
)


def clinical_reason_node(state: AgentState) -> dict:
    cfg = get_settings()
    llm = ChatGoogleGenerativeAI(
        model=cfg.gemini_model,
        google_api_key=cfg.gemini_api_key,
        temperature=cfg.llm_temp_clinical,
    )

    retriever    = get_retriever()
    history      = state.get("history", [])
    history_text = _format_history_full(history, max_turns=6)

    # Build symptom narrative from full history
    symptom_text = _build_symptom_narrative(state)

    # Format retrieved context with citations
    context_text = retriever.format_context(state.get("retrieved_chunks", []))

    prompt   = clinical_reason_prompt(symptom_text, context_text, history_text)
    response = llm.invoke([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": prompt},
    ])

    # ── parse with field-level graceful degradation ───────────
    symptom_summary: list[str] = []
    ddx: list[DDxItem]         = []
    rationale: list[str]       = []
    red_flags: list[str]       = []
    knowledge_gaps: list[str]  = []

    try:
        raw  = strip_fences(response.content)
        data = json.loads(raw)

        symptom_summary = _ensure_list(data.get("symptom_summary", []))
        ddx             = _parse_ddx(data.get("differential_diagnosis", []))
        rationale       = _ensure_list(data.get("clinical_rationale", []))
        red_flags       = _ensure_list(data.get("red_flags", []))
        knowledge_gaps  = _ensure_list(data.get("knowledge_gaps", []))

    except json.JSONDecodeError as exc:
        logger.warning(f"[clinical_reason] JSON parse failed: {exc} | using fallback")
        symptom_summary = [state["user_message"]]
    except Exception as exc:
        logger.error(f"[clinical_reason] unexpected error: {exc}")
        symptom_summary = [state["user_message"]]

    logger.info(
        f"[clinical_reason] symptoms={len(symptom_summary)} "
        f"ddx={len(ddx)} red_flags={red_flags} gaps={knowledge_gaps}"
    )

    # Surface knowledge gaps for debugging / Phase 2 web search
    if knowledge_gaps:
        logger.debug(f"[clinical_reason] knowledge_gaps: {knowledge_gaps}")

    return {
        "symptom_summary":        symptom_summary,
        "differential_diagnosis": ddx,
        "clinical_rationale":     rationale,
        "red_flags_found":        red_flags,
        "next_action":            "safety_gate",
    }


def _build_symptom_narrative(state: AgentState) -> str:
    """
    Aggregate all user messages into a readable symptom narrative.
    Prioritise extracted symptom_summary from prior rounds if available.
    """
    prior_summary = state.get("symptom_summary", [])
    if prior_summary:
        return " | ".join(prior_summary)

    history = state.get("history", [])
    user_msgs = [
        h["content"] for h in history if h.get("role") == "user"
    ]
    if not user_msgs:
        return state.get("user_message", "")

    # add latest message if not already included
    latest = state.get("user_message", "")
    if latest and (not user_msgs or user_msgs[-1] != latest):
        user_msgs.append(latest)

    return "\n".join(f"- {m}" for m in user_msgs)


def _parse_ddx(raw_list: list) -> list[DDxItem]:
    """Parse DDx list with validation."""
    result = []
    valid_confidence = {"high", "medium", "low"}
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        name       = str(item.get("name", "")).strip()
        confidence = str(item.get("confidence", "low")).lower()
        if confidence not in valid_confidence:
            confidence = "low"
        if name:
            result.append(DDxItem(name=name, confidence=confidence))
    return result


_EMPTY_MARKERS = {"ไม่มี", "none", "n/a", "-", "–", "ไม่พบ", ""}

def _ensure_list(val) -> list[str]:
    if isinstance(val, list):
        return [
            str(v).strip() for v in val
            if v and str(v).strip().lower() not in _EMPTY_MARKERS
        ]
    if isinstance(val, str) and val.strip().lower() not in _EMPTY_MARKERS:
        return [val.strip()]
    return []