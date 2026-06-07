"""
agent/nodes/clarify.py — v4
Extra fix: ถ้า score ≥ threshold และ missing ว่าง → ไม่ถาม เสมอ
"""
from __future__ import annotations
import json
from langchain_google_genai import ChatGoogleGenerativeAI
from loguru import logger
from api.agent.state import AgentState
from api.config import get_settings
from api.prompts.pharmacist import (
    SYSTEM_PROMPT, completeness_prompt, clarify_question_prompt, strip_fences,
)

# Domains that rarely need clarification when data looks complete
_FAST_DOMAINS = {"general"}

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

    score        = cfg.completeness_threshold
    missing:      list[str] = []
    already_have: list[str] = []
    domain = "general"

    try:
        resp    = llm.invoke([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": completeness_prompt(user_msg, history)},
        ])
        content = resp.content
        if isinstance(content, list):
            content = " ".join(
                p.text if hasattr(p, "text")
                else p.get("text", str(p)) if isinstance(p, dict)
                else str(p)
                for p in content
            )
        raw          = strip_fences(content)
        data         = json.loads(raw)
        score        = float(data.get("score", cfg.completeness_threshold))
        missing      = data.get("missing", [])
        already_have = data.get("already_have", [])
        domain       = data.get("domain", "general")
    except Exception as exc:
        logger.warning(f"[clarify] completeness parse error: {exc}")

    # Domain-specific max rounds
    domain_max = {"AOM": 3, "pharyngitis": 3, "sinusitis": 2, "allergy": 2, "general": 2}
    effective_max = domain_max.get(domain, cfg.max_clarify_rounds)

    logger.info(
        f"[clarify] round={current_round}/{effective_max} domain={domain} "
        f"score={score:.2f} threshold={cfg.completeness_threshold} missing={missing}"
    )

    # ── Decision ──────────────────────────────────────────────
    # Hard conditions to NOT ask:
    # 1. score >= threshold
    # 2. no missing fields
    # 3. already hit max rounds
    should_ask = (
        score < cfg.completeness_threshold
        and len(missing) > 0
        and current_round < effective_max
    )

    if not should_ask:
        logger.info("[clarify] sufficient info → proceed to retrieve")
        return {
            "completeness_score":  score,
            "clarify_round":       current_round,
            "clarifying_question": None,
            "next_action":         "retrieve",
            "symptom_domain":      domain,
        }

    # ── Ask ───────────────────────────────────────────────────
    try:
        q_resp   = llm.invoke([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": clarify_question_prompt(
                missing_info=missing,
                already_have=already_have,
                round_num=current_round + 1,
                history=history,
                max_rounds=effective_max,
                domain=domain,
            )},
        ])
        q_content = q_resp.content
        if isinstance(q_content, list):
            q_content = " ".join(
                p.text if hasattr(p, "text")
                else p.get("text", str(p)) if isinstance(p, dict)
                else str(p)
                for p in q_content
            )
        question = q_content.strip()
    except Exception as exc:
        logger.warning(f"[clarify] question generation error: {exc}")
        question = "ช่วยเล่าอาการเพิ่มเติมหน่อยได้ไหมครับ?"

    logger.info(f"[clarify] asking round {current_round+1}: '{question[:80]}'")
    return {
        "completeness_score":  score,
        "clarify_round":       current_round + 1,
        "clarifying_question": question,
        "next_action":         "clarify",
        "symptom_domain":      domain,
    }