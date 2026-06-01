"""
prompts/pharmacist.py
---------------------
All LLM prompts live here — one place to tune tone/behavior.

Design:
- SYSTEM_PROMPT  : persona + hard rules (injected every call)
- Node templates : f-string functions, called by each agent node
"""

# ── Persona ────────────────────────────────────────────────────

SYSTEM_PROMPT = """คุณคือเภสัชกรผู้เชี่ยวชาญที่ให้คำปรึกษาด้านยาและอาการเบื้องต้น
ตอบเป็นภาษาไทยเสมอ ใช้ภาษาที่เป็นธรรมชาติ กระชับ อ่านง่าย

กฎที่ต้องปฏิบัติเสมอ:
1. ใช้ข้อมูลจาก guideline ที่ให้มาเป็นหลัก หากไม่มีในข้อมูลให้ใช้ความรู้ทางคลินิกทั่วไป
2. ไม่วินิจฉัยแทนแพทย์ — ให้ข้อมูลเบื้องต้นและแนะนำส่งต่อเมื่อจำเป็น
3. หากพบ red flag ให้แนะนำพบแพทย์ทันที ไม่ต้อง generate คำแนะนำยา
4. ให้เหตุผลสั้นๆ ที่อ่านเข้าใจง่าย ไม่ใช่ medical jargon ที่ไม่จำเป็น
5. ไม่เปิดเผย chain-of-thought ภายใน — แสดงเฉพาะผลสรุปที่กระชับ"""


# ── Node: classify ─────────────────────────────────────────────

def classify_prompt(user_message: str) -> str:
    return f"""วิเคราะห์ข้อความต่อไปนี้และระบุประเภทคำถาม

ข้อความ: "{user_message}"

ตอบด้วย JSON เท่านั้น รูปแบบ:
{{
  "intent": "<symptom | drug_info | general_pharma | unknown>",
  "reason": "<อธิบายสั้นๆ ว่าทำไมถึงเป็น intent นี้>"
}}

คำอธิบาย intent:
- symptom       : ผู้ใช้อธิบายอาการ หรือถามว่าควรใช้ยาอะไรสำหรับอาการ
- drug_info     : ถามข้อมูลยาโดยตรง (ขนาดยา, ผลข้างเคียง, interaction)
- general_pharma: คำถามทั่วไปด้านเภสัชหรือสุขภาพที่ไม่ใช่ symptom/drug โดยตรง
- unknown       : ไม่เกี่ยวข้องกับเภสัช/สุขภาพ"""


# ── Node: clarify ──────────────────────────────────────────────

def completeness_prompt(user_message: str, history: list[dict]) -> str:
    history_text = _format_history(history)
    return f"""ประเมินความสมบูรณ์ของข้อมูลอาการที่ได้รับ

ประวัติการสนทนา:
{history_text}

ข้อความล่าสุด: "{user_message}"

ประเมิน completeness score (0.0–1.0) โดยพิจารณา:
- มีการระบุอาการหลักชัดเจนหรือไม่
- ทราบระยะเวลาที่มีอาการหรือไม่
- ทราบความรุนแรงหรือไม่
- มี context เพิ่มเติมที่สำคัญ (เช่น โรคประจำตัว, ยาที่ใช้อยู่) หรือไม่

ตอบด้วย JSON เท่านั้น:
{{
  "score": <0.0–1.0>,
  "missing": ["<ข้อมูลที่ขาด 1>", "<ข้อมูลที่ขาด 2>"]
}}"""


def clarify_question_prompt(missing_info: list[str], round_num: int) -> str:
    missing_text = ", ".join(missing_info) if missing_info else "รายละเอียดเพิ่มเติม"
    return f"""สร้างคำถามเพื่อขอข้อมูลเพิ่มเติมจากผู้ป่วย (รอบที่ {round_num}/3)

ข้อมูลที่ยังขาด: {missing_text}

กฎ:
- ถามครั้งละ 1 คำถามที่สำคัญที่สุด
- ใช้ภาษาที่เป็นมิตร ไม่เป็นทางการมากเกินไป
- ไม่ถามเรื่องที่ได้รับข้อมูลแล้ว

ตอบเฉพาะคำถามที่จะถามผู้ป่วย ไม่ต้องมีคำนำ"""


# ── Node: clinical_reason ──────────────────────────────────────

def clinical_reason_prompt(
    symptom_summary: str,
    retrieved_context: str,
) -> str:
    return f"""คุณเป็นเภสัชกรที่กำลังวิเคราะห์อาการของผู้ป่วย

อาการที่รวบรวมได้:
{symptom_summary}

ข้อมูลจาก Guideline:
{retrieved_context}

วิเคราะห์และตอบด้วย JSON เท่านั้น:
{{
  "symptom_summary": ["<อาการ 1>", "<อาการ 2>"],
  "differential_diagnosis": [
    {{"name": "<ชื่อโรค/ภาวะ>", "confidence": "<high|medium|low>"}},
    ...
  ],
  "clinical_rationale": [
    "<เหตุผลที่ 1 — อ่านเข้าใจง่าย>",
    "<เหตุผลที่ 2>"
  ],
  "red_flags": ["<red flag ที่พบ หรือ array ว่างถ้าไม่มี>"]
}}

หมายเหตุ: clinical_rationale ต้องเป็นภาษาที่ผู้ป่วยทั่วไปเข้าใจได้ ไม่ใช่ internal CoT"""


# ── Node: safety_gate ─────────────────────────────────────────

RED_FLAG_LIST = [
    "ไอเป็นเลือด (hemoptysis)",
    "เจ็บหน้าอก (chest pain)",
    "หายใจลำบากรุนแรง (severe dyspnea)",
    "ซึมลง สับสน หมดสติ (altered consciousness)",
    "ไข้สูงมาก > 39.5°C ร่วมกับอาการรุนแรง",
    "อาเจียนเป็นเลือด",
    "ปวดศีรษะรุนแรงฉับพลัน (thunderclap headache)",
    "อัมพาตหรืออ่อนแรงครึ่งซีก",
]

def safety_gate_prompt(symptom_summary: str, ddx_list: str) -> str:
    flags = "\n".join(f"- {f}" for f in RED_FLAG_LIST)
    return f"""ตรวจสอบ red flags จากข้อมูลต่อไปนี้

อาการ: {symptom_summary}
การวินิจฉัยเบื้องต้น: {ddx_list}

Red flags ที่ต้องตรวจสอบ:
{flags}

ตอบด้วย JSON เท่านั้น:
{{
  "has_red_flag": <true|false>,
  "red_flags_found": ["<red flag ที่พบ>"],
  "refer_reason": "<เหตุผลสั้นๆ สำหรับบอกผู้ป่วย หรือ null ถ้าไม่มี>"
}}"""


# ── Node: recommendation ──────────────────────────────────────

def recommendation_prompt(
    symptom_summary: str,
    ddx_text: str,
    rationale_text: str,
    retrieved_context: str,
) -> str:
    return f"""คุณเป็นเภสัชกรที่กำลังให้คำแนะนำยา OTC และการดูแลตัวเองเบื้องต้น

อาการสรุป: {symptom_summary}
การวินิจฉัยเบื้องต้น: {ddx_text}
เหตุผลทางคลินิก: {rationale_text}

ข้อมูลจาก Guideline:
{retrieved_context}

เขียนคำแนะนำที่:
1. ระบุยาหรือวิธีการรักษาเบื้องต้น (ถ้ามีใน guideline ให้อ้างอิง)
2. บอกขนาดและวิธีใช้ชัดเจน
3. แจ้งสิ่งที่ต้องระวัง
4. บอกเงื่อนไขที่ควรพบแพทย์
5. ใช้ภาษาเป็นธรรมชาติ กระชับ

ตอบด้วย JSON เท่านั้น:
{{
  "recommendation": "<คำแนะนำฉบับเต็ม>",
  "sources": ["<แหล่งที่มา 1>", "<แหล่งที่มา 2>"]
}}"""


# ── helpers ────────────────────────────────────────────────────

def _format_history(history: list[dict]) -> str:
    if not history:
        return "(ยังไม่มีประวัติการสนทนา)"
    lines = []
    for turn in history[-6:]:  # last 3 exchanges
        role = "ผู้ใช้" if turn.get("role") == "user" else "เภสัชกร"
        lines.append(f"{role}: {turn.get('content', '')}")
    return "\n".join(lines)