"""
agent/graph.py
--------------
LangGraph StateGraph — wires all nodes + conditional edges.

Flow:
    START
      │
   classify
      │
   ┌──┴──────────────────┐
   │ intent in            │
   │ no_clarify_intents?  │
   │ yes → retrieve       │
   │ no  → clarify        │
   └─────────────────────┘
           │
        clarify ◄──── user re-enters here on next message
           │
   ┌───────┴────────────────────┐
   │ next_action == "clarify"?  │
   │ yes → format (end turn,    │
   │        ask user)           │
   │ no  → retrieve             │
   └────────────────────────────┘
           │
        retrieve
           │
     clinical_reason
           │
       safety_gate
           │
   ┌───────┴────────────────────┐
   │ refer_to_doctor?           │
   │ yes → format (refer)       │
   │ no  → recommendation       │
   └────────────────────────────┘
           │
     recommendation
           │
         format
           │
          END
"""

from __future__ import annotations

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
    followup_node,       
)


# ── conditional edge functions ─────────────────────────────────

def _after_classify(state: AgentState) -> str:
    action = state.get("next_action", "clarify")
    logger.debug(f"[edge:after_classify] next_action={action}")
    # valid: "clarify" | "retrieve" | "followup" | "respond"
    return action


def _after_clarify(state: AgentState) -> str:
    """
    If bot still needs info → end this turn (ask user).
    Otherwise → retrieve.

    Note: next message from user re-enters at START → classify → clarify
    (because classify node preserves clarify_round from state).
    """
    action = state.get("next_action", "retrieve")
    if action == "clarify":
        logger.debug("[edge:after_clarify] waiting for user → end_turn")
        return "end_turn"
    logger.debug("[edge:after_clarify] info sufficient → retrieve")
    return "retrieve"


def _after_safety_gate(state: AgentState) -> str:
    if state.get("refer_to_doctor", False):
        logger.debug("[edge:after_safety_gate] red flag detected → format (refer)")
        return "format"
    logger.debug("[edge:after_safety_gate] clear → recommendation")
    return "recommendation"


# ── graph builder ──────────────────────────────────────────────

def _build_graph() -> StateGraph:
    g = StateGraph(AgentState)
 
    g.add_node("classify",        classify_node)
    g.add_node("clarify",         clarify_node)
    g.add_node("retrieve",        retrieve_node)
    g.add_node("clinical_reason", clinical_reason_node)
    g.add_node("safety_gate",     safety_gate_node)
    g.add_node("recommendation",  recommendation_node)
    g.add_node("format",          format_node)
    g.add_node("followup",        followup_node)  
 
    g.add_edge(START, "classify")
 
    g.add_conditional_edges(
        "classify",
        _after_classify,
        {
            "clarify":  "clarify",
            "retrieve": "retrieve",
            "followup": "followup",   
        },
    )
 
    g.add_conditional_edges(
        "clarify",
        _after_clarify,
        {
            "end_turn": "format",
            "retrieve": "retrieve",
        },
    )
 
    g.add_edge("retrieve",        "clinical_reason")
    g.add_edge("clinical_reason", "safety_gate")
 
    g.add_conditional_edges(
        "safety_gate",
        _after_safety_gate,
        {
            "format":         "format",
            "recommendation": "recommendation",
        },
    )
 
    g.add_edge("recommendation", "format")
    g.add_edge("followup",       "format")   
    g.add_edge("format",         END)
 
    return g
 


# ── compiled singleton ─────────────────────────────────────────

@lru_cache(maxsize=1)
def get_graph():
    """Compile once and cache (expensive). Called at startup by lifespan."""
    graph = _build_graph().compile()
    logger.info("[graph] LangGraph StateGraph compiled and cached")
    return graph