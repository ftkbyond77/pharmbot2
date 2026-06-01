"""
agent/nodes/clarify.py
----------------------
Node 2: Clarification loop

Logic:
  - Score completeness of current info (0.0–1.0)
  - If score < threshold AND rounds < max → ask one clarifying question
  - If score >= threshold OR rounds == max   → proceed to retrieve

Input  : state.user_message, state.history, state.clarify_round
Output : state.completeness_score, state.clarify_round,
         state.clarifying_question, state.next_action
"""

import json
from loguru import logger
from langchain_google_genai import ChatGoogleGenerativeAI

from api.agent.state import AgentState
from api.config import get_settings
from api.prompts.pharmacist import (
    SYSTEM_PROMPT,
    completeness_prompt,
    clarify_question_prompt,
)


def clarify_node(state: AgentState) -> dict:
    cfg = get_settings()
    llm = ChatGoogleGenerativeAI(
        model=cfg.gemini_model,
        google_api_key=cfg.gemini_api_key,
        temperature=0.3,
    )

    current_round = state.get("clarify_round", 0)

    # ── 1. score completeness ─────────────────────────────────
    score_prompt = completeness_prompt(state["user_message"], state.get("history", []))
    score_resp = llm.invoke([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": score_prompt},
    ])

    score = cfg.completeness_threshold  # safe default
    missing: list[str] = []
    try:
        raw = _strip_fences(score_resp.content)
        data = json.loads(raw)
        score = float(data.get("score", cfg.completeness_threshold))
        missing = data.get("missing", [])
    except Exception as e:
        logger.warning(f"clarify_node score parse error: {e}")

    logger.info(
        f"[clarify] round={current_round} score={score:.2f} "
        f"threshold={cfg.completeness_threshold} missing={missing}"
    )

    # ── 2. decide: ask more or proceed ───────────────────────
    should_ask = (
        score < cfg.completeness_threshold
        and current_round < cfg.max_clarify_rounds
        and missing
    )

    if should_ask:
        q_resp = llm.invoke([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": clarify_question_prompt(missing, current_round + 1)},
        ])
        question = q_resp.content.strip()
        logger.info(f"[clarify] asking round {current_round + 1}: {question[:80]}")

        return {
            "completeness_score": score,
            "clarify_round": current_round + 1,
            "clarifying_question": question,
            "next_action": "clarify",          # graph loops back to user
        }

    # enough info (or max rounds hit) → move forward
    return {
        "completeness_score": score,
        "clarify_round": current_round,
        "clarifying_question": None,
        "next_action": "retrieve",
    }


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return text.strip()