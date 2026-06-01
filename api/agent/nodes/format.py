"""
agent/nodes/format.py
---------------------
Node 7 (terminal): Output formatter

Assembles final_response dict that the API router returns to the client.
Two response shapes:
  - refer   : show red flags + refer message, no drug advice
  - normal  : DDx + rationale + recommendation + sources

Input  : full state
Output : state.final_response (dict consumed by chat router)
"""

from api.agent.state import AgentState
from loguru import logger


# ── refer response ─────────────────────────────────────────────

_REFER_HEADER = "⚠️ พบสัญญาณที่ควรพบแพทย์"
_REFER_FOOTER = (
    "กรุณาไปพบแพทย์หรือห้องฉุกเฉินโดยเร็ว "
    "เภสัชกรไม่สามารถแนะนำยาได้ในกรณีนี้"
)


def format_node(state: AgentState) -> dict:
    refer = state.get("refer_to_doctor", False)

    if refer:
        response = _build_refer_response(state)
    else:
        response = _build_normal_response(state)

    logger.info(f"[format] type={'refer' if refer else 'normal'}")
    return {"final_response": response}


# ── builders ───────────────────────────────────────────────────

def _build_refer_response(state: AgentState) -> dict:
    flags = state.get("red_flags_found", [])
    reason = state.get("refer_reason") or _REFER_FOOTER

    return {
        "type":             "refer",
        "message":          f"{_REFER_HEADER}\n\n{reason}",
        "red_flags":        flags,
        "refer_to_doctor":  True,
        "diagnosis":        [],
        "recommendation":   None,
        "sources":          [],
        "clarifying_question": None,
    }


def _build_normal_response(state: AgentState) -> dict:
    # ── clarification still in progress ──────────────────────
    clarifying_q = state.get("clarifying_question")
    if clarifying_q:
        return {
            "type":                "clarify",
            "message":             clarifying_q,
            "red_flags":           [],
            "refer_to_doctor":     False,
            "diagnosis":           [],
            "recommendation":      None,
            "sources":             [],
            "clarifying_question": clarifying_q,
        }

    # ── full response ─────────────────────────────────────────
    ddx = state.get("differential_diagnosis", [])
    rationale = state.get("clinical_rationale", [])
    recommendation = state.get("recommendation", "")
    sources = state.get("sources", [])

    # Build human-readable message combining rationale + recommendation
    message_parts = []
    if rationale:
        message_parts.append("**เหตุผลเบื้องต้น**\n" + "\n".join(f"- {r}" for r in rationale))
    if recommendation:
        message_parts.append("**คำแนะนำ**\n" + recommendation)
    message = "\n\n".join(message_parts) if message_parts else recommendation

    return {
        "type":                "recommendation",
        "message":             message,
        "red_flags":           [],
        "refer_to_doctor":     False,
        "diagnosis":           _format_ddx_output(ddx),
        "recommendation":      recommendation,
        "sources":             sources,
        "clarifying_question": None,
    }


def _format_ddx_output(ddx_list: list[dict]) -> list[dict]:
    """Normalise DDx for frontend DiagnosisCard."""
    return [
        {
            "name":       d.get("name", ""),
            "confidence": d.get("confidence", "low"),
        }
        for d in ddx_list
    ]