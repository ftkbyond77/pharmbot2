"""
agent/nodes/followup.py — v3
Changes vs v2:
- Pass user_lang จาก state เข้า followup_prompt
  → ตอบภาษาเดียวกับ user (EN/TH)
- ส่วนอื่นคงเดิมทั้งหมดจาก v2
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
    user_lang    = state.get("user_lang", "th")  # v3: ดึงภาษาจาก state

    prompt   = followup_prompt(
        user_message=user_message,
        history=history,
        user_lang=user_lang,   # v3: pass language
    )
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
        recommendation = (
            "Sorry, I was unable to process your request at this time."
            if user_lang == "en"
            else "ขออภัยครับ ไม่สามารถประมวลผลได้ในขณะนี้"
        )

    # validate response_type
    valid_types = {"conversational", "diagnosis_explain"}
    if response_type not in valid_types:
        response_type = "conversational"

    logger.info(f"[followup] response_type={response_type} lang={user_lang}")

    # ถ้า diagnosis_explain → คง DDx + sources ไว้ใน state
    if response_type == "diagnosis_explain":
        return {
            "recommendation":  recommendation,
            "response_type":   response_type,
            "augmented_notes": augmented_notes,
        }

    # conversational → clear DDx + retrieved_chunks
    return {
        "recommendation":         recommendation,
        "response_type":          response_type,
        "augmented_notes":        augmented_notes,
        "differential_diagnosis": [],
        "retrieved_chunks":       [],
    }