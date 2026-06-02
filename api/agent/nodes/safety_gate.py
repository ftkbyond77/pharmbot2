"""
agent/nodes/safety_gate.py
--------------------------
Node 5: Safety gate

If any red flag confirmed → set refer_to_doctor=True and terminate.
Otherwise → proceed to recommendation.

Input  : state.symptom_summary, state.differential_diagnosis,
         state.red_flags_found  (pre-filled by clinical_reason)
Output : state.refer_to_doctor, state.refer_reason, state.next_action
"""

import json
from loguru import logger
from langchain_google_genai import ChatGoogleGenerativeAI

from api.agent.state import AgentState
from api.config import get_settings
from api.prompts.pharmacist import SYSTEM_PROMPT, safety_gate_prompt


def safety_gate_node(state: AgentState) -> dict:
    # fast path: clinical_reason already detected red flags
    pre_detected = state.get("red_flags_found", [])

    if pre_detected:
        logger.warning(f"[safety_gate] pre-detected red flags: {pre_detected}")
        return _refer(
            red_flags=pre_detected,
            reason="พบสัญญาณอันตราย กรุณาพบแพทย์หรือไปห้องฉุกเฉินทันที",
        )

    # secondary check via LLM for edge cases clinical_reason missed
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

    prompt = safety_gate_prompt(symptom_text, ddx_text)
    response = llm.invoke([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": prompt},
    ])

    try:
        raw = strip_fences(response.content)
        data = json.loads(raw)
        has_flag    = bool(data.get("has_red_flag", False))
        flags_found = data.get("red_flags_found", [])
        reason      = data.get("refer_reason")
    except Exception as e:
        logger.warning(f"safety_gate_node parse error: {e} — defaulting to safe")
        has_flag, flags_found, reason = False, [], None

    logger.info(f"[safety_gate] has_flag={has_flag} flags={flags_found}")

    if has_flag:
        return _refer(red_flags=flags_found, reason=reason)

    return {
        "refer_to_doctor":   False,
        "refer_reason":      None,
        "red_flags_found":   [],
        "next_action":       "recommend",
    }


def _refer(red_flags: list[str], reason: str | None) -> dict:
    return {
        "refer_to_doctor":  True,
        "refer_reason":     reason or "กรุณาพบแพทย์เพื่อรับการวินิจฉัยที่ถูกต้อง",
        "red_flags_found":  red_flags,
        "next_action":      "refer",
    }