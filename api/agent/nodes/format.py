"""
agent/nodes/format.py  (v2 — Tuned)
--------------------------------------
Node 7 (terminal): Output formatter

CHANGES v2:
- Adds diagnosis_flow to normal response
- Adds pushback_message for Negative Case responses
- Adds antibiotic_indicated flag
- Adds supportive_care list
- Richer "clarify" response type with domain hint
- Natural Thai language assembly

Two response shapes:
  - refer   : show red flags + refer message, no drug advice
  - clarify : ask one (or grouped) question
  - normal  : DDx + diagnosis_flow + recommendation + sources
"""

from api.agent.state import AgentState
from loguru import logger


# ── refer response ─────────────────────────────────────────────

_REFER_HEADER = "⚠️ พบสัญญาณที่ควรพบแพทย์โดยเร็ว"
_REFER_FOOTER = (
    "กรุณาไปพบแพทย์หรือห้องฉุกเฉินโดยเร็ว "
    "เภสัชกรไม่สามารถแนะนำยาได้ในกรณีนี้ครับ"
)


def format_node(state: AgentState) -> dict:
    refer = state.get("refer_to_doctor", False)

    if refer:
        response = _build_refer_response(state)
    else:
        response = _build_normal_response(state)

    logger.info(f"[format] type={response.get('type', 'unknown')}")
    return {"final_response": response}


# ── builders ───────────────────────────────────────────────────

def _build_refer_response(state: AgentState) -> dict:
    flags  = state.get("red_flags_found", [])
    reason = state.get("refer_reason") or _REFER_FOOTER

    return {
        "type":                "refer",
        "message":             f"{_REFER_HEADER}\n\n{reason}",
        "red_flags":           flags,
        "refer_to_doctor":     True,
        "diagnosis":           [],
        "diagnosis_flow":      None,
        "recommendation":      None,
        "antibiotic_indicated": False,
        "pushback_message":    None,
        "supportive_care":     [],
        "sources":             [],
        "clarifying_question": None,
    }


def _build_normal_response(state: AgentState) -> dict:
    # ── Clarification still in progress ──────────────────────
    clarifying_q = state.get("clarifying_question")
    if clarifying_q:
        domain = state.get("symptom_domain", "general")
        return {
            "type":                "clarify",
            "message":             clarifying_q,
            "domain":              domain,
            "red_flags":           [],
            "refer_to_doctor":     False,
            "diagnosis":           [],
            "diagnosis_flow":      None,
            "recommendation":      None,
            "antibiotic_indicated": False,
            "pushback_message":    None,
            "supportive_care":     [],
            "sources":             [],
            "clarifying_question": clarifying_q,
        }

    # ── Full response ─────────────────────────────────────────
    ddx = state.get("differential_diagnosis", [])

    # Serialize DDx
    diagnosis_list = []
    for item in ddx:
        if isinstance(item, dict):
            diagnosis_list.append(item)
        else:
            diagnosis_list.append({
                "name":       getattr(item, "name", str(item)),
                "confidence": getattr(item, "confidence", "low"),
                "reasoning":  getattr(item, "reasoning", ""),
            })

    recommendation      = state.get("recommendation", "")
    sources             = state.get("sources", [])
    when_to_see         = state.get("when_to_see_doctor", "")
    augmented_notes     = state.get("augmented_notes")
    first_line          = state.get("first_line_drug")
    alternatives        = state.get("alternatives", [])
    diagnosis_flow      = state.get("diagnosis_flow", "")
    antibiotic_indicated = state.get("antibiotic_indicated", False)
    pushback_message    = state.get("pushback_message")
    supportive_care     = state.get("supportive_care", [])
    clinical_scores     = state.get("clinical_scores", {})

    # Assemble final message
    message = _assemble_message(
        recommendation=recommendation,
        pushback_message=pushback_message,
        diagnosis_flow=diagnosis_flow,
        first_line=first_line,
        alternatives=alternatives,
        when_to_see=when_to_see,
        supportive_care=supportive_care,
        augmented_notes=augmented_notes,
    )

    return {
        "type":                "normal",
        "message":             message,
        "red_flags":           [],
        "refer_to_doctor":     False,
        "diagnosis":           diagnosis_list,
        "diagnosis_flow":      diagnosis_flow,
        "recommendation":      recommendation,
        "antibiotic_indicated": antibiotic_indicated,
        "first_line_drug":     first_line,
        "alternatives":        alternatives,
        "supportive_care":     supportive_care,
        "when_to_see_doctor":  when_to_see,
        "clinical_scores":     clinical_scores,
        "pushback_message":    pushback_message,
        "augmented_notes":     augmented_notes,
        "sources":             sources,
        "clarifying_question": None,
    }


def _assemble_message(
    recommendation: str,
    pushback_message: str | None,
    diagnosis_flow: str,
    first_line: str | None,
    alternatives: list[str],
    when_to_see: str,
    supportive_care: list[str],
    augmented_notes: str | None,
) -> str:
    """
    Assemble a natural Thai response message.
    Order: pushback (if any) → recommendation → sources hint
    """
    parts = []

    # 1. Pushback (Negative Case)
    if pushback_message:
        parts.append(pushback_message)

    # 2. Main recommendation (already formatted by LLM)
    if recommendation:
        parts.append(recommendation)
    
    # 3. Supportive care (if not already in recommendation)
    if supportive_care and "supportive" not in recommendation.lower():
        care_text = "\n".join(f"• {c}" for c in supportive_care[:4])
        if care_text:
            parts.append(f"การดูแลตัวเอง:\n{care_text}")

    # 4. When to see doctor
    if when_to_see and "พบแพทย์" not in recommendation[-100:]:
        parts.append(f"⚠️ ควรพบแพทย์เมื่อ: {when_to_see}")

    # 5. Augmented notes
    if augmented_notes:
        parts.append(f"ℹ️ {augmented_notes}")

    return "\n\n".join(p.strip() for p in parts if p.strip()) or recommendation