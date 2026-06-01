"""
agent/nodes/classify.py
-----------------------
Node 1: Intent classification

Input  : state.user_message
Output : state.intent, state.next_action
"""

import json
from loguru import logger
from langchain_google_genai import ChatGoogleGenerativeAI

from api.agent.state import AgentState
from api.config import get_settings
from api.prompts.pharmacist import SYSTEM_PROMPT, classify_prompt


def classify_node(state: AgentState) -> dict:
    cfg = get_settings()
    llm = ChatGoogleGenerativeAI(
        model=cfg.gemini_model,
        google_api_key=cfg.gemini_api_key,
        temperature=0,
    )

    prompt = classify_prompt(state["user_message"])
    response = llm.invoke([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": prompt},
    ])

    try:
        raw = response.content.strip()
        # strip markdown fences if model adds them
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        intent = data.get("intent", "unknown")
    except Exception as e:
        logger.warning(f"classify_node parse error: {e} — defaulting to 'unknown'")
        intent = "unknown"

    logger.info(f"[classify] intent={intent}")

    # route: unknown → still go to clarify so bot can ask what's wrong
    next_action = "clarify" if intent in ("symptom", "general_pharma", "unknown") else "retrieve"

    return {
        "intent": intent,
        "next_action": next_action,
        "clarify_round": 0,
    }