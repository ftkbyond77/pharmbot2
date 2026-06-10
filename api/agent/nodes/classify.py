"""
agent/nodes/classify.py — v4
Changes vs v3:
- เพิ่ม valid intents: chit_chat, off_topic
- chit_chat + off_topic → followup_node (ตอบ conversational ไม่ผ่าน pipeline)
- unknown ที่ไม่มี history → format_node โดยตรง (แสดง welcome message ไม่ crash)
- unknown ที่มี history → followup_node (อาจเป็น context ที่ LLM อ่านไม่ออก)
"""
from __future__ import annotations

import json

from langchain_google_genai import ChatGoogleGenerativeAI
from loguru import logger

from api.agent.state import AgentState
from api.config import get_settings
from api.prompts.pharmacist import SYSTEM_PROMPT, classify_prompt, strip_fences


def classify_node(state: AgentState) -> dict:
    cfg = get_settings()
    llm = ChatGoogleGenerativeAI(
        model=cfg.gemini_model,
        google_api_key=cfg.gemini_api_key,
        temperature=cfg.llm_temp_classify,
    )

    history      = state.get("history", [])
    user_message = state["user_message"]

    prompt   = classify_prompt(user_message=user_message, history=history)
    response = llm.invoke([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": prompt},
    ])

    intent      = "unknown"
    reason      = ""
    topic_shift = False

    try:
        raw  = strip_fences(response.content)
        data = json.loads(raw)
        intent      = data.get("intent", "unknown")
        reason      = data.get("reason", "")
        topic_shift = bool(data.get("topic_shift", False))
    except Exception as exc:
        logger.warning(f"[classify] JSON parse error: {exc} | raw: {response.content[:200]}")

    # validate
    valid_intents = {
        "symptom", "drug_info", "followup",
        "chit_chat", "off_topic", "unknown",
        "general_pharma",   # backward compat
    }
    if intent not in valid_intents:
        logger.warning(f"[classify] invalid intent '{intent}' → fallback 'unknown'")
        intent = "unknown"

    logger.info(f"[classify] intent={intent} | topic_shift={topic_shift} | reason={reason}")

    # topic shift → reset round
    clarify_round = 0 if topic_shift else state.get("clarify_round", 0)

    # ── Routing ───────────────────────────────────────────────
    # followup / chit_chat / off_topic → followup_node (conversational, fast)
    # symptom → clarify pipeline
    # drug_info / general_pharma → retrieve (no clarify)
    # unknown:
    #   - มี history → followup_node (bot จะ acknowledge + redirect)
    #   - ไม่มี history → followup_node (welcome + invite to ask)

    conversational_intents = {"followup", "chit_chat", "off_topic", "unknown"}

    if intent in conversational_intents:
        next_action = "followup"
        logger.info(f"[classify] {intent} → followup_node (conversational)")
    elif intent in {"drug_info", "general_pharma"}:
        next_action = "retrieve"
    else:
        # symptom → clarify
        next_action = "clarify"

    return {
        "intent":        intent,
        "next_action":   next_action,
        "clarify_round": clarify_round,
        "topic_shift":   topic_shift,
    }