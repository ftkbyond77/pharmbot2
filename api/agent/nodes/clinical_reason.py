"""
agent/nodes/clinical_reason.py  (v4)
----------------------------------------------
Base: v3

Changes v4:
- เพิ่ม max_tokens=1024 ใน ChatGoogleGenerativeAI
  → ป้องกัน JSON truncation ใน clinical reasoning step
- ส่วน logic ทั้งหมดคงเดิมจาก v3
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
        max_tokens=1024,  # v4: ป้องกัน JSON truncation
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
    symptom_summary: list[str]      = []
    ddx: list[DDxItem]              = []
    rationale: list[str]            = []
    red_flags: list[str]            = []
    knowledge_gaps: list[str]       = []
    clinical_scores: dict           = {}
    needs_pushback: bool            = False
    pushback_reason: str | None     = None
    needs_rx_change_warning: bool   = False
    allergy_detail_incomplete: bool = False

    try:
        raw  = strip_fences(response.content)
        data = json.loads(raw)

        symptom_summary           = _ensure_list(data.get("symptom_summary", []))
        ddx                       = _parse_ddx(data.get("differential_diagnosis", []))
        rationale                 = _ensure_list(data.get("clinical_rationale", []))
        red_flags                 = _ensure_list(data.get("red_flags", []))
        knowledge_gaps            = _ensure_list(data.get("knowledge_gaps", []))
        clinical_scores           = data.get("clinical_scores", {}) or {}
        needs_pushback            = bool(data.get("needs_pushback", False))
        pushback_reason           = data.get("pushback_reason")
        needs_rx_change_warning   = bool(data.get("needs_rx_change_warning", False))
        allergy_detail_incomplete = bool(data.get("allergy_detail_incomplete", False))

    except json.JSONDecodeError as exc:
        logger.warning(f"[clinical_reason] JSON parse failed: {exc} — using raw text")
        symptom_summary = [symptom_text[:200]]

    # ── Merge needs_rx_change_warning into clinical_scores ────
    if needs_rx_change_warning:
        clinical_scores["needs_rx_change_warning"] = True

    logger.info(
        f"[clinical_reason] ddx={[d['name'] for d in ddx[:3]]} "
        f"red_flags={red_flags} needs_pushback={needs_pushback} "
        f"allergy_incomplete={allergy_detail_incomplete} "
        f"centor={clinical_scores.get('mcisaac')} "
        f"aom={clinical_scores.get('aom_severity')}"
    )

    return {
        "symptom_summary":           symptom_summary,
        "differential_diagnosis":    ddx,
        "clinical_rationale":        rationale,
        "red_flags_found":           red_flags,
        "knowledge_gaps":            knowledge_gaps,
        "clinical_scores":           clinical_scores,
        "needs_pushback":            needs_pushback,
        "pushback_reason":           pushback_reason,
        "allergy_detail_incomplete": allergy_detail_incomplete,
        "next_action":               "safety_gate",
    }


# ── helpers ───────────────────────────────────────────────────

def _build_symptom_narrative(state: AgentState) -> str:
    prior_summary: list[str] = state.get("symptom_summary", [])
    user_message: str        = state.get("user_message", "")
    domain: str              = state.get("symptom_domain", "general")
    complexity: str          = state.get("symptom_complexity", "moderate")

    parts: list[str] = []
    if prior_summary:
        parts.append("Prior summary: " + " | ".join(prior_summary))
    parts.append(f"Latest: {user_message}")
    parts.append(f"[domain={domain} complexity={complexity}]")
    return "\n".join(parts)


def _ensure_list(val) -> list:
    if isinstance(val, list):
        return [str(v) for v in val if v]
    if val:
        return [str(val)]
    return []


def _parse_ddx(raw: list) -> list[DDxItem]:
    result: list[DDxItem] = []
    for item in raw:
        if isinstance(item, dict):
            result.append(DDxItem(
                name=str(item.get("name", "")),
                confidence=item.get("confidence", "low"),
                reasoning=str(item.get("reasoning", "")),
            ))
    return result