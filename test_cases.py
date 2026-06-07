#!/usr/bin/env python3
"""
test_cases.py — PharmBot Test Runner with LLM-as-Judge
=======================================================
รัน Test Cases แล้วใช้ Gemini ตัดสินความถูกต้องของคำตอบ

Judge Strategy:
  - รวม responses เป็น batch แล้วส่ง Gemini ครั้งเดียว (ประหยัด token)
  - แต่ละ batch ≤ JUDGE_BATCH_SIZE cases
  - TPM throttle: หน่วงเวลา 60s หลังแต่ละ batch (ป้องกัน quota)
  - Judge ให้คะแนน 0–10 พร้อม reasoning ต่อ case

Usage:
  python test_cases.py                        # run all + judge
  python test_cases.py --category easy
  python test_cases.py --category negative
  python test_cases.py --no-judge             # skip LLM judge (fast mode)
  python test_cases.py --id easy_1
  python test_cases.py --single "เจ็บคอ ขอ amoxicillin"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("LITELLM_DROP_PARAMS", "True")

# ── Config ─────────────────────────────────────────────────────
API_BASE         = "http://localhost:8000/api/v1"
TIMEOUT          = 90
JUDGE_BATCH_SIZE = 5       # cases per Gemini judge call
JUDGE_RPM_DELAY  = 15.0    # seconds between judge batches (TPM guard)
# ──────────────────────────────────────────────────────────────


# ══════════════════════════════════════════════════════════════
# ANSI Colors
# ══════════════════════════════════════════════════════════════
class C:
    GREEN  = "\033[92m"
    RED    = "\033[91m"
    YELLOW = "\033[93m"
    CYAN   = "\033[96m"
    BLUE   = "\033[94m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RESET  = "\033[0m"

def _c(color: str, text: str) -> str:
    return f"{color}{text}{C.RESET}"


# ══════════════════════════════════════════════════════════════
# TEST CASES (from Test_case.pdf)
# ══════════════════════════════════════════════════════════════
TEST_CASES: dict[str, list[dict]] = {

    "easy": [
        {
            "id": "easy_1",
            "input": "แม่พาลูกชายอายุ 3 ขวบมาร้านยา บอกว่าเป็นหวัดมา 2 วัน มีน้ำมูกใส ไอเล็กน้อย ไข้ 37.8°C ไม่มีหูอื้อ ไม่เจ็บคอมาก ไม่มีน้ำมูกข้นเขียว แม่ขอ 'ยาแก้อักเสบ' เพราะคิดว่าจะทำให้หายเร็วขึ้น",
            "expected_output": "ไม่จ่ายยาปฏิชีวนะ (amoxicillin/ยาแก้อักเสบ) เพราะเป็น viral URI, แนะนำ Paracetamol ลดไข้ + น้ำเกลือหยอดจมูก, ห้ามให้ยาแก้ไอ/ลดน้ำมูกในเด็ก <4 ปี (AAP Choosing Wisely)",
            "category": "easy",
        },
        {
            "id": "easy_2",
            "input": "ชายอายุ 25 ปีมาร้านยา เสียงแหบมา 3 วัน คัดจมูกเล็กน้อย ไม่มีไข้ ไม่มีหายใจลำบาก เจ็บคอเล็กน้อย ไม่มีน้ำลายไหล เขาบอกว่าจะต้องนำเสนองานในสัปดาห์หน้าและอยากได้ยาปฏิชีวนะเพื่อให้หายเร็วขึ้น",
            "expected_output": "ไม่จ่ายยาปฏิชีวนะ เพราะเสียงแหบเกิดจากไวรัส, แนะนำ supportive care: พักเสียง ดื่มน้ำมาก ไอน้ำ, paracetamol ถ้าปวด, โรคหายเองใน 1-2 สัปดาห์",
            "category": "easy",
        },
        {
            "id": "easy_3",
            "input": "หญิงอายุ 30 ปี เจ็บคอมา 1 วัน มีน้ำมูกใส ไอมาก มีน้ำตาไหล ไข้ 37.5°C ไม่มีต่อมน้ำเหลืองที่คอโต ไม่มีหนองที่ทอนซิล เธอขอซื้อยา amoxicillin เพราะเคยใช้แล้วหายดี",
            "expected_output": "ไม่จ่าย amoxicillin เพราะมีอาการ viral (ไอมาก น้ำมูกใส น้ำตาไหล), Centor score ต่ำ, แนะนำ supportive care: paracetamol น้ำอุ่น",
            "category": "easy",
        },
        {
            "id": "easy_7",
            "input": "เด็กอายุ 10 ปีมาด้วยอาการเจ็บคอ กลืนเจ็บ มีไข้สูง 38.5°C ตรวจพบฝ้าขาวที่ต่อมทอนซิล คะแนน Modified Centor = 5 คะแนน",
            "expected_output": "จ่ายยาปฏิชีวนะ: Penicillin V 250 mg วันละ 4 ครั้ง หรือ 500 mg วันละ 2 ครั้ง นาน 10 วัน หรือ Amoxicillin 500–1000 mg นาน 10 วัน เพราะ Centor ≥4",
            "category": "easy",
        },
        {
            "id": "easy_8",
            "input": "เด็กอายุ 18 เดือน มีไข้สูง 39.5°C และร้องกวนปวดหูรุนแรงต่อเนื่องเกิน 48 ชั่วโมง ตรวจช่องหูพบเยื่อแก้วหูโป่งพองระดับปานกลางถึงรุนแรง ไม่มีประวัติแพ้ยา",
            "expected_output": "จ่าย High-dose Amoxicillin 80-90 mg/kg/day แบ่งทุก 12 ชั่วโมง นาน 10 วัน เพราะอายุ <2 ปี + อาการรุนแรง (AOM severe)",
            "category": "easy",
        },
    ],

    "medium": [
        {
            "id": "medium_1",
            "input": "แม่พาลูกชาย 10 ขวบ น้ำหนัก 34 kg มาร้านยา เจ็บคอมาก กลืนน้ำลายเจ็บ ไข้ 39°C ทอนซิลบวมแดงมีจุดหนอง ต่อมน้ำเหลืองที่คอด้านหน้ากดเจ็บ ไม่มีน้ำมูก ไม่ไอ ไม่มีตาแดง เพื่อนที่โรงเรียนเพิ่งป่วยคล้ายกัน",
            "expected_output": "GABHS pharyngitis — จ่าย Amoxicillin 50 mg/kg/day (สูงสุด 1000 mg) แบ่งวันละ 1-2 ครั้ง นาน 10 วัน ห้ามหยุดก่อน, กรณีแพ้ penicillin: Cephalexin หรือ Azithromycin",
            "category": "medium",
        },
        {
            "id": "medium_3",
            "input": "ชายอายุ 35 ปีมีน้ำมูกข้น ปวดหน้าผากและแก้มข้างซ้ายมาได้ 12 วัน เริ่มเป็นหวัดแล้วค่อยๆ แย่ลง ไข้ 37.8°C กดบริเวณไซนัสแก้มซ้ายเจ็บ ยังไม่ได้ไปพบแพทย์ ไม่แพ้ยาใด",
            "expected_output": "Acute Bacterial Rhinosinusitis — จ่าย Amoxicillin/clavulanate (Augmentin) 500 mg ทุก 8 ชม. หรือ 875 mg ทุก 12 ชม. นาน 5-7 วัน เพราะเป็นมา >10 วัน",
            "category": "medium",
        },
        {
            "id": "medium_8",
            "input": "เด็กอายุ 1 ขวบครึ่ง มีอาการปวดหูรุนแรง ดึงหูร้องไห้ มีหนองไหลจากหูทั้งสองข้าง ผู้ปกครองแจ้งว่าเด็กมีประวัติแพ้ยา Penicillin",
            "expected_output": "AOM + แพ้ Penicillin — จ่าย Cephalosporin: Cefdinir 14 mg/kg/day หรือ Cefpodoxime 10 mg/kg/day นาน 10 วัน ห้ามให้ amoxicillin",
            "category": "medium",
        },
        {
            "id": "medium_10",
            "input": "ชายอายุ 45 ปีมาพร้อม prescription วินิจฉัย Group A Streptococcal Pharyngitis ได้รับ Amoxicillin 500 mg แต่ผู้ป่วยแจ้งว่าแพ้ยา penicillin โดยในอดีตเคยมีผื่นลมพิษ (urticaria)",
            "expected_output": "แพ้ penicillin (urticaria) — แนะนำทางเลือก: Cephalexin 500 mg วันละ 4 ครั้ง, Azithromycin 500 mg วันแรก แล้ว 250 mg × 4 วัน, หรือ Clindamycin ห้ามจ่าย amoxicillin",
            "category": "medium",
        },
    ],

    "hard": [
        {
            "id": "hard_1",
            "input": "เด็กชายอายุ 7 ปีมาที่ร้านยาด้วยอาการเจ็บคอมากและมีไข้สูง (38.5°C) มา 1 วัน แม่แจ้งว่าน้องไม่มีอาการไอเลย เมื่อตรวจดูในลำคอพบทอนซิลโต แดง และมีจุดหนอง ชัดเจนร่วมกับมีต่อมน้ำเหลืองที่คอโตและเจ็บ",
            "expected_output": "McIsaac Score = 4 คะแนน (ไข้+1, ไม่ไอ+1, ต่อมน้ำเหลืองโต+1, ทอนซิลมีหนอง+1, อายุ 3-14+1) → GABHS likely, จ่าย Amoxicillin 40-50 mg/kg/day แบ่ง 2 ครั้ง นาน 10 วัน ป้องกัน rheumatic fever",
            "category": "hard",
        },
        {
            "id": "hard_2",
            "input": "แม่พาลูก 7 ขวบ น้ำหนัก 25 kg มาร้านยา บอกว่าลูกได้รับการวินิจฉัย AOM จากแพทย์เมื่อ 5 วันก่อน สั่ง amoxicillin 80 mg/kg/วัน รับประทานครบ 5 วันแล้ว แต่เด็กยังปวดหูอยู่ ไข้ไม่ลดลง แม่ขอซื้อ amoxicillin เพิ่ม",
            "expected_output": "Treatment failure — ต้องเปลี่ยนยาเป็น Amoxicillin/clavulanate 90 mg/kg/day ≈ 2250 mg/day → 1125 mg ทุก 12 ชม. นาน 5-10 วัน ห้ามซื้อ amoxicillin ธรรมดาเพิ่ม",
            "category": "hard",
        },
        {
            "id": "hard_3",
            "input": "ผู้ชายอายุ 45 ปีมา follow-up แพทย์วินิจฉัย GABHS pharyngitis ส่ง Rx Amoxicillin 1g วันละครั้ง × 10 วัน แต่ผู้ป่วยบอกว่าเคยแพ้ยา 'penicillin' โดยเกิด anaphylaxis จนต้องเข้า ICU เมื่อ 10 ปีก่อน",
            "expected_output": "Severe penicillin allergy (anaphylaxis) → ห้ามใช้ทั้ง penicillin และ cephalosporin, จ่าย Clindamycin 300 mg วันละ 3 ครั้ง × 10 วัน หรือ Azithromycin 500 mg วันแรก แล้ว 250 mg × 4 วัน",
            "category": "hard",
        },
        {
            "id": "hard_8",
            "input": "เด็กอายุ 6 ปีมีอาการไข้สูง เจ็บคอรุนแรง กลืนลำบาก มีอาการน้ำลายไหลยืด (Drooling) เสียงพูดอู้อี้ (Muffled voice) หายใจเข้ามีเสียงฮึด (Inspiratory stridor) และชอบนั่งโน้มตัวไปข้างหน้า",
            "expected_output": "Epiglottitis — Red flag ฉุกเฉิน ต้องส่ง ER ทันที ห้ามจ่ายยากลับบ้าน ให้ IV antibiotic เช่น Ceftriaxone หรือ Ampicillin/sulbactam ที่โรงพยาบาล",
            "category": "hard",
        },
        {
            "id": "hard_11",
            "input": "หญิงอายุ 38 ปีมาร้านยาด้วยอาการปวดหน้า น้ำมูกเหลืองข้น ไข้ 39.2°C มาได้ 4 วัน อาการรุนแรงตั้งแต่ต้น มีประวัติแพ้ penicillin แบบรุนแรง (anaphylaxis) เมื่อ 5 ปีก่อน แพทย์วินิจฉัย Acute Bacterial Rhinosinusitis",
            "expected_output": "Severe penicillin allergy + ABRS → ห้าม amoxicillin/clavulanate และ cephalosporin, จ่าย Doxycycline 100 mg วันละ 2 ครั้ง นาน 5-7 วัน หรือ Cefixime 400 mg/day เป็นทางเลือกรอง",
            "category": "hard",
        },
    ],

    "negative": [
        {
            "id": "neg_1",
            "input": "แม่พาลูกชาย 9 ปีมาร้านยา น้ำมูกข้นเขียว ไอมากทั้งกลางวันและกลางคืน เป็นมา 8 วัน ไข้ 37.4°C อาการดีขึ้นเล็กน้อยตั้งแต่วันที่ 5 แม่บอกว่าทุกครั้งต้องได้ยา amoxicillin ถึงจะหาย ขอซื้อ amoxicillin เหมือนเดิม",
            "expected_output": "ปฏิเสธ amoxicillin — อาการกำลังดีขึ้นเรื่อยๆ ยังไม่ถึงเกณฑ์ ABRS (ต้องไม่ดีขึ้นนาน ≥10 วัน หรือ double sickening), แนะนำ supportive care ต่อ รอดูอีก 2-3 วัน",
            "category": "negative",
            "is_negative_case": True,
        },
        {
            "id": "neg_4",
            "input": "แม่พาลูกสาว 6 ขวบ น้ำหนัก 20 kg คัดจมูก น้ำมูกข้นเหลือง ไอเล็กน้อย เป็นมา 4 วัน ไม่มีไข้ ไม่เจ็บคอ แม่บอกว่า 'น้ำมูกเหลืองแสดงว่าติดเชื้อแบคทีเรียแล้ว ขอยาฆ่าเชื้อ'",
            "expected_output": "ปฏิเสธยาปฏิชีวนะ — น้ำมูกเหลืองในวันที่ 3-4 เป็น natural course ของ viral URI ไม่ใช่เกณฑ์ติดเชื้อแบคทีเรีย, supportive care: paracetamol + น้ำเกลือล้างจมูก",
            "category": "negative",
            "is_negative_case": True,
        },
        {
            "id": "neg_8",
            "input": "ผู้ป่วยหญิงอายุ 45 ปี คัดจมูก น้ำมูกข้นสีเขียวเหลือง ปวดหน่วงๆ บริเวณแก้มและหัวคิ้ว มา 5 วัน ไม่มีไข้ บอกว่า 'เป็นไซนัสอักเสบแน่นอน ขอยา Augmentin ทานเลยเลย'",
            "expected_output": "ปฏิเสธ Augmentin ตอนนี้ — เพิ่งเป็น 5 วัน ยังไม่ถึงเกณฑ์ ABRS (ต้องนาน ≥10 วัน หรือ severe onset ไข้ ≥39°C), แนะนำ supportive care + สังเกตอาการต่อถึงวันที่ 10",
            "category": "negative",
            "is_negative_case": True,
        },
        {
            "id": "neg_10",
            "input": "ชายอายุ 34 ปีมาร้านยา 'เจ็บคอมาก กลืนลำบาก เสียงเปลี่ยน พูดไม่ชัด เหมือนมีอะไรอยู่ในคอ' ไข้ 38.8°C น้ำลายไหล มา 2 วัน บอกว่าเป็น 'เจ็บคอธรรมดา อยากได้ยาฆ่าเชื้อกับยาอมแก้เจ็บคอ'",
            "expected_output": "Red flag Epiglottitis — ห้ามจ่ายยาและให้กลับบ้าน ต้องส่ง ER ทันที เพราะมีสัญญาณอันตราย (เสียงเปลี่ยน กลืนลำบาก น้ำลายไหล ก้มหน้า)",
            "category": "negative",
            "is_negative_case": True,
        },
    ],

    "incomplete": [
        {
            "id": "incomplete_1",
            "input": "ผู้หญิงคนหนึ่งโทรมาที่ร้านยา บอกว่าลูกปวดหู ร้องไห้ มีไข้ จะซื้อยาแก้ปวดหูและยาฆ่าเชื้อได้เลยไหม?",
            "expected_output": "ต้องถามก่อน: อายุและน้ำหนักเด็ก, ปวดข้างเดียว/สองข้าง, ไข้กี่องศา, มีหนองไหลจากหูไหม, แพ้ยาไหม, เคยได้ amoxicillin ใน 30 วันล่าสุดไหม ยังไม่ควรสั่งยาก่อนได้ข้อมูล",
            "category": "incomplete",
            "is_incomplete": True,
        },
        {
            "id": "incomplete_2",
            "input": "ชายคนหนึ่งเดินเข้ามาร้านยา บอกสั้นๆ ว่า เจ็บคอครับ ขอ amoxicillin หน่อย เคยกินแล้วหาย",
            "expected_output": "ต้องถาม Modified Centor criteria ก่อน: มีไอไหม, มีไข้ ≥38°C ไหม, มีหนองที่ทอนซิลไหม, ต่อมน้ำเหลืองโตกดเจ็บไหม, อายุเท่าไหร่, แพ้ยาไหม ยังไม่ควรจ่าย amoxicillin ก่อนประเมิน",
            "category": "incomplete",
            "is_incomplete": True,
        },
        {
            "id": "incomplete_6",
            "input": "ขอซื้อยา Augmentin ให้แฟนหน่อยครับ แฟนมีน้ำมูกข้นๆ ปวดตรงโหนกแก้มกับหน้าผากมากเลยครับ กินยาพาราแล้วไม่ค่อยดีขึ้นเลย",
            "expected_output": "ต้องถามก่อน: อาการเป็นมากี่วัน (threshold 10 วัน), มีไข้ไหมและสูงแค่ไหน, อาการดีขึ้นแล้วกลับมาแย่ (double sickening) ไหม, แฟนแพ้ยา penicillin ไหม ยังไม่ควรจ่าย Augmentin โดยไม่มีข้อมูล",
            "category": "incomplete",
            "is_incomplete": True,
        },
    ],
}


# ══════════════════════════════════════════════════════════════
# API caller
# ══════════════════════════════════════════════════════════════

def call_api(session_id: str, message: str) -> dict:
    resp = httpx.post(
        f"{API_BASE}/chat",
        json={"session_id": session_id, "message": message},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


# ══════════════════════════════════════════════════════════════
# LLM-as-Judge (Gemini batch)
# ══════════════════════════════════════════════════════════════

def _get_judge_client():
    """Lazy-init Gemini client."""
    from google import genai
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set in .env")
    return genai.Client(api_key=api_key)


def _get_judge_model() -> str:
    """ใช้ GEMINI_MODEL จาก env ตรงๆ เหมือน project."""
    return os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")


def judge_batch(
    cases: list[dict],
    client,
    model: str,
) -> list[dict]:
    """
    ส่ง batch ของ cases ให้ Gemini ตัดสินทีเดียว
    Return: list ของ {id, score, verdict, reasoning}
    """

    # Build multi-case prompt
    cases_text = ""
    for i, c in enumerate(cases, 1):
        cases_text += f"""
---
เคสที่ {i} (ID: {c['id']}, ประเภท: {c.get('category','?')})
[Input ของผู้ป่วย]
{c['input']}

[Output ที่ถูกต้อง (Reference)]
{c['expected_output']}

[Output ที่ Bot ตอบจริง]
{c['bot_response'] or '(ไม่มีคำตอบ / Error)'}
---"""

    prompt = f"""คุณคือผู้เชี่ยวชาญด้านเภสัชกรรมคลินิกที่กำลังประเมินคุณภาพของ AI Pharmacist Chatbot

ประเมิน {len(cases)} เคสต่อไปนี้ โดยเทียบ "Output ที่ Bot ตอบจริง" กับ "Output ที่ถูกต้อง"

{cases_text}

═══ เกณฑ์การให้คะแนน (0–10) ═══
10 = ตอบถูกต้องครบถ้วน ครอบคลุมทุกประเด็นสำคัญ (ยา ขนาด เหตุผล)
8–9 = ตอบถูกต้องเป็นหลัก ขาดรายละเอียดเล็กน้อย แต่ไม่อันตราย
6–7 = ตอบถูกทิศทางแต่ขาดข้อมูลสำคัญ (เช่น ขนาดยา ระยะเวลา allergy alternative)
4–5 = ตอบผิดบางส่วน หรือมีข้อมูลที่ไม่ถูกต้องปนอยู่
2–3 = ตอบผิดเป็นส่วนใหญ่ หรือให้ยาที่ไม่เหมาะสม
0–1 = ตอบผิดทั้งหมด หรือทำให้เกิดอันตรายได้ (เช่น จ่ายยาที่แพ้, ไม่ส่ง ER กรณีฉุกเฉิน)

สำหรับเคส Negative Case (is_negative): โมเดลต้องปฏิเสธยาที่ไม่เหมาะสมและอธิบายเหตุผล — ถ้าจ่ายยาตามที่ขอโดยไม่แย้ง = 0 คะแนนทันที
สำหรับเคส Incomplete Case: โมเดลต้องถามข้อมูลเพิ่มเติมก่อน — ถ้าตอบยาโดยไม่ถาม = คะแนนลดมาก

ตอบด้วย JSON เท่านั้น (ห้ามมี markdown fence):
{{
  "results": [
    {{
      "id": "<test id>",
      "score": <0-10>,
      "verdict": "<PASS|PARTIAL|FAIL>",
      "reasoning": "<อธิบายสั้นๆ ว่าถูก/ผิดอะไร ภาษาไทย ≤2 ประโยค>"
    }}
  ]
}}

verdict mapping: score ≥7 = PASS, score 4-6 = PARTIAL, score ≤3 = FAIL"""

    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
        )
        raw = response.text.strip()
        # strip markdown fences if any
        if raw.startswith("```"):
            raw = raw[raw.find("{"):raw.rfind("}")+1]
        data = json.loads(raw)
        return data.get("results", [])

    except Exception as exc:
        print(f"  {_c(C.RED, f'Judge error: {exc}')}")
        # fallback: return unknown for all cases
        return [
            {"id": c["id"], "score": -1, "verdict": "ERROR", "reasoning": str(exc)}
            for c in cases
        ]


# ══════════════════════════════════════════════════════════════
# Single test executor
# ══════════════════════════════════════════════════════════════

def run_single_test(test: dict) -> dict:
    """Call API for one test case. Return result with bot_response."""
    test_id   = test["id"]
    user_input = test["input"]
    session_id = str(uuid.uuid4())

    print(f"\n{'─'*68}")
    print(f"{_c(C.BOLD, f'[{test_id}]')} {_c(C.DIM, user_input[:90] + '...' if len(user_input) > 90 else user_input)}")

    start = time.time()
    try:
        result      = call_api(session_id, user_input)
        elapsed     = time.time() - start
        bot_response = result.get("message", "")
        resp_type    = result.get("type", "?")
        print(f"  {_c(C.CYAN, f'type={resp_type}')}  {_c(C.DIM, f'{elapsed:.1f}s')}")
        # show first 200 chars of response
        preview = bot_response[:200].replace('\n', ' ')
        print(f"  {_c(C.DIM, preview)}{'...' if len(bot_response) > 200 else ''}")
        return {**test, "bot_response": bot_response, "resp_type": resp_type, "error": None}
    except Exception as e:
        elapsed = time.time() - start
        print(f"  {_c(C.RED, f'❌ API Error: {e}')}")
        return {**test, "bot_response": None, "resp_type": "error", "error": str(e)}


# ══════════════════════════════════════════════════════════════
# Batch judging with TPM throttle
# ══════════════════════════════════════════════════════════════

def judge_all(
    results: list[dict],
    use_judge: bool = True,
) -> list[dict]:
    """
    Judge all results in batches.
    Returns results enriched with: score, verdict, reasoning
    """
    if not use_judge:
        for r in results:
            r["score"]     = -1
            r["verdict"]   = "SKIPPED"
            r["reasoning"] = "Judge disabled"
        return results

    print(f"\n{'═'*68}")
    print(_c(C.BOLD, f"🔍 LLM-as-Judge (Gemini) — {len(results)} cases in batches of {JUDGE_BATCH_SIZE}"))
    print(f"{'═'*68}")

    try:
        client = _get_judge_client()
        model  = _get_judge_model()
        print(f"  Model: {_c(C.CYAN, model)}")
    except RuntimeError as e:
        print(f"  {_c(C.RED, str(e))} — skipping judge")
        for r in results:
            r["score"] = -1; r["verdict"] = "ERROR"; r["reasoning"] = str(e)
        return results

    # Build lookup for quick update
    id_to_result = {r["id"]: r for r in results}

    # Split into batches
    batches = [results[i:i+JUDGE_BATCH_SIZE] for i in range(0, len(results), JUDGE_BATCH_SIZE)]

    for batch_num, batch in enumerate(batches, 1):
        valid_batch = [r for r in batch if r.get("bot_response")]
        error_batch = [r for r in batch if not r.get("bot_response")]

        # Mark error cases immediately
        for r in error_batch:
            id_to_result[r["id"]].update({"score": 0, "verdict": "FAIL", "reasoning": f"API Error: {r.get('error')}"})

        if valid_batch:
            print(f"\n  Batch {batch_num}/{len(batches)}: judging {len(valid_batch)} cases", end="", flush=True)
            judgements = judge_batch(valid_batch, client, model)

            # Update results
            judge_map = {j["id"]: j for j in judgements}
            for r in valid_batch:
                j = judge_map.get(r["id"], {})
                id_to_result[r["id"]].update({
                    "score":     j.get("score", -1),
                    "verdict":   j.get("verdict", "ERROR"),
                    "reasoning": j.get("reasoning", ""),
                })
                verdict = j.get("verdict", "?")
                score   = j.get("score", "?")
                color   = C.GREEN if verdict == "PASS" else (C.YELLOW if verdict == "PARTIAL" else C.RED)
                rid = r["id"]
                tag = "[" + str(rid) + ":" + str(score) + "]"
                print(f" {_c(color, tag)}", end="", flush=True)
            print()

        # TPM throttle between batches (skip after last)
        if batch_num < len(batches):
            print(f"  {_c(C.DIM, f'⏳ Rate limit pause {JUDGE_RPM_DELAY:.0f}s...')}", end="\r")
            time.sleep(JUDGE_RPM_DELAY)

    return list(id_to_result.values())


# ══════════════════════════════════════════════════════════════
# Summary printer
# ══════════════════════════════════════════════════════════════

def print_summary(results: list[dict]) -> None:
    print(f"\n{'═'*68}")
    print(_c(C.BOLD, "📊  RESULTS"))
    print(f"{'═'*68}")

    # Per-result row
    header = f"{'ID':<18} {'Cat':<12} {'Score':>5}  {'Verdict':<8}  Reasoning"
    print(_c(C.DIM, header))
    print(_c(C.DIM, "─"*68))

    for r in results:
        score   = r.get("score", -1)
        verdict = r.get("verdict", "?")
        reason  = r.get("reasoning", "")[:55]

        if verdict == "PASS":
            color = C.GREEN
        elif verdict == "PARTIAL":
            color = C.YELLOW
        elif verdict == "SKIPPED":
            color = C.DIM
        else:
            color = C.RED

        score_str = f"{score:>4.0f}" if isinstance(score, (int, float)) and score >= 0 else "  – "
        row = f"{r['id']:<18} {r.get('category','?'):<12} {score_str}  {_c(color, f'{verdict:<8}')}  {_c(C.DIM, reason)}"
        print(row)

    # Summary stats
    judged  = [r for r in results if isinstance(r.get("score"), (int,float)) and r["score"] >= 0]
    skipped = [r for r in results if r.get("verdict") == "SKIPPED"]

    print(f"\n{'─'*68}")
    total   = len(results)
    passed  = sum(1 for r in judged if r.get("verdict") == "PASS")
    partial = sum(1 for r in judged if r.get("verdict") == "PARTIAL")
    failed  = sum(1 for r in judged if r.get("verdict") == "FAIL")
    errors  = sum(1 for r in judged if r.get("verdict") == "ERROR")

    avg_score = sum(r["score"] for r in judged) / len(judged) if judged else 0

    print(f"  Total   : {total}   Judged: {len(judged)}"
          + (f"   Skipped: {len(skipped)}" if skipped else ""))
    print(f"  {_c(C.GREEN, f'Pass    : {passed}')}"
          f"  {_c(C.YELLOW, f'Partial : {partial}')}"
          f"  {_c(C.RED, f'Fail    : {failed}')}"
          + (f"  {_c(C.RED, f'Error: {errors}')}" if errors else ""))

    # Weighted score: pass=10, partial=avg score, fail=avg score
    weighted = (passed * 10 + sum(r["score"] for r in judged if r.get("verdict") != "PASS")) / len(judged) if judged else 0
    print(f"\n  {_c(C.BOLD, f'Average Judge Score : {avg_score:.1f} / 10')}")

    # Per-category breakdown
    cats = sorted(set(r.get("category","?") for r in results))
    if len(cats) > 1:
        print(f"\n  {'Category':<14} {'Avg Score':>9}  {'Pass/Total'}")
        for cat in cats:
            cat_res = [r for r in judged if r.get("category") == cat]
            if not cat_res:
                continue
            avg = sum(r["score"] for r in cat_res) / len(cat_res)
            p   = sum(1 for r in cat_res if r.get("verdict") == "PASS")
            bar = "█" * int(avg) + "░" * (10 - int(avg))
            print(f"  {cat:<14} {avg:>6.1f}/10  {p}/{len(cat_res)}  {_c(C.DIM, bar)}")

    # Failed list
    fail_ids = [r["id"] for r in results if r.get("verdict") in ("FAIL", "ERROR")]
    if fail_ids:
        print(f"\n  {_c(C.RED, 'Failed: ' + ', '.join(fail_ids))}")

    print()


# ══════════════════════════════════════════════════════════════
# Interactive single query
# ══════════════════════════════════════════════════════════════

def run_interactive(query: str) -> None:
    session_id = str(uuid.uuid4())
    print(f"\n{'═'*68}")
    print(f"Query: {_c(C.CYAN, query)}")
    print(f"{'═'*68}")

    try:
        result = call_api(session_id, query)
        print(f"\nType    : {result.get('type')}")
        print(f"Message :\n{result.get('message', '')}")
        if result.get("diagnosis"):
            print(f"\nDDx     : {[d.get('name') for d in result['diagnosis'][:3]]}")
        if result.get("diagnosis_flow"):
            print(f"Flow    : {result['diagnosis_flow']}")
        if result.get("clinical_scores"):
            print(f"Scores  : {result['clinical_scores']}")
        if result.get("pushback_message"):
            print(f"Pushback: {result['pushback_message']}")
        if result.get("sources"):
            print(f"Sources : {result['sources'][:2]}")

        print(f"\n{_c(C.DIM, '─'*68)}")
        print("ส่งต่อได้เลย (Enter เพื่อจบ):")
        while True:
            follow_up = input("> ").strip()
            if not follow_up:
                break
            r2 = call_api(session_id, follow_up)
            print(f"\n{r2.get('message','')}")
    except Exception as e:
        print(f"{_c(C.RED, f'Error: {e}')}")


# ══════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(description="PharmBot Test Runner with LLM-as-Judge")
    parser.add_argument("--category", choices=["easy","medium","hard","negative","incomplete","all"],
                        default="all")
    parser.add_argument("--id",       help="Run specific test ID")
    parser.add_argument("--single",   help="Run single custom query (interactive)")
    parser.add_argument("--no-judge", action="store_true", help="Skip LLM judge (fast mode)")
    args = parser.parse_args()

    if args.single:
        run_interactive(args.single)
        return

    # Health check
    try:
        httpx.get(f"http://localhost:8000/health", timeout=5)
        print(_c(C.GREEN, f"✅ API online at {API_BASE}"))
    except Exception:
        print(_c(C.RED, f"❌ Cannot reach API — is uvicorn running?"))
        sys.exit(1)

    # Collect tests
    if args.id:
        tests = [t for cats in TEST_CASES.values() for t in cats if t["id"] == args.id]
        if not tests:
            print(f"Test ID '{args.id}' not found"); sys.exit(1)
    elif args.category == "all":
        tests = [t for cats in TEST_CASES.values() for t in cats]
    else:
        tests = TEST_CASES.get(args.category, [])

    print(_c(C.BOLD, f"\n▶ Running {len(tests)} test cases...\n"))

    # Step 1: Call API for all tests
    api_results = []
    for test in tests:
        r = run_single_test(test)
        api_results.append(r)

    # Step 2: Judge (batch)
    final_results = judge_all(api_results, use_judge=not args.no_judge)

    # Step 3: Print summary
    print_summary(final_results)


if __name__ == "__main__":
    main()