"""
agent/nodes/safety_gate.py  (v2)
---------------------------------
Changes v2:
- ใช้ refer_explanation จาก prompt เพื่อสร้างข้อความที่อธิบายเหตุผลก่อนแนะนำ ER
  แทนที่จะบอกแค่ "พบสัญญาณอันตราย กรุณาพบแพทย์"
"""

import json
from loguru import logger
from langchain_google_genai import ChatGoogleGenerativeAI

from api.agent.state import AgentState
from api.config import get_settings
from api.prompts.pharmacist import SYSTEM_PROMPT, safety_gate_prompt, strip_fences


def safety_gate_node(state: AgentState) -> dict:
    # fast path: clinical_reason already detected red flags
    pre_detected = state.get("red_flags_found", [])

    if pre_detected:
        logger.warning(f"[safety_gate] pre-detected red flags: {pre_detected}")
        reason = _build_refer_message(
            flags=pre_detected,
            explanation=None,
            symptom_summary=" | ".join(state.get("symptom_summary", [])),
        )
        return _refer(red_flags=pre_detected, reason=reason)

    # secondary LLM check for edge cases
    cfg = get_settings()
    llm = ChatGoogleGenerativeAI(
        model=cfg.gemini_model,
        google_api_key=cfg.gemini_api_key,
        temperature=0,
    )

    symptom_text = " | ".join(state.get("symptom_summary", [state["user_message"]]))
    ddx_text = ", ".join(
        d["name"] for d in state.get("differential_diagnosis", [])
    ) or "ยังไม่ระบุ"

    prompt = safety_gate_prompt(symptom_text, ddx_text, user_lang=state.get("user_lang", "th"))
    response = llm.invoke([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": prompt},
    ])

    has_flag    = False
    flags_found = []
    explanation = None
    reason      = None

    try:
        raw  = strip_fences(response.content)
        data = json.loads(raw)
        has_flag    = bool(data.get("has_red_flag", False))
        flags_found = data.get("red_flags_found", [])
        explanation = data.get("refer_explanation")
        reason      = data.get("refer_reason")
    except Exception as e:
        logger.warning(f"safety_gate_node parse error: {e} — defaulting to safe")

    logger.info(f"[safety_gate] has_flag={has_flag} flags={flags_found}")

    if has_flag:
        # ถ้า LLM ให้ refer_reason ที่ดีมาแล้ว ใช้เลย ไม่งั้นสร้างจาก explanation
        if not reason or len(reason) < 20:
            reason = _build_refer_message(flags_found, explanation, symptom_text)
        return _refer(red_flags=flags_found, reason=reason)

    return {
        "refer_to_doctor":   False,
        "refer_reason":      None,
        "red_flags_found":   [],
        "next_action":       "recommend",
    }


def _build_refer_message(flags: list[str], explanation: str | None, symptom_summary: str) -> str:
    """สร้างข้อความ refer ที่อธิบายเหตุผลก่อนแนะนำ ER"""
    flag_name = flags[0] if flags else "อาการที่พบ"

    if explanation:
        return f"{explanation}\n\nกรุณาไปห้องฉุกเฉินหรือพบแพทย์ทันทีครับ อย่าชะลอเวลา"

    # fallback ตาม flag type
    flag_lower = flag_name.lower()
    if "epiglottitis" in flag_lower or "muffled" in flag_lower:
        return (
            "อาการเสียงเปลี่ยน ร่วมกับน้ำลายไหลและกลืนลำบากมาก อาจบ่งชี้ภาวะกล่องเสียงบวมเฉียบพลัน "
            "(Epiglottitis) ซึ่งทางเดินหายใจอาจอุดตันได้รวดเร็วมาก\n\n"
            "กรุณาไปห้องฉุกเฉินทันทีครับ ไม่ควรชะลอเวลา"
        )
    elif "stridor" in flag_lower or "drooling" in flag_lower:
        return (
            "อาการหายใจมีเสียงดังและน้ำลายไหลในเด็กเป็นสัญญาณเตือนว่าทางเดินหายใจอาจถูกกีดขวาง "
            "ซึ่งอันตรายถึงชีวิตได้\n\n"
            "กรุณาพาไปห้องฉุกเฉินทันทีครับ"
        )
    elif "peritonsillar" in flag_lower or "abscess" in flag_lower:
        return (
            "อาการเจ็บคอรุนแรงมากข้างเดียว ร่วมกับอ้าปากได้น้อย อาจบ่งชี้ฝีรอบทอนซิล "
            "(Peritonsillar Abscess) ซึ่งต้องรับการรักษาโดยแพทย์ด่วน\n\n"
            "กรุณาไปพบแพทย์ที่ห้องฉุกเฉินโดยเร็วครับ"
        )
    elif "meningitis" in flag_lower:
        return (
            "ไข้สูงร่วมกับคอแข็งและระดับความรู้สึกตัวเปลี่ยน อาจบ่งชี้เยื่อหุ้มสมองอักเสบ "
            "ซึ่งเป็นภาวะฉุกเฉินที่ต้องรักษาเร่งด่วน\n\n"
            "กรุณาไปห้องฉุกเฉินทันทีครับ"
        )
    else:
        return (
            f"พบสัญญาณที่น่าเป็นห่วง: {flag_name} "
            "ซึ่งอาจเป็นภาวะที่ต้องได้รับการรักษาโดยแพทย์เร่งด่วน\n\n"
            "กรุณาไปพบแพทย์หรือห้องฉุกเฉินโดยเร็วครับ"
        )


def _refer(red_flags: list[str], reason: str | None) -> dict:
    return {
        "refer_to_doctor":  True,
        "refer_reason":     reason or "กรุณาพบแพทย์เพื่อรับการวินิจฉัยที่ถูกต้อง",
        "red_flags_found":  red_flags,
        "next_action":      "refer",
    }