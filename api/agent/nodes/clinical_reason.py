"""
agent/nodes/clinical_reason.py  (v2 — Tuned)
----------------------------------------------
Node 4: Clinical reasoning — Augmented Generation

CHANGES v2:
- Extracts clinical_scores (Centor, AOM severity, sinusitis criteria) from LLM
- Extracts needs_pushback + pushback_reason for Negative Case handling
- Passes symptom_domain + symptom_complexity from clarify into query building
- Structured parse with graceful degradation per field

Input  : state.user_message, state.history, state.retrieved_chunks,
         state.symptom_domain, state.symptom_complexity
Output : state.symptom_summary, state.differential_diagnosis,
         state.clinical_rationale, state.red_flags_found,
         state.clinical_scores, state.needs_pushback, state.pushback_reason,
         state.next_action
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

    symptom_text = _build_symptom_narrative(state)
    context_text = retriever.format_context(state.get("retrieved_chunks", []))

    prompt   = clinical_reason_prompt(symptom_text, context_text, history_text)
    response = llm.invoke([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": prompt},
    ])

    # ── Parse with field-level graceful degradation ───────────
    symptom_summary: list[str]  = []
    ddx: list[DDxItem]          = []
    rationale: list[str]        = []
    red_flags: list[str]        = []
    knowledge_gaps: list[str]   = []
    clinical_scores: dict       = {}
    needs_pushback: bool        = False
    pushback_reason: str | None = None

    try:
        raw  = strip_fences(response.content)
        data = json.loads(raw)

        symptom_summary = _ensure_list(data.get("symptom_summary", []))
        ddx             = _parse_ddx(data.get("differential_diagnosis", []))
        rationale       = _ensure_list(data.get("clinical_rationale", []))
        red_flags       = _ensure_list(data.get("red_flags", []))
        knowledge_gaps  = _ensure_list(data.get("knowledge_gaps", []))
        clinical_scores = data.get("clinical_scores", {}) or {}
        needs_pushback  = bool(data.get("needs_pushback", False))
        pushback_reason = data.get("pushback_reason")

    except json.JSONDecodeError as exc:
        logger.warning(f"[clinical_reason] JSON parse failed: {exc} — using raw text")
        symptom_summary = [symptom_text[:200]]

    logger.info(
        f"[clinical_reason] ddx={[d['name'] for d in ddx[:3]]} "
        f"red_flags={red_flags} needs_pushback={needs_pushback} "
        f"centor={clinical_scores.get('centor_score')} "
        f"aom={clinical_scores.get('aom_severity')}"
    )

    return {
        "symptom_summary":         symptom_summary,
        "differential_diagnosis":  ddx,
        "clinical_rationale":      rationale,
        "red_flags_found":         red_flags,
        "knowledge_gaps":          knowledge_gaps,
        "clinical_scores":         clinical_scores,
        "needs_pushback":          needs_pushback,
        "pushback_reason":         pushback_reason,
        "next_action":             "safety_gate",
    }


# ── helpers ───────────────────────────────────────────────────

def _build_symptom_narrative(state: AgentState) -> str:
    """
    Build a rich symptom narrative:
    1. Use prior symptom_summary if available (from clarify rounds)
    2. Augment with domain/complexity info
    3. Fall back to recent user turns
    """
    symptom_summary: list[str] = state.get("symptom_summary", [])
    domain      = state.get("symptom_domain", "general")
    complexity  = state.get("symptom_complexity", "moderate")

    if symptom_summary:
        base = " | ".join(symptom_summary)
        return f"[Domain: {domain}, Complexity: {complexity}] {base}"

    history = state.get("history", [])
    user_turns = [
        h["content"]
        for h in history
        if h.get("role") == "user"
    ][-4:]

    parts = user_turns + [state["user_message"]]
    narrative = " ".join(p.strip() for p in parts if p.strip())
    return f"[Domain: {domain}, Complexity: {complexity}] {narrative}"


def _ensure_list(val) -> list:
    if isinstance(val, list):
        return [str(v) for v in val if v]
    if val:
        return [str(val)]
    return []


def _parse_ddx(raw_ddx) -> list[DDxItem]:
    if not isinstance(raw_ddx, list):
        return []
    result = []
    for item in raw_ddx:
        if not isinstance(item, dict):
            continue
        try:
            result.append(DDxItem(
                name=str(item.get("name", "Unknown")),
                confidence=str(item.get("confidence", "low")),
                reasoning=str(item.get("reasoning", "")),
            ))
        except Exception:
            pass
    return result