"""
agent/state.py
--------------
Single source of truth for all data flowing through the graph.
LangGraph merges partial dicts returned by each node into the state.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal

from typing_extensions import TypedDict


# ── sub-types ──────────────────────────────────────────────────

class DDxItem(TypedDict):
    name:       str
    confidence: Literal["high", "medium", "low"]


class RetrievedChunk(TypedDict):
    text:   str
    source: str    # e.g. "ARIA Guideline 2022, p.14 [docling]"
    score:  float


# ── main state ─────────────────────────────────────────────────

class AgentState(TypedDict):

    # ── conversation ─────────────────────────────────────────
    session_id:   str
    user_message: str                                      # latest user input
    history:      Annotated[list[dict], operator.add]      # append-only

    # ── intent classification ────────────────────────────────
    intent: Literal["symptom", "drug_info", "general_pharma", "unknown"]

    # ── clarification loop ───────────────────────────────────
    clarify_round:         int           # 0–max_clarify_rounds
    completeness_score:    float         # 0.0–1.0
    clarifying_question:   str | None    # question to ask user

    # ── retrieval ────────────────────────────────────────────
    retrieved_chunks: list[RetrievedChunk]

    # ── clinical reasoning ───────────────────────────────────
    symptom_summary:         list[str]       # extracted symptom list
    differential_diagnosis:  list[DDxItem]
    clinical_rationale:      list[str]       # human-readable rationale
    red_flags_found:         list[str]       # empty = all clear

    # ── recommendation ───────────────────────────────────────
    recommendation:    str | None
    sources:           list[str]
    refer_to_doctor:   bool
    refer_reason:      str | None

    # ── recommendation extras (from augmented generation) ────
    _first_line_drug:  str | None           # e.g. "paracetamol 500mg"
    _alternatives:     list[str]            # when allergic / contraindicated

    # ── flow control ─────────────────────────────────────────
    next_action: str   # "clarify" | "retrieve" | "refer" | "recommend" | "done"

    # ── terminal output (set by format node) ─────────────────
    final_response: dict[str, Any] | None