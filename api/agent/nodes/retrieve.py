"""
agent/nodes/retrieve.py
-----------------------
Node 3: Semantic RAG retrieval

Improvements:
- Composite query uses symptom_summary (if available from prior clarify rounds)
  rather than just raw user_message — more precise semantic search
- Calls retriever.search() with expand=True (query expansion)
- Reranking done inside retriever — node stays thin
- All thresholds from config

Input  : state.user_message, state.history, state.intent,
         state.symptom_summary (optional, filled by prior rounds)
Output : state.retrieved_chunks, state.next_action
"""

from __future__ import annotations

from loguru import logger

from api.agent.state import AgentState
from api.config import get_settings

_HISTORY_WINDOW = 4  # how many recent user turns to include in query


def retrieve_node(state: AgentState) -> dict:
    from api.knowledge.retriever import get_retriever

    cfg      = get_settings()
    retriever = get_retriever()

    # ── build composite query ─────────────────────────────────
    query = _build_query(state)
    logger.info(f"[retrieve] query='{query[:120]}' intent={state.get('intent')}")

    # ── optional: category filter from intent ─────────────────
    # Phase 2: map intent/DDx → disease_cat filter
    # For now keep None so we search across all categories
    disease_cat = None
    lang        = None  # BGE-M3 is bilingual — no filter needed

    chunks = retriever.search(
        query=query,
        top_k=cfg.retrieval_top_k,
        final_k=cfg.retrieval_final_k,
        disease_cat=disease_cat,
        lang=lang,
        score_threshold=cfg.retrieval_score_threshold,
        expand=cfg.query_expansion_enabled,
    )

    if not chunks:
        logger.warning("[retrieve] 0 chunks returned — proceeding with empty context")
    else:
        logger.info(
            f"[retrieve] {len(chunks)} chunks | "
            f"top_score={chunks[0]['score']} | "
            f"sources={[c['source'] for c in chunks[:2]]}"
        )

    return {
        "retrieved_chunks": chunks,
        "next_action":      "clinical_reason",
    }


def _build_query(state: AgentState) -> str:
    """
    Build a rich query string:
    1. Use symptom_summary if already populated (from prior clarify round)
    2. Fall back to recent user turns + latest message
    """
    # If clinical reason already ran in a prior turn, use its summary
    symptom_summary: list[str] = state.get("symptom_summary", [])
    if symptom_summary:
        base = " | ".join(symptom_summary)
        logger.debug(f"[retrieve] using symptom_summary: {base[:80]}")
        return base

    # Otherwise: last N user turns + current message
    history = state.get("history", [])
    user_turns = [
        h["content"]
        for h in history
        if h.get("role") == "user"
    ][-_HISTORY_WINDOW:]

    parts = user_turns + [state["user_message"]]
    return " ".join(p.strip() for p in parts if p.strip())