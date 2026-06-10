"""
agent/nodes/recommendation.py — v5
Changes vs v4:
- Pass user_lang จาก state เข้า recommendation_prompt
  → LLM ตอบภาษาเดียวกับ user (EN/TH)
- เพิ่ม max_tokens=2048 ใน ChatGoogleGenerativeAI
  → ป้องกัน response truncation ที่พบใน eval
- ส่วนอื่นคงเดิมทั้งหมด
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
        max_tokens=2048,  # v5: ป้องกัน truncation
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

    # v5: ดึง user_lang จาก state
    user_lang = state.get("user_lang", "th")

    prompt = recommendation_prompt(
        symptom_summary=symptom_text,
        ddx_text=ddx_text,
        rationale_text=rationale_text,
        retrieved_context=context_text,
        history_text=history_text,
        needs_pushback=needs_pushback,
        pushback_reason=pushback_reason,
        clinical_scores=clinical_scores,
        user_lang=user_lang,   # v5: pass language
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
        alternatives    = [str(a) for a in data.get("alternatives", []) if a]
        when_to_see     = str(data.get("when_to_see_doctor", "")).strip()
        augmented_notes = data.get("augmented_notes")
        pushback_message = data.get("pushback_message")
    except json.JSONDecodeError as exc:
        logger.warning(f"[recommendation] JSON parse failed: {exc}")
        # graceful fallback: ใช้ raw content เป็น recommendation
        raw_content = response.content
        if isinstance(raw_content, list):
            recommendation = " ".join(
                p.text if hasattr(p, "text")
                else p.get("text", str(p)) if isinstance(p, dict)
                else str(p)
                for p in raw_content
            ).strip()
        else:
            recommendation = str(raw_content).strip()

    if not recommendation:
        recommendation = "ขออภัยครับ ไม่สามารถประมวลผลได้ในขณะนี้" if user_lang == "th" \
                         else "I'm sorry, I was unable to process your request at this time."

    logger.info(
        f"[recommendation] lang={user_lang} pushback={bool(pushback_message)} "
        f"sources={len(sources)} first_line={first_line}"
    )

    return {
        "recommendation":  recommendation,
        "sources":         sources,
        "first_line_drug": first_line,
        "alternatives":    alternatives,
        "when_to_see_doctor": when_to_see,
        "augmented_notes": augmented_notes,
        "pushback_message": pushback_message,
    }


# ── helpers ────────────────────────────────────────────────────

def _format_ddx(ddx: list) -> str:
    if not ddx:
        return "(ยังไม่ระบุ)"
    parts = []
    for d in ddx[:3]:
        if isinstance(d, dict):
            name = d.get("name", "")
            conf = d.get("confidence", "")
            parts.append(f"{name} ({conf})")
        else:
            parts.append(str(d))
    return ", ".join(parts)