"""
agent/nodes/classify.py — v5
Changes vs v4:
- ตรวจภาษาจาก user_message (Thai / English) แล้ว set state["user_lang"]
  Logic: นับสัดส่วน Thai unicode character ใน message
         >= 30% Thai chars → "th", otherwise → "en"
  ใช้ simple heuristic ไม่ต้องเรียก LLM เพิ่ม (zero latency / zero cost)
- user_lang ถูก propagate ผ่าน state ไปให้ทุก node ที่ต้องสร้าง response
- ส่วนอื่นคงเดิมทั้งหมด (v4 behaviour unchanged)
"""
from __future__ import annotations

import json
import re

from langchain_google_genai import ChatGoogleGenerativeAI
from loguru import logger

from api.agent.state import AgentState
from api.config import get_settings
from api.prompts.pharmacist import SYSTEM_PROMPT, classify_prompt, strip_fences


# ── Language detection helper ──────────────────────────────────

def _detect_lang(text: str) -> str:
    """
    Return "th" if text is predominantly Thai, else "en".
    Thai Unicode block: U+0E00–U+0E7F
    Threshold: >=30% of non-whitespace chars are Thai → "th"
    """
    stripped = re.sub(r"\s+", "", text)
    if not stripped:
        return "th"  # default fallback
    thai_chars = sum(1 for c in stripped if "\u0e00" <= c <= "\u0e7f")
    ratio = thai_chars / len(stripped)
    lang = "th" if ratio >= 0.30 else "en"
    logger.debug(f"[classify] lang_detect ratio={ratio:.2f} → {lang}")
    return lang


# ── Node ───────────────────────────────────────────────────────

def classify_node(state: AgentState) -> dict:
    cfg = get_settings()
    llm = ChatGoogleGenerativeAI(
        model=cfg.gemini_model,
        google_api_key=cfg.gemini_api_key,
        temperature=cfg.llm_temp_classify,
    )

    history      = state.get("history", [])
    user_message = state["user_message"]

    # ── Language detection (no LLM call needed) ───────────────
    user_lang = _detect_lang(user_message)

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

    logger.info(
        f"[classify] intent={intent} | topic_shift={topic_shift} "
        f"| user_lang={user_lang} | reason={reason}"
    )

    # topic shift → reset clarify state
    base_update: dict = {
        "intent":    intent,
        "user_lang": user_lang,
    }

    if topic_shift:
        base_update["topic_shift"]    = True
        base_update["clarify_round"]  = 0
        base_update["symptom_domain"] = "general"
        base_update["symptom_summary"] = []
        base_update["differential_diagnosis"] = []
    else:
        base_update["topic_shift"] = False

    # routing decision
    cfg2 = get_settings()
    no_clarify: list[str] = cfg2.no_clarify_intents or []

    if intent in ("chit_chat", "off_topic"):
        base_update["next_action"] = "followup"
    elif intent == "unknown" and not history:
        base_update["next_action"] = "followup"
    elif intent == "unknown" and history:
        base_update["next_action"] = "followup"
    elif intent in no_clarify:
        base_update["next_action"] = "retrieve"
    else:
        base_update["next_action"] = "clarify"

    return base_update