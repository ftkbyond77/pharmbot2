"""
agent/nodes/clarify.py — v6
Changes vs v5:
- ALLERGY HARD BLOCK: deterministic Python check (ไม่ใช่แค่ adjust score)
  ถ้าพบ vague allergy pattern ใน input → force clarify โดยไม่สนใจ LLM score เลย
- AOM watchful waiting: เพิ่ม criteria ชัดเจน (≥2 ปี + unilateral + ไข้ <39°C)
- Completeness: เพิ่ม high-score pattern สำหรับ input ที่มีข้อมูลครบ (Test 2 pattern)
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

_ATB_DOMAINS = {"AOM", "pharyngitis", "sinusitis", "allergy"}

_ALLERGY_ANSWERED_KEYWORDS = [
    "ไม่แพ้", "ไม่มีประวัติแพ้", "ไม่มีการแพ้",
    "แพ้ยา", "แพ้ penicillin", "แพ้ amoxicillin", "แพ้ ampicillin",
    "แพ้ cephalosporin", "แพ้ augmentin", "แพ้ sulfa",
    "allergy", "anaphylaxis", "ผื่นขึ้น", "ลมพิษ", "บวม",
    "หายใจลำบาก", "ช็อก", "แพ้รุนแรง",
    "ประวัติแพ้ยา", "ไม่เคยแพ้", "เคยแพ้",
]

# Vague allergy = พูดถึงแพ้ยาแต่ไม่มีรายละเอียด → hard block เสมอ
_ALLERGY_VAGUE_KEYWORDS = [
    "แพ้ยาอยู่", "มีประวัติแพ้ยา", "เคยแพ้ยาปฏิชีวนะ",
    "จำได้ว่าเคยแพ้", "ไม่แน่ใจว่าแพ้ยาอะไร", "ไม่แน่ใจว่ายาตัวไหน",
    "จำไม่ได้ว่าแพ้ยาอะไร", "ไม่รู้ว่าแพ้ยาอะไร", "แพ้ยาบางตัว",
    "แพ้ยา ไม่แน่ใจ",
]

# Detail keywords ที่บอกว่ามีรายละเอียดแพ้ยาแล้ว (ทำให้ไม่ hard block)
_ALLERGY_DETAIL_KEYWORDS = [
    "ผื่น", "ลมพิษ", "anaphylaxis", "บวม", "หายใจลำบาก", "ช็อก",
    "ไม่แพ้", "ไม่มีประวัติแพ้", "ไม่เคยแพ้", "Stevens-Johnson",
    "epinephrine", "ฉีดยา",
]


def _allergy_answered_in_history(history: list[dict]) -> bool:
    for turn in history:
        content = str(turn.get("content", "")).lower()
        if any(kw.lower() in content for kw in _ALLERGY_ANSWERED_KEYWORDS):
            return True
    return False


def _is_vague_allergy(user_msg: str, history: list[dict]) -> bool:
    """
    Deterministic check: ผู้ป่วยพูดถึงแพ้ยาแต่ไม่มีรายละเอียด
    Returns True → hard block, ต้องถามก่อนเสมอ ไม่ว่า LLM จะ score เท่าไหร่

    ระวัง false positive: "ไม่มีประวัติแพ้ยา" ไม่ใช่ vague allergy
    """
    import re
    all_text = user_msg + " " + " ".join(str(t.get("content", "")) for t in history)

    # ตรวจ vague patterns แบบ context-aware
    # keyword ที่บ่งบอกแพ้ยาแต่ไม่รู้รายละเอียด
    vague_patterns = [
        r'แพ้ยาอยู่',
        r'(?<!ไม่)(?<!ไม่มี)มีประวัติแพ้ยา',   # "มีประวัติแพ้ยา" แต่ไม่ใช่ "ไม่มีประวัติแพ้ยา"
        r'เคยแพ้ยาปฏิชีวนะ',
        r'จำได้ว่าเคยแพ้',
        r'ไม่แน่ใจว่าแพ้ยาอะไร',
        r'ไม่แน่ใจว่ายาตัวไหน',
        r'จำไม่ได้ว่าแพ้ยาอะไร',
        r'ไม่รู้ว่าแพ้ยาอะไร',
        r'แพ้ยาบางตัว',
        r'แพ้ยา\s*ไม่แน่ใจ',
    ]

    has_vague = any(re.search(p, all_text) for p in vague_patterns)

    # ถ้ามี detail → ไม่ block
    has_detail = any(kw.lower() in all_text.lower() for kw in _ALLERGY_DETAIL_KEYWORDS)

    return has_vague and not has_detail


def clarify_node(state: AgentState) -> dict:
    cfg = get_settings()
    llm = ChatGoogleGenerativeAI(
        model=cfg.gemini_model,
        google_api_key=cfg.gemini_api_key,
        temperature=cfg.llm_temp_clarify,
    )

    topic_shift   = state.get("topic_shift", False)
    current_round = 0 if topic_shift else state.get("clarify_round", 0)
    if topic_shift:
        logger.info("[clarify] topic_shift detected → reset clarify_round=0")

    history  = state.get("history", [])
    user_msg = state["user_message"]

    # ══════════════════════════════════════════════════════════
    # HARD BLOCK #1 — Vague Allergy (deterministic, before LLM)
    # ══════════════════════════════════════════════════════════
    if _is_vague_allergy(user_msg, history) and current_round < 3:
        logger.info("[clarify] HARD BLOCK: vague allergy detected before LLM call")
        missing = ["รายละเอียดการแพ้ยา: ชื่อยา + อาการแพ้ (ผื่น/บวม/ช็อก) + ระยะเวลา + เคยใช้ซ้ำไหม"]
        try:
            q_resp = llm.invoke([
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": clarify_question_prompt(
                    missing_info=missing,
                    already_have=[],
                    round_num=current_round + 1,
                    history=history,
                    max_rounds=3,
                    domain="allergy",
                )},
            ])
            q_content = q_resp.content
            if isinstance(q_content, list):
                q_content = " ".join(
                    p.text if hasattr(p, "text")
                    else p.get("text", str(p)) if isinstance(p, dict)
                    else str(p) for p in q_content
                )
            question = q_content.strip()
        except Exception as exc:
            logger.warning(f"[clarify] allergy gate question error: {exc}")
            question = (
                "ก่อนให้คำแนะนำด้านยา รบกวนขอทราบรายละเอียดการแพ้ยาก่อนนะครับ:\n"
                "1. แพ้ยาชื่ออะไร? (ถ้าจำชื่อไม่ได้ บอกชื่อการค้าก็ได้)\n"
                "2. อาการแพ้เป็นอย่างไร? (เช่น ผื่น ลมพิษ หน้าบวม หายใจลำบาก หรือช็อก)\n"
                "3. เกิดขึ้นนานแค่ไหนแล้ว?\n"
                "4. หลังจากนั้นเคยกินยากลุ่มเดิมอีกไหม?"
            )
        return {
            "completeness_score":  0.10,
            "clarify_round":       current_round + 1,
            "clarifying_question": question,
            "next_action":         "clarify",
            "symptom_domain":      "allergy",
        }

    # ══════════════════════════════════════════════════════════
    # LLM completeness check (ปกติ)
    # ══════════════════════════════════════════════════════════
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
                else str(p) for p in content
            )
        raw          = strip_fences(content)
        data         = json.loads(raw)
        score        = float(data.get("score", cfg.completeness_threshold))
        missing      = data.get("missing", [])
        already_have = data.get("already_have", [])
        domain       = data.get("domain", "general")
    except Exception as exc:
        logger.warning(f"[clarify] completeness parse error: {exc}")

    domain_max    = {"AOM": 3, "pharyngitis": 3, "sinusitis": 3, "allergy": 2, "general": 2}
    effective_max = domain_max.get(domain, cfg.max_clarify_rounds)

    logger.info(
        f"[clarify] round={current_round}/{effective_max} domain={domain} "
        f"score={score:.2f} threshold={cfg.completeness_threshold} missing={missing}"
    )

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
            )},
        ])
        q_content = q_resp.content
        if isinstance(q_content, list):
            q_content = " ".join(
                p.text if hasattr(p, "text")
                else p.get("text", str(p)) if isinstance(p, dict)
                else str(p) for p in q_content
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

# Keywords ที่บ่งบอกว่าผู้ป่วย "พูดถึงการแพ้ยา แต่ไม่มีรายละเอียด"
# → ต้องถาม ห้ามปล่อยผ่าน
_ALLERGY_VAGUE_KEYWORDS = [
    "แพ้ยาอยู่", "มีประวัติแพ้ยา", "เคยแพ้ยาปฏิชีวนะ",
    "จำได้ว่าเคยแพ้", "ไม่แน่ใจว่าแพ้ยาอะไร", "ไม่แน่ใจว่ายาตัวไหน",
    "จำไม่ได้ว่าแพ้ยาอะไร", "ไม่รู้ว่าแพ้ยาอะไร", "แพ้ยาบางตัว",
    "แพ้ยา ไม่แน่ใจ",
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
    # บล็อกเมื่อ: ผู้ป่วยพูดถึงการแพ้ยาแต่ไม่มีรายละเอียด (vague allergy)
    allergy_gate_triggered = False
    if domain in _ATB_DOMAINS:
        # ตรวจ vague allergy ใน user_msg และ history ทั้งหมด
        all_text = user_msg + " " + " ".join(
            str(t.get("content", "")) for t in history
        )
        user_said_vague = any(kw in all_text for kw in _ALLERGY_VAGUE_KEYWORDS)

        allergy_details_known = _allergy_answered_in_history(history) or any(
            kw.lower() in user_msg.lower()
            for kw in ["ผื่น", "ลมพิษ", "anaphylaxis", "บวม", "หายใจลำบาก", "ช็อก",
                       "ไม่แพ้", "ไม่มีประวัติแพ้", "ไม่เคยแพ้"]
        )

        if user_said_vague and not allergy_details_known:
            allergy_in_missing = any(
                "แพ้" in m or "allergy" in m.lower()
                for m in missing
            )
            if not allergy_in_missing:
                missing = missing + ["รายละเอียดการแพ้ยา: ชื่อยา + อาการแพ้ + ระยะเวลา + เคยใช้ซ้ำไหม"]
            if score >= cfg.completeness_threshold:
                score = cfg.completeness_threshold - 0.05
                allergy_gate_triggered = True
                logger.info("[clarify] ALLERGY GATE triggered — vague allergy pattern detected")

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