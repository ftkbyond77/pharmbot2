"""
agent/nodes/clarify.py — v8
Base: v7

Changes v8:
─────────────────────────────────────────────────────────────
L1 — pass user_lang จาก state เข้า clarify_question_prompt
     → คำถามตอบภาษาเดียวกับ user เสมอ

T1 — เพิ่ม max_tokens=512 ใน LLM
     → ป้องกัน truncation ใน clarify question

M1 — HARD ESCAPE เมื่อถึง effective_max
     → ถ้า current_round >= effective_max → force retrieve ทันที
       ไม่ว่า score จะเป็นเท่าไหร่ ไม่ถามซ้ำ

M2 — fallback question ภาษา-aware
     → ถ้า LLM generate question ล้มเหลว → fallback ตาม user_lang
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
        max_tokens=512,         # v8: ป้องกัน truncation
    )

    topic_shift   = state.get("topic_shift", False)
    current_round = 0 if topic_shift else state.get("clarify_round", 0)
    if topic_shift:
        logger.info("[clarify] topic_shift detected → reset clarify_round=0")

    history   = state.get("history", [])
    user_msg  = state["user_message"]
    user_lang = state.get("user_lang", "th")   # v8: ดึงภาษาจาก state

    # ── LLM completeness check ────────────────────────────────
    score        = cfg.completeness_threshold
    missing:      list[str] = []
    already_have: list[str] = []
    domain       = "general"

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

    # domain-based max rounds
    domain_max    = {"AOM": 3, "pharyngitis": 3, "sinusitis": 3, "allergy": 2, "general": 2}
    effective_max = domain_max.get(domain, cfg.max_clarify_rounds)

    # log allergy gate for observability
    allergy_gate_triggered = (domain == "allergy" and score < cfg.completeness_threshold)

    logger.info(
        f"[clarify] round={current_round}/{effective_max} domain={domain} "
        f"score={score:.2f} threshold={cfg.completeness_threshold} "
        f"missing={missing} allergy_gate={allergy_gate_triggered} "
        f"user_lang={user_lang}"
    )

    # ── Decision ──────────────────────────────────────────────
    # v8: HARD ESCAPE — ถ้าถามครบ effective_max แล้ว → retrieve เสมอ ไม่ถามซ้ำ
    hard_escape = current_round >= effective_max

    should_ask = (
        not hard_escape
        and score < cfg.completeness_threshold
        and len(missing) > 0
    )

    if not should_ask:
        if hard_escape:
            logger.info(f"[clarify] hard escape at round={current_round} → force retrieve")
        else:
            logger.info("[clarify] sufficient info → proceed to retrieve")
        return {
            "completeness_score":  score,
            "clarify_round":       current_round,
            "clarifying_question": None,
            "next_action":         "retrieve",
            "symptom_domain":      domain,
        }

    # ── Generate question ─────────────────────────────────────
    try:
        q_resp = llm.invoke([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": clarify_question_prompt(
                missing_info=missing,
                already_have=already_have,
                round_num=current_round + 1,
                history=history,
                max_rounds=effective_max,
                domain=domain,
                user_lang=user_lang,      # v8: pass language
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
        # v8: fallback ภาษา-aware
        question = (
            "Could you tell me more about your symptoms?"
            if user_lang == "en"
            else "ช่วยเล่าอาการเพิ่มเติมหน่อยได้ไหมครับ?"
        )

    logger.info(f"[clarify] asking round {current_round+1}: '{question[:80]}'")
    return {
        "completeness_score":  score,
        "clarify_round":       current_round + 1,
        "clarifying_question": question,
        "next_action":         "clarify",
        "symptom_domain":      domain,
    }