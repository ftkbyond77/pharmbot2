"""
agent/nodes/followup.py — v2
Changes vs v1:
- อ่าน response_type จาก LLM response ("conversational" | "diagnosis_explain")
- ถ้า diagnosis_explain → คง differential_diagnosis + sources ไว้ใน state
- ถ้า conversational → clear differential_diagnosis + retrieved_chunks
"""
from __future__ import annotations

import json

from langchain_google_genai import ChatGoogleGenerativeAI
from loguru import logger

from api.agent.state import AgentState
from api.config import get_settings
from api.prompts.pharmacist import (
    SYSTEM_PROMPT,
    followup_prompt,
    strip_fences,
)


def followup_node(state: AgentState) -> dict:
    cfg = get_settings()
    llm = ChatGoogleGenerativeAI(
        model=cfg.gemini_model,
        google_api_key=cfg.gemini_api_key,
        temperature=cfg.llm_temp_recommend,
    )

    history      = state.get("history", [])
    user_message = state["user_message"]

    prompt   = followup_prompt(user_message=user_message, history=history)
    response = llm.invoke([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": prompt},
    ])

    recommendation  = ""
    response_type   = "conversational"
    augmented_notes = None

    try:
        raw  = strip_fences(response.content)
        data = json.loads(raw)
        recommendation  = str(data.get("recommendation", "")).strip()
        response_type   = data.get("response_type", "conversational")
        augmented_notes = data.get("augmented_notes")
    except Exception as exc:
        logger.warning(f"[followup] JSON parse error: {exc}")
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
        recommendation = "ขออภัยครับ ไม่สามารถประมวลผลได้ในขณะนี้"

    # validate response_type
    if response_type not in {"conversational", "diagnosis_explain"}:
        response_type = "conversational"

    logger.info(
        f"[followup] intent={state.get('intent')} "
        f"response_type={response_type} | '{user_message[:60]}'"
    )

    base = {
        "recommendation":   recommendation,
        "response_type":    response_type,
        "first_line_drug":  None,
        "alternatives":     [],
        "when_to_see_doctor": None,
        "augmented_notes":  augmented_notes,
        "pushback_message": None,
        "next_action":      "format",
    }

    if response_type == "diagnosis_explain":
        # คง DDx + sources ไว้เพื่อให้ format_node แสดงได้
        # ไม่ต้อง clear
        logger.info("[followup] diagnosis_explain → keep DDx + sources in state")
        return base

    # conversational — clear clinical state จาก turn ก่อนหน้า
    return {
        **base,
        "differential_diagnosis": [],
        "sources":                [],
        "retrieved_chunks":       [],
    }