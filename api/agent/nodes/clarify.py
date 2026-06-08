"""
agent/nodes/clarify.py — v5
Changes vs v4:
- ALLERGY GATE: ถ้า domain ต้องการ ATB (AOM/pharyngitis/sinusitis/allergy)
  และยังไม่มีข้อมูลประวัติแพ้ยาในประวัติสนทนา → บังคับถาม (override score threshold)
- topic_shift: ถ้า classify บอก topic_shift=True → reset clarify_round เป็น 0
- ปรับ effective_max: pharyngitis เพิ่มเป็น 3 รอบ (รับ allergy gate)
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

# Domains ที่ต้องการ ATB และจึงต้องผ่าน allergy gate
_ATB_DOMAINS = {"AOM", "pharyngitis", "sinusitis", "allergy"}

# Keywords ที่บ่งบอกว่ามีข้อมูลแพ้ยาแล้วในประวัติ (ครอบคลุม Core Question C3–C5)
_ALLERGY_ANSWERED_KEYWORDS = [
    # คำตอบว่าไม่แพ้
    "ไม่แพ้", "ไม่มีประวัติแพ้", "ไม่มีการแพ้",
    # คำตอบว่าแพ้ (พร้อมชื่อยา)
    "แพ้ยา", "แพ้ penicillin", "แพ้ amoxicillin", "แพ้ ampicillin",
    "แพ้ cephalosporin", "แพ้ augmentin", "แพ้ sulfa",
    # อาการแพ้
    "allergy", "anaphylaxis", "ผื่นขึ้น", "ลมพิษ", "บวม",
    "หายใจลำบาก", "ช็อก", "แพ้รุนแรง",
    # บริบทที่บอกว่าถามแล้ว
    "ประวัติแพ้ยา", "ไม่เคยแพ้", "เคยแพ้",
]


def _allergy_answered_in_history(history: list[dict]) -> bool:
    """ตรวจสอบว่าประวัติการสนทนามีคำตอบเรื่องแพ้ยาแล้วหรือยัง"""
    for turn in history:
        content = str(turn.get("content", "")).lower()
        if any(kw.lower() in content for kw in _ALLERGY_ANSWERED_KEYWORDS):
            return True
    return False


def clarify_node(state: AgentState) -> dict:
    cfg = get_settings()
    llm = ChatGoogleGenerativeAI(
        model=cfg.gemini_model,
        google_api_key=cfg.gemini_api_key,
        temperature=cfg.llm_temp_clarify,
    )

    # ── Topic shift: reset round ──────────────────────────────
    topic_shift = state.get("topic_shift", False)
    current_round = 0 if topic_shift else state.get("clarify_round", 0)
    if topic_shift:
        logger.info("[clarify] topic_shift detected → reset clarify_round=0")

    history  = state.get("history", [])
    user_msg = state["user_message"]

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
    domain_max = {"AOM": 3, "pharyngitis": 3, "sinusitis": 3, "allergy": 2, "general": 2}
    effective_max = domain_max.get(domain, cfg.max_clarify_rounds)

    # ── ALLERGY GATE (เฉพาะกรณีที่ต้องบล็อกจริง) ─────────────
    # บล็อกเฉพาะเมื่อ: ผู้ป่วยบอก "แพ้ยาอยู่" แต่ไม่มีรายละเอียด
    # ไม่บล็อกเมื่อ: ผู้ป่วยไม่ได้พูดถึงแพ้ยาเลย (completeness_prompt จะ score สูงเองแล้ว)
    allergy_gate_triggered = False
    if domain in _ATB_DOMAINS:
        user_said_allergic = any(
            kw in user_msg.lower()
            for kw in ["แพ้ยาอยู่", "แพ้ยา", "เคยแพ้", "allergy", "แพ้"]
        )
        allergy_details_known = _allergy_answered_in_history(history) or any(
            kw.lower() in user_msg.lower()
            for kw in ["ผื่น", "ลมพิษ", "anaphylaxis", "บวม", "หายใจลำบาก", "ช็อก",
                       "ไม่แพ้", "ไม่มีประวัติแพ้", "ไม่เคยแพ้"]
        )
        # บล็อกเฉพาะเมื่อผู้ป่วยพูดถึงแพ้ยาแต่ไม่มีรายละเอียด
        if user_said_allergic and not allergy_details_known:
            allergy_in_missing = any(
                "แพ้" in m or "allergy" in m.lower()
                for m in missing
            )
            if not allergy_in_missing:
                missing = missing + ["รายละเอียดการแพ้ยา (ชื่อยา + อาการที่เกิดขึ้น)"]
            if score >= cfg.completeness_threshold:
                score = cfg.completeness_threshold - 0.05
                allergy_gate_triggered = True
                logger.info("[clarify] ALLERGY GATE triggered — patient mentioned allergy without details")

    logger.info(
        f"[clarify] round={current_round}/{effective_max} domain={domain} "
        f"score={score:.2f} threshold={cfg.completeness_threshold} "
        f"missing={missing} allergy_gate={allergy_gate_triggered}"
    )

    # ── Decision ──────────────────────────────────────────────
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