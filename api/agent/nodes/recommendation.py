"""
agent/nodes/recommendation.py — v4
Changes vs v3:
- อ่าน allergy_detail_incomplete จาก state → ส่งเข้า recommendation_prompt
- อ่าน needs_rx_change_warning จาก clinical_scores (เหมือนเดิม)
"""
from __future__ import annotations
import json
from langchain_google_genai import ChatGoogleGenerativeAI
from loguru import logger
from api.agent.state import AgentState
from api.config import get_settings
from api.knowledge.retriever import get_retriever
from api.prompts.pharmacist import (
    SYSTEM_PROMPT, recommendation_prompt,
    _format_history_full, strip_fences,
)


def recommendation_node(state: AgentState) -> dict:
    cfg = get_settings()
    llm = ChatGoogleGenerativeAI(
        model=cfg.gemini_model,
        google_api_key=cfg.gemini_api_key,
        temperature=cfg.llm_temp_recommend,
    )

    retriever    = get_retriever()
    history      = state.get("history", [])
    history_text = _format_history_full(history, max_turns=6)

    symptom_text   = " | ".join(state.get("symptom_summary", [state["user_message"]]))
    ddx_text       = _format_ddx(state.get("differential_diagnosis", []))
    rationale_text = "\n".join(f"- {r}" for r in state.get("clinical_rationale", [])) or "(ไม่มีข้อมูลเพิ่มเติม)"
    context_text   = retriever.format_context(state.get("retrieved_chunks", []))

    needs_pushback  = state.get("needs_pushback", False)
    pushback_reason = state.get("pushback_reason", "")

    # v4: รวม clinical_scores + allergy_detail_incomplete เข้า dict เดียว
    clinical_scores = dict(state.get("clinical_scores", {}))
    if state.get("allergy_detail_incomplete", False):
        clinical_scores["allergy_detail_incomplete"] = True

    prompt = recommendation_prompt(
        symptom_summary=symptom_text,
        ddx_text=ddx_text,
        rationale_text=rationale_text,
        retrieved_context=context_text,
        history_text=history_text,
        needs_pushback=needs_pushback,
        pushback_reason=pushback_reason,
        clinical_scores=clinical_scores,
    )

    response = llm.invoke([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": prompt},
    ])

    recommendation    = ""
    sources:    list[str] = []
    first_line        = None
    alternatives: list[str] = []
    when_to_see       = ""
    augmented_notes   = None
    pushback_message  = None

    try:
        raw  = strip_fences(response.content)
        data = json.loads(raw)
        recommendation  = str(data.get("recommendation", "")).strip()
        sources         = [str(s) for s in data.get("sources", []) if s]
        first_line      = data.get("first_line_drug")
        alternatives    = data.get("alternatives", [])
        when_to_see     = str(data.get("when_to_see_doctor", ""))
        augmented_notes = data.get("augmented_notes")
        pushback_message = data.get("pushback_message")
    except Exception as exc:
        logger.warning(f"[recommendation] JSON parse error: {exc}")
        recommendation = response.content if isinstance(response.content, str) else str(response.content)

    return {
        "recommendation":    recommendation,
        "sources":           sources,
        "first_line_drug":   first_line,
        "alternatives":      alternatives,
        "when_to_see_doctor": when_to_see,
        "augmented_notes":   augmented_notes,
        "pushback_message":  pushback_message,
        "next_action":       "respond",
    }


def _format_ddx(ddx_list: list) -> str:
    if not ddx_list:
        return "ยังไม่ระบุ"
    parts = []
    for d in ddx_list:
        if isinstance(d, dict):
            name       = d.get("name", "")
            confidence = d.get("confidence", "")
            reasoning  = d.get("reasoning", "")
            parts.append(f"{name} ({confidence}): {reasoning}")
        else:
            parts.append(str(d))
    return " | ".join(parts)