"""
agent/nodes/format.py  (v4)
--------------------------------------
Changes v4 vs v3:
- ตรวจ state["response_type"] ก่อน render diagnosis + sources
  - "conversational" (followup/chit_chat/off_topic/unknown):
      type = "conversational", diagnosis = [], sources = []
  - "diagnosis_explain" (user ถามขอดู flow การวินิจฉัย):
      type = "diagnosis_explain", diagnosis เต็ม, sources เต็ม, แสดง reasoning flow
  - ปกติ (symptom → clinical pipeline):
      type = "normal", diagnosis + sources ตามเดิม

- เพิ่ม _build_conversational_response() สำหรับ type="conversational"
- เพิ่ม _build_diagnosis_explain_response() สำหรับ type="diagnosis_explain"
- ปรับ _build_normal_response() ให้ pass diagnosis_context เฉพาะ type="normal"
"""

from api.agent.state import AgentState
from loguru import logger


_REFER_HEADER = "พบสัญญาณที่ควรพบแพทย์โดยเร็ว"


def format_node(state: AgentState) -> dict:
    refer         = state.get("refer_to_doctor", False)
    response_type = state.get("response_type", "normal")

    if refer:
        response = _build_refer_response(state)
    elif response_type == "conversational":
        response = _build_conversational_response(state)
    elif response_type == "diagnosis_explain":
        response = _build_diagnosis_explain_response(state)
    else:
        response = _build_normal_response(state)

    logger.info(f"[format] type={response.get('type')} response_type={response_type}")
    return {"final_response": response}


# ── conversational response (followup / chit_chat / off_topic) ────

def _build_conversational_response(state: AgentState) -> dict:
    """
    ตอบแบบสนทนา ไม่แสดง DDx หรือ sources
    """
    recommendation = state.get("recommendation", "") or ""
    return {
        "type":                 "conversational",
        "message":              recommendation.strip(),
        "red_flags":            [],
        "refer_to_doctor":      False,
        "diagnosis":            [],
        "diagnosis_flow":       None,
        "recommendation":       recommendation,
        "antibiotic_indicated": False,
        "first_line_drug":      None,
        "alternatives":         [],
        "supportive_care":      [],
        "when_to_see_doctor":   None,
        "clinical_scores":      {},
        "pushback_message":     None,
        "augmented_notes":      state.get("augmented_notes"),
        "sources":              [],
        "clarifying_question":  None,
    }


# ── diagnosis explain response (user ถามขอดู flow วินิจฉัย) ──────

def _build_diagnosis_explain_response(state: AgentState) -> dict:
    """
    แสดง DDx พร้อม reasoning flow เต็ม + sources
    ใช้เมื่อ user ถามว่า "ทำไมถึงวินิจฉัยเป็นโรคนี้" หรือ "อธิบาย flow การวินิจฉัย"
    """
    ddx            = _serialize_ddx(state)
    sources        = _extract_sources(state)
    recommendation = state.get("recommendation", "") or ""

    # สร้าง reasoning flow จาก DDx + clinical_rationale
    rationale = state.get("clinical_rationale", [])
    flow_parts = []
    for item in ddx:
        flow_parts.append(
            f"{item['name']} ({item['confidence']}): {item.get('reasoning', '')}"
        )
    diagnosis_flow = "\n".join(flow_parts) if flow_parts else ""

    return {
        "type":                 "diagnosis_explain",
        "message":              recommendation.strip(),
        "red_flags":            state.get("red_flags_found", []),
        "refer_to_doctor":      False,
        "diagnosis":            ddx,
        "diagnosis_flow":       diagnosis_flow,
        "clinical_rationale":   rationale,
        "recommendation":       recommendation,
        "antibiotic_indicated": state.get("antibiotic_indicated", False),
        "first_line_drug":      state.get("first_line_drug"),
        "alternatives":         state.get("alternatives", []),
        "supportive_care":      state.get("supportive_care", []),
        "when_to_see_doctor":   state.get("when_to_see_doctor", ""),
        "clinical_scores":      state.get("clinical_scores", {}),
        "pushback_message":     state.get("pushback_message"),
        "augmented_notes":      state.get("augmented_notes"),
        "sources":              sources,
        "clarifying_question":  None,
    }


# ── normal response (symptom → clinical pipeline) ─────────────────

def _build_normal_response(state: AgentState) -> dict:
    # Clarification still in progress
    clarifying_q = state.get("clarifying_question")
    if clarifying_q:
        return {
            "type":                 "clarify",
            "message":              clarifying_q,
            "domain":               state.get("symptom_domain", "general"),
            "red_flags":            [],
            "refer_to_doctor":      False,
            "diagnosis":            [],
            "diagnosis_flow":       None,
            "recommendation":       None,
            "antibiotic_indicated": False,
            "pushback_message":     None,
            "supportive_care":      [],
            "sources":              [],
            "clarifying_question":  clarifying_q,
        }

    ddx            = _serialize_ddx(state)
    sources        = _extract_sources(state)
    recommendation = state.get("recommendation", "") or ""

    message = _assemble_message(
        recommendation=recommendation,
        pushback_message=state.get("pushback_message"),
    )

    return {
        "type":                 "normal",
        "message":              message,
        "red_flags":            [],
        "refer_to_doctor":      False,
        "diagnosis":            ddx,
        "diagnosis_flow":       state.get("diagnosis_flow", ""),
        "recommendation":       recommendation,
        "antibiotic_indicated": state.get("antibiotic_indicated", False),
        "first_line_drug":      state.get("first_line_drug"),
        "alternatives":         state.get("alternatives", []),
        "supportive_care":      state.get("supportive_care", []),
        "when_to_see_doctor":   state.get("when_to_see_doctor", ""),
        "clinical_scores":      state.get("clinical_scores", {}),
        "pushback_message":     state.get("pushback_message"),
        "augmented_notes":      state.get("augmented_notes"),
        "sources":              sources,
        "clarifying_question":  None,
    }


# ── refer response ─────────────────────────────────────────────

def _build_refer_response(state: AgentState) -> dict:
    flags  = state.get("red_flags_found", [])
    reason = state.get("refer_reason") or "กรุณาไปพบแพทย์หรือห้องฉุกเฉินโดยเร็วครับ"

    return {
        "type":                 "refer",
        "message":              f"{_REFER_HEADER}\n\n{reason}",
        "red_flags":            flags,
        "refer_to_doctor":      True,
        "diagnosis":            [],
        "diagnosis_flow":       None,
        "recommendation":       None,
        "antibiotic_indicated": False,
        "pushback_message":     None,
        "supportive_care":      [],
        "sources":              [],
        "clarifying_question":  None,
    }


# ── helpers ────────────────────────────────────────────────────

def _serialize_ddx(state: AgentState) -> list[dict]:
    ddx = state.get("differential_diagnosis", [])
    result = []
    for item in ddx:
        if isinstance(item, dict):
            result.append(item)
        else:
            result.append({
                "name":       getattr(item, "name", str(item)),
                "confidence": getattr(item, "confidence", "low"),
                "reasoning":  getattr(item, "reasoning", ""),
            })
    return result


def _extract_sources(state: AgentState) -> list[str]:
    """
    ดึง source จาก retrieved_chunks — deduplicated, clean, limit 5
    """
    chunks = state.get("retrieved_chunks", [])
    seen:    set[str] = set()
    sources: list[str] = []

    for chunk in chunks:
        raw_src = (
            chunk.get("source", "") if isinstance(chunk, dict)
            else getattr(chunk, "source", "")
        )
        clean = _clean_source(raw_src.strip())
        if clean and clean not in seen:
            seen.add(clean)
            sources.append(clean)
        if len(sources) >= 5:
            break

    return sources


def _clean_source(src: str) -> str:
    import re

    src = re.sub(r',?\s*\[[\w_]+\]', '', src).strip()

    replacements = {
        "AAFP_2022_Original": "AAFP 2022",
        "AAFP_2021_Original": "AAFP 2021",
        "Thai_URI_Children":  "แนวทาง URI เด็ก (ไทย)",
    }
    for ugly, clean in replacements.items():
        src = src.replace(ugly, clean)

    src = re.sub(r'\.pdf', '', src, flags=re.IGNORECASE)
    src = re.sub(r'([^\s,])(p\.\d)', r'\1, \2', src)
    src = src.strip(', ').strip()

    return src


def _assemble_message(
    recommendation: str,
    pushback_message: str | None,
) -> str:
    parts = []
    if pushback_message:
        parts.append(pushback_message)
    if recommendation:
        parts.append(recommendation)
    return "\n\n".join(p.strip() for p in parts if p.strip()) or recommendation