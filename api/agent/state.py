"""
agent/state.py  (v4)
------------------------------
CHANGES v4:
- Added user_lang: str  (set by classify_node from user_message detection)
  "th" | "en" — ใช้โดย prompt functions ทุกตัวเพื่อตอบภาษาเดียวกับ user
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal

from typing_extensions import TypedDict


# ── Sub-types ──────────────────────────────────────────────────

class DDxItem(TypedDict):
    name:       str
    confidence: Literal["high", "medium", "low"]
    reasoning:  str


class RetrievedChunk(TypedDict):
    text:   str
    source: str    # e.g. "ARIA Guideline 2022, p.14 [docling]"
    score:  float


# ── Main State ─────────────────────────────────────────────────

class AgentState(TypedDict):

    # ── Conversation ─────────────────────────────────────────
    session_id:   str
    user_message: str                                      # latest user input
    history:      Annotated[list[dict], operator.add]      # append-only

    # ── Intent classification ────────────────────────────────
    intent: Literal["symptom", "drug_info", "general_pharma", "unknown"]
    topic_shift: bool  # v3: True เมื่อ user เปลี่ยนหัวข้อใหม่
    user_lang:   str   # v4: "th" | "en" — detected from user_message by classify_node

    # ── Clarification loop ───────────────────────────────────
    clarify_round:         int           # 0–max_clarify_rounds
    completeness_score:    float         # 0.0–1.0
    clarifying_question:   str | None    # question to ask user

    # domain + complexity awareness
    symptom_domain:        str           # "AOM" | "pharyngitis" | "sinusitis" | "allergy" | "general"
    symptom_complexity:    str           # "simple" | "moderate" | "complex"

    # ── Retrieval ────────────────────────────────────────────
    retrieved_chunks: list[RetrievedChunk]

    # ── Clinical Reasoning ───────────────────────────────────
    symptom_summary:         list[str]       # extracted symptom list
    differential_diagnosis:  list[DDxItem]
    clinical_rationale:      list[str]       # human-readable rationale
    red_flags_found:         list[str]       # empty = all clear
    knowledge_gaps:          list[str]       # topics not in guideline

    # clinical scores
    clinical_scores: dict[str, Any]          # centor_score, aom_severity, sinusitis_criteria

    # Negative case handling
    needs_pushback:   bool
    pushback_reason:  str | None

    # allergy detail flag (v3)
    allergy_detail_incomplete: bool

    # ── Safety Gate ──────────────────────────────────────────
    refer_to_doctor:  bool
    refer_reason:     str | None

    # ── Recommendation ───────────────────────────────────────
    recommendation:   str | None
    sources:          list[str]
    first_line_drug:  str | None           # e.g. "Amoxicillin 500mg q8h × 10d"
    alternatives:     list[str]            # when allergic / contraindicated
    when_to_see_doctor: str | None

    # recommendation extras
    diagnosis_flow:       str | None
    antibiotic_indicated: bool
    supportive_care:      list[str]
    pushback_message:     str | None
    augmented_notes:      str | None

    # ── Flow Control ─────────────────────────────────────────
    next_action: str   # "clarify" | "retrieve" | "refer" | "recommend" | "done"

    # ── Terminal Output (set by format node) ─────────────────
    final_response: dict[str, Any] | None