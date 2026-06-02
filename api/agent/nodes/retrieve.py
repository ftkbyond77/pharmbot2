"""
agent/nodes/retrieve.py
-----------------------
Node 3: RAG retrieval

Builds a rich query from history + latest message,
then fetches top-k chunks from Qdrant via BGE-M3.

Input  : state.user_message, state.history, state.intent
Output : state.retrieved_chunks, state.next_action
"""

from loguru import logger

from api.agent.state import AgentState
# from api.knowledge.retriever import get_retriever


# how many history turns to include in the composite query
_HISTORY_WINDOW = 4


def retrieve_node(state: AgentState) -> dict:
    from api.knowledge.retriever import get_retriever
    retriever = get_retriever()

    # ── build composite query ─────────────────────────────────
    query = _build_query(state)
    logger.info(f"[retrieve] query='{query[:100]}...' intent={state.get('intent')}")

    # ── pick filter based on intent ───────────────────────────
    disease_cat = None   # Phase 2: map intent → category
    lang = None          # BGE-M3 handles bilingual; skip filter for now

    chunks = retriever.search(
        query=query,
        top_k=5,
        disease_cat=disease_cat,
        lang=lang,
        score_threshold=0.30,
    )

    if not chunks:
        logger.warning("[retrieve] No chunks returned — proceeding with empty context")

    logger.info(f"[retrieve] {len(chunks)} chunks retrieved, top score="
                f"{chunks[0]['score'] if chunks else 'n/a'}")

    return {
        "retrieved_chunks": chunks,
        "next_action": "clinical_reason",
    }


def _build_query(state: AgentState) -> str:
    """
    Combine recent history turns + latest message into one query string.
    This gives BGE-M3 more context than the single latest message alone.
    """
    history = state.get("history", [])
    # take last N user turns only
    user_turns = [
        h["content"] for h in history
        if h.get("role") == "user"
    ][-_HISTORY_WINDOW:]

    parts = user_turns + [state["user_message"]]
    return " ".join(parts).strip()