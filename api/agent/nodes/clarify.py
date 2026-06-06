"""
agent/nodes/clarify.py
----------------------
Node 2: Clarification loop — FIXED

Problems fixed from original:
  1. "ตอบตู้มเดียว" → completeness_threshold was too easy to pass.
     Now uses history-aware scoring + requires actual symptom details
  2. clarify_question_prompt now gets both missing AND already_have,
     so the bot never asks questions already answered in history
  3. round counting was sometimes reset → preserved from state properly
  4. max_rounds from config (not hardcoded)

Logic:
  score < threshold AND round < max → ask one targeted question
  score >= threshold OR round >= max → proceed to retrieve

Input  : state.user_message, state.history, state.clarify_round, state.intent
Output : state.completeness_score, state.clarify_round,
         state.clarifying_question, state.next_action
"""

from __future__ import annotations

import json

from langchain_google_genai import ChatGoogleGenerativeAI
from loguru import logger

from api.agent.state import AgentState
from api.config import get_settings
from api.prompts.pharmacist import (
    SYSTEM_PROMPT,
    completeness_prompt,
    clarify_question_prompt,
    strip_fences,
)


def clarify_node(state: AgentState) -> dict:
    cfg = get_settings()
    llm = ChatGoogleGenerativeAI(
        model=cfg.gemini_model,
        google_api_key=cfg.gemini_api_key,
        temperature=cfg.llm_temp_clarify,
    )

    current_round = state.get("clarify_round", 0)
    history       = state.get("history", [])
    user_msg      = state["user_message"]

    # ── 1. score completeness ─────────────────────────────────
    score    = cfg.completeness_threshold  # safe default (pass-through)
    missing: list[str] = []
    already_have: list[str] = []

    try:
        score_resp = llm.invoke([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": completeness_prompt(user_msg, history)},
        ])
        raw  = strip_fences(score_resp.content)
        data = json.loads(raw)
        score       = float(data.get("score", cfg.completeness_threshold))
        missing     = data.get("missing", [])
        already_have = data.get("already_have", [])
    except Exception as exc:
        logger.warning(f"[clarify] completeness parse error: {exc} — using default score")

    logger.info(
        f"[clarify] round={current_round}/{cfg.max_clarify_rounds} "
        f"score={score:.2f} threshold={cfg.completeness_threshold} "
        f"missing={missing} | intent={state.get('intent')}"
    )

    # ── 2. decide: ask more or proceed ───────────────────────
    should_ask = (
        score < cfg.completeness_threshold
        and current_round < cfg.max_clarify_rounds
        and len(missing) > 0
    )

    if should_ask:
        try:
            q_resp = llm.invoke([
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": clarify_question_prompt(
                        missing_info=missing,
                        already_have=already_have,
                        round_num=current_round + 1,
                        history=history,
                        max_rounds=cfg.max_clarify_rounds,
                    ),
                },
            ])
            question = q_resp.content.strip()
        except Exception as exc:
            logger.error(f"[clarify] question generation failed: {exc}")
            # graceful degrade — skip clarify, go to retrieve
            return _proceed(score, current_round)

        logger.info(f"[clarify] asking round {current_round + 1}: '{question[:100]}'")
        return {
            "completeness_score":  score,
            "clarify_round":       current_round + 1,
            "clarifying_question": question,
            "next_action":         "clarify",  # graph ends this turn
        }

    # ── enough info (or max rounds hit) ──────────────────────
    return _proceed(score, current_round)


def _proceed(score: float, current_round: int) -> dict:
    logger.info(f"[clarify] score sufficient or max rounds → proceed to retrieve")
    return {
        "completeness_score":  score,
        "clarify_round":       current_round,
        "clarifying_question": None,
        "next_action":         "retrieve",
    }