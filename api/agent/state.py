"""
agent/state.py  (v2 — Tuned)
------------------------------
Single source of truth for all data flowing through the graph.
LangGraph merges partial dicts returned by each node into the state.

CHANGES v2:
- Added symptom_domain, symptom_complexity (from clarify node)
- Added clinical_scores (Centor, AOM, Sinusitis from clinical_reason)
- Added needs_pushback, pushback_reason (Negative Case handling)
- Added diagnosis_flow, antibiotic_indicated, supportive_care (from recommendation)
- Added first_line_drug, alternatives (exposed directly)
- Added knowledge_gaps for debugging
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal

from typing_extensions import TypedDict


# ── Sub-types ──────────────────────────────────────────────────

class DDxItem(TypedDict):
    name:       str
    confidence: Literal["high", "medium", "low"]
    reasoning:  str  # v2: added reasoning field


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

    # ── Clarification loop ───────────────────────────────────
    clarify_round:         int           # 0–max_clarify_rounds
    completeness_score:    float         # 0.0–1.0
    clarifying_question:   str | None    # question to ask user

    # v2: domain + complexity awareness
    symptom_domain:        str           # "ear" | "throat" | "sinus_nasal" | "general"
    symptom_complexity:    str           # "simple" | "moderate" | "complex"

    # ── Retrieval ────────────────────────────────────────────
    retrieved_chunks: list[RetrievedChunk]

    # ── Clinical Reasoning ───────────────────────────────────
    symptom_summary:         list[str]       # extracted symptom list
    differential_diagnosis:  list[DDxItem]
    clinical_rationale:      list[str]       # human-readable rationale
    red_flags_found:         list[str]       # empty = all clear
    knowledge_gaps:          list[str]       # topics not in guideline

    # v2: clinical scores
    clinical_scores: dict[str, Any]          # centor_score, aom_severity, sinusitis_criteria

    # v2: Negative case handling
    needs_pushback:   bool
    pushback_reason:  str | None

    # ── Safety Gate ──────────────────────────────────────────
    refer_to_doctor:  bool
    refer_reason:     str | None

    # ── Recommendation ───────────────────────────────────────
    recommendation:   str | None
    sources:          list[str]
    first_line_drug:  str | None           # e.g. "Amoxicillin 500mg q8h × 10d"
    alternatives:     list[str]            # when allergic / contraindicated
    when_to_see_doctor: str | None

    # v2: recommendation extras
    diagnosis_flow:       str | None       # "อาการ → Centor 4 → GABHS likely → Amoxicillin"
    antibiotic_indicated: bool             # True if ATB is recommended
    supportive_care:      list[str]        # list of self-care recommendations
    pushback_message:     str | None       # polite correction message for negative cases
    augmented_notes:      str | None       # notes from general clinical knowledge

    # ── Flow Control ─────────────────────────────────────────
    next_action: str   # "clarify" | "retrieve" | "refer" | "recommend" | "done"

    # ── Terminal Output (set by format node) ─────────────────
    final_response: dict[str, Any] | None