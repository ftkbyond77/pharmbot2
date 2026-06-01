"""
agent/graph.py
--------------
LangGraph StateGraph — wires all nodes + conditional edges.

Flow diagram:
                        ┌─────────┐
                        │  START  │
                        └────┬────┘
                             │
                        ┌────▼─────┐
                        │ classify │
                        └────┬─────┘
                             │
              ┌──────────────▼──────────────┐
              │  intent == drug_info?        │
              │  yes → retrieve              │
              │  no  → clarify               │
              └──────────────┬──────────────┘
                             │
                   ┌─────────▼──────────┐
                   │      clarify        │◄──────────────┐
                   └─────────┬──────────┘               │
                             │                           │
              ┌──────────────▼──────────────┐            │
              │  next_action == clarify?    │            │
              │  yes → END (ask user)  ─────┼── user     │
              │  no  → retrieve             │  replies ──┘
              └──────────────┬──────────────┘
                             │
                   ┌─────────▼──────────┐
                   │      retrieve       │
                   └─────────┬──────────┘
                             │
                   ┌─────────▼──────────┐
                   │  clinical_reason    │
                   └─────────┬──────────┘
                             │
                   ┌─────────▼──────────┐
                   │    safety_gate      │
                   └─────────┬──────────┘
                             │
              ┌──────────────▼──────────────┐
              │  refer_to_doctor?           │
              │  yes → format (refer)       │
              │  no  → recommendation       │
              └──────────────┬──────────────┘
                             │
                   ┌─────────▼──────────┐
                   │   recommendation    │
                   └─────────┬──────────┘
                             │
                   ┌─────────▼──────────┐
                   │       format        │
                   └─────────┬──────────┘
                             │
                        ┌────▼────┐
                        │   END   │
                        └─────────┘
"""

from functools import lru_cache

from langgraph.graph import END, START, StateGraph
from loguru import logger

from api.agent.state import AgentState
from api.agent.nodes import (
    classify_node,
    clarify_node,
    retrieve_node,
    clinical_reason_node,
    safety_gate_node,
    recommendation_node,
    format_node,
)


# ── conditional edge functions ─────────────────────────────────

def _after_classify(state: AgentState) -> str:
    """
    drug_info → skip clarify, go straight to retrieve.
    everything else → clarify first.
    """
    if state.get("intent") == "drug_info":
        logger.debug("[edge:classify] drug_info → retrieve")
        return "retrieve"
    logger.debug(f"[edge:classify] {state.get('intent')} → clarify")
    return "clarify"


def _after_clarify(state: AgentState) -> str:
    """
    If bot still needs more info → return 'clarify' (graph ends this
    turn; next user message re-enters at classify which routes back here).
    Otherwise → proceed to retrieve.
    """
    if state.get("next_action") == "clarify":
        logger.debug("[edge:clarify] waiting for user → END turn")
        return "end_turn"
    logger.debug("[edge:clarify] enough info → retrieve")
    return "retrieve"


def _after_safety_gate(state: AgentState) -> str:
    """
    Red flag detected → go straight to format (refer shape).
    Clear → recommendation.
    """
    if state.get("refer_to_doctor"):
        logger.debug("[edge:safety_gate] red flag → format(refer)")
        return "format"
    logger.debug("[edge:safety_gate] clear → recommendation")
    return "recommendation"


# ── graph builder ──────────────────────────────────────────────

def _build_graph() -> StateGraph:
    g = StateGraph(AgentState)

    # ── register nodes ────────────────────────────────────────
    g.add_node("classify",        classify_node)
    g.add_node("clarify",         clarify_node)
    g.add_node("retrieve",        retrieve_node)
    g.add_node("clinical_reason", clinical_reason_node)
    g.add_node("safety_gate",     safety_gate_node)
    g.add_node("recommendation",  recommendation_node)
    g.add_node("format",          format_node)

    # ── entry ─────────────────────────────────────────────────
    g.add_edge(START, "classify")

    # ── conditional: after classify ──────────────────────────
    g.add_conditional_edges(
        "classify",
        _after_classify,
        {
            "clarify":  "clarify",
            "retrieve": "retrieve",
        },
    )

    # ── conditional: after clarify ────────────────────────────
    g.add_conditional_edges(
        "clarify",
        _after_clarify,
        {
            "end_turn": "format",   # format wraps clarifying_question for client
            "retrieve": "retrieve",
        },
    )

    # ── linear: retrieve → clinical_reason → safety_gate ─────
    g.add_edge("retrieve",        "clinical_reason")
    g.add_edge("clinical_reason", "safety_gate")

    # ── conditional: after safety_gate ───────────────────────
    g.add_conditional_edges(
        "safety_gate",
        _after_safety_gate,
        {
            "format":         "format",
            "recommendation": "recommendation",
        },
    )

    # ── linear: recommendation → format → END ────────────────
    g.add_edge("recommendation", "format")
    g.add_edge("format",         END)

    return g


# ── compiled singleton ─────────────────────────────────────────

@lru_cache(maxsize=1)
def get_graph():
    """
    Compile once and cache.
    LangGraph compilation is expensive — do it at startup, not per request.
    """
    graph = _build_graph().compile()
    logger.info("LangGraph StateGraph compiled and cached")
    return graph