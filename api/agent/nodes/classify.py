"""
agent/nodes/classify.py — v2
Changes vs v1:
- Extracts topic_shift from LLM response
- Passes topic_shift into state (used by clarify_node to reset round counter)
- topic_shift=True → reset context เพื่อไม่ให้ context เก่าปะปน
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

    prompt = classify_prompt(
        user_message=state["user_message"],
        history=state.get("history", []),
    )
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

    # validate — catch hallucinated values
    valid_intents = {"symptom", "drug_info", "general_pharma", "unknown"}
    if intent not in valid_intents:
        logger.warning(f"[classify] invalid intent '{intent}' → fallback 'unknown'")
        intent = "unknown"

    logger.info(f"[classify] intent={intent} | topic_shift={topic_shift} | reason={reason}")

    # configurable: which intents skip clarify and go straight to retrieve
    no_clarify = set(cfg.no_clarify_intents)
    if intent in no_clarify:
        next_action = "retrieve"
        logger.debug(f"[classify] intent '{intent}' in no_clarify_intents → retrieve")
    else:
        next_action = "clarify"
        logger.debug(f"[classify] intent '{intent}' → clarify")

    # ── Topic shift: reset clarify_round ─────────────────────
    # clarify_node จะอ่าน topic_shift จาก state และ reset round เอง
    clarify_round = 0 if topic_shift else state.get("clarify_round", 0)

    return {
        "intent":        intent,
        "next_action":   next_action,
        "clarify_round": clarify_round,
        "topic_shift":   topic_shift,
    }