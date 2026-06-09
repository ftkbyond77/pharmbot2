"""
agent/nodes/format.py  (v3)
--------------------------------------
Changes v3:
- sources ดึงจาก retrieved_chunks โดยตรง (deduplicated, format: "Title, p.XX")
  แทนที่จะรอ LLM ส่ง sources: [] กลับมา ซึ่ง hardcode ว่างอยู่
- ลบ emoji ออกจาก _REFER_HEADER และ _assemble_message
- ลบส่วน supportive_care และ when_to_see ใน _assemble_message ออก
  เพราะ LLM format ไว้ใน recommendation แล้ว ไม่ต้อง append ซ้ำ
"""

from api.agent.state import AgentState
from loguru import logger


_REFER_HEADER = "พบสัญญาณที่ควรพบแพทย์โดยเร็ว"


def format_node(state: AgentState) -> dict:
    refer = state.get("refer_to_doctor", False)
    response = _build_refer_response(state) if refer else _build_normal_response(state)
    logger.info(f"[format] type={response.get('type', 'unknown')}")
    return {"final_response": response}


# ── sources helper ─────────────────────────────────────────────

def _extract_sources(state: AgentState) -> list[str]:
    """
    ดึง source จาก retrieved_chunks
    - Strip extractor suffix [vision] [docling_text] ออก — ไม่มีประโยชน์กับผู้ใช้
    - ถ้าไม่มี page → แสดงแค่ชื่อ+ปี ไม่แสดง p.None
    - Deduplicated + limit 5
    """
    chunks = state.get("retrieved_chunks", [])
    seen:    set[str] = set()
    sources: list[str] = []

    for chunk in chunks:
        raw_src = (chunk.get("source", "") if isinstance(chunk, dict)
                   else getattr(chunk, "source", ""))
        # Strip extractor tag: "AAFP 2022, p.630, [docling_text]" → "AAFP 2022, p.630"
        clean = _clean_source(raw_src.strip())
        if clean and clean not in seen:
            seen.add(clean)
            sources.append(clean)
        if len(sources) >= 5:
            break

    return sources


def _clean_source(src: str) -> str:
    """
    ล้าง internal metadata tags ออกจาก source string
    Input:  "AAFP_2022_Original (2022), p.630, [docling_text]"
    Output: "AAFP 2022, p.630"

    Input:  "แนวทาง URI เด็ก (ไทย)p.40, [vision]"
    Output: "แนวทาง URI เด็ก (ไทย), p.40"
    """
    import re

    # ตัด [extractor] suffix ออก
    src = re.sub(r',?\s*\[[\w_]+\]', '', src).strip()

    # Normalize ชื่อไฟล์ที่น่าเกลียด
    replacements = {
        "AAFP_2022_Original": "AAFP 2022",
        "AAFP_2021_Original": "AAFP 2021",
        "Thai_URI_Children":  "แนวทาง URI เด็ก (ไทย)",
    }
    for ugly, clean in replacements.items():
        src = src.replace(ugly, clean)

    # ตัด (.pdf) ออก
    src = re.sub(r'\.pdf', '', src, flags=re.IGNORECASE)

    # Fix: "ชื่อp.40" → "ชื่อ, p.40" (ถ้า p. ติดกับชื่อโดยไม่มี space)
    src = re.sub(r'([^\s,])(p\.\d)', r'\1, \2', src)

    # ตัด trailing comma/space
    src = src.strip(', ').strip()

    return src


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


# ── normal response ────────────────────────────────────────────

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

    # Serialize DDx
    ddx = state.get("differential_diagnosis", [])
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

    recommendation       = state.get("recommendation", "") or ""
    when_to_see          = state.get("when_to_see_doctor", "")
    augmented_notes      = state.get("augmented_notes")
    first_line           = state.get("first_line_drug")
    alternatives         = state.get("alternatives", [])
    diagnosis_flow       = state.get("diagnosis_flow", "")
    antibiotic_indicated = state.get("antibiotic_indicated", False)
    pushback_message     = state.get("pushback_message")
    supportive_care      = state.get("supportive_care", [])
    clinical_scores      = state.get("clinical_scores", {})

    # ── sources จาก retrieved_chunks (ไม่ใช่จาก LLM) ──────────
    sources = _extract_sources(state)

    message = _assemble_message(
        recommendation=recommendation,
        pushback_message=pushback_message,
    )

    return {
        "type":                 "normal",
        "message":              message,
        "red_flags":            [],
        "refer_to_doctor":      False,
        "diagnosis":            diagnosis_list,
        "diagnosis_flow":       diagnosis_flow,
        "recommendation":       recommendation,
        "antibiotic_indicated": antibiotic_indicated,
        "first_line_drug":      first_line,
        "alternatives":         alternatives,
        "supportive_care":      supportive_care,
        "when_to_see_doctor":   when_to_see,
        "clinical_scores":      clinical_scores,
        "pushback_message":     pushback_message,
        "augmented_notes":      augmented_notes,
        "sources":              sources,
        "clarifying_question":  None,
    }


def _assemble_message(
    recommendation: str,
    pushback_message: str | None,
) -> str:
    """
    ประกอบข้อความสุดท้าย
    LLM format ทุกอย่างใน recommendation แล้ว — ไม่ append ซ้ำ
    เพิ่มแค่ pushback ถ้ามี (negative case)
    """
    parts = []

    if pushback_message:
        parts.append(pushback_message)

    if recommendation:
        parts.append(recommendation)

    return "\n\n".join(p.strip() for p in parts if p.strip()) or recommendation