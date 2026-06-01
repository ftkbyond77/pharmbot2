"""
agent/state.py
--------------
Single source of truth for everything that flows through the graph.

Every node reads from and writes to AgentState.
LangGraph merges partial dicts returned by each node into the state.
"""

from typing import Annotated, Any, Literal
from typing_extensions import TypedDict
import operator


# ── sub-types ──────────────────────────────────────────────────

class DDxItem(TypedDict):
    name: str
    confidence: Literal["high", "medium", "low"]


class RetrievedChunk(TypedDict):
    text: str
    source: str      # e.g. "ARIA Guideline 2022, p.14"
    score: float


# ── main state ─────────────────────────────────────────────────

class AgentState(TypedDict):
    # ── conversation ─────────────────────────────────────
    session_id: str
    user_message: str                      # latest user input
    history: Annotated[list[dict], operator.add]  # accumulate turns

    # ── intent classification ────────────────────────────
    intent: Literal["symptom", "drug_info", "general_pharma", "unknown"]

    # ── clarification loop ───────────────────────────────
    clarify_round: int                     # 0–3
    completeness_score: float              # 0.0–1.0
    clarifying_question: str | None        # question to ask user

    # ── retrieval ────────────────────────────────────────
    retrieved_chunks: list[RetrievedChunk]

    # ── clinical reasoning ───────────────────────────────
    symptom_summary: list[str]             # extracted symptom list
    differential_diagnosis: list[DDxItem]
    clinical_rationale: list[str]          # human-readable, CoT hidden
    red_flags_found: list[str]             # empty = clear

    # ── output ───────────────────────────────────────────
    recommendation: str | None
    sources: list[str]
    refer_to_doctor: bool
    refer_reason: str | None

    # ── flow control ─────────────────────────────────────
    # "clarify" | "retrieve" | "refer" | "recommend" | "done"
    next_action: str

    # ── raw final response (set by format node) ──────────
    final_response: dict[str, Any] | None