"""
knowledge/retriever.py
----------------------
Semantic retrieval from Qdrant using BGE-M3 embeddings.

Phase 1: pure vector search + optional metadata filter
Phase 2: hybrid (vector + NetworkX KG walk) — slots already here

Usage:
    retriever = Retriever()
    chunks = retriever.search("ไอเรื้อรัง น้ำมูกใส", top_k=5)
"""

from functools import lru_cache
from typing import Any

from loguru import logger
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue
from sentence_transformers import SentenceTransformer

from api.config import get_settings
from api.agent.state import RetrievedChunk


class Retriever:
    """
    Wraps Qdrant + BGE-M3 into a single search interface.
    Instantiate once (singleton via get_retriever()) and reuse.
    """

    def __init__(self) -> None:
        cfg = get_settings()
        logger.info(f"Retriever init — model={cfg.embedding_model}, device={cfg.embedding_device}")
        self._model = SentenceTransformer(cfg.embedding_model, device=cfg.embedding_device)
        self._client = QdrantClient(url=cfg.qdrant_url)
        self._collection = cfg.qdrant_collection

    # ── public API ─────────────────────────────────────────────

    def search(
        self,
        query: str,
        top_k: int = 5,
        disease_cat: str | None = None,
        lang: str | None = None,
        score_threshold: float = 0.35,
    ) -> list[RetrievedChunk]:
        """
        Semantic search. Returns top_k chunks above score_threshold.

        Args:
            query          : natural language query (Thai or English)
            top_k          : number of results to return
            disease_cat    : optional metadata filter (e.g. "respiratory")
            lang           : optional lang filter ("th" | "en")
            score_threshold: minimum cosine similarity to include
        """
        query_vec = self._embed(query)
        qdrant_filter = self._build_filter(disease_cat=disease_cat, lang=lang)

        from qdrant_client.models import Query
        results = self._client.query_points(
            collection_name=self._collection,
            query=query_vec,
            limit=top_k,
            query_filter=qdrant_filter,
            score_threshold=score_threshold,
            with_payload=True,
        )

        chunks: list[RetrievedChunk] = []
        for hit in results.points:
            payload = hit.payload or {}
            source = self._format_source(payload)
            chunks.append(
                RetrievedChunk(
                    text=payload.get("text", ""),
                    source=source,
                    score=round(hit.score, 4),
                )
            )

        logger.debug(f"Retrieved {len(chunks)} chunks for query: '{query[:60]}...'")
        return chunks

    def format_context(self, chunks: list[RetrievedChunk]) -> str:
        """
        Flatten chunks into a prompt-ready context block.
        Each chunk tagged with its source for citation.
        """
        if not chunks:
            return "(ไม่พบข้อมูลที่เกี่ยวข้องใน guideline)"

        parts = []
        for i, chunk in enumerate(chunks, start=1):
            parts.append(
                f"[{i}] {chunk['source']}\n{chunk['text']}"
            )
        return "\n\n---\n\n".join(parts)

    # ── Phase 2 hook (KG walk) ────────────────────────────────

    def search_with_kg(
        self,
        query: str,
        top_k: int = 5,
        # kg: NetworkXGraph  ← injected Phase 2
    ) -> list[RetrievedChunk]:
        """
        Placeholder for hybrid retrieval (vector + KG).
        Phase 1: delegates to plain vector search.
        Phase 2: uncomment KG walk + merge + rerank.
        """
        # Phase 2 example:
        # kg_chunks = kg.walk(entities=extract_entities(query))
        # vector_chunks = self.search(query, top_k=top_k)
        # return rerank(vector_chunks + kg_chunks)[:top_k]
        return self.search(query, top_k=top_k)

    # ── private ────────────────────────────────────────────────

    def _embed(self, text: str) -> list[float]:
        vec = self._model.encode(
            text,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vec.tolist()

    def _build_filter(
        self,
        disease_cat: str | None,
        lang: str | None,
    ) -> Filter | None:
        conditions = []
        if disease_cat:
            conditions.append(
                FieldCondition(key="disease_cat", match=MatchValue(value=disease_cat))
            )
        if lang:
            conditions.append(
                FieldCondition(key="lang", match=MatchValue(value=lang))
            )
        if not conditions:
            return None
        return Filter(must=conditions)

    @staticmethod
    def _format_source(payload: dict[str, Any]) -> str:
        """Build human-readable citation string from chunk metadata."""
        source = payload.get("source", "Unknown")
        page = payload.get("page")
        version = payload.get("guideline_version", "")

        parts = [source.replace(".pdf", "")]
        if version and version != "unknown":
            parts[0] += f" {version}"
        if page:
            parts.append(f"p.{page}")
        return ", ".join(parts)


# ── module-level singleton ─────────────────────────────────────
_retriever: Retriever | None = None


def init_retriever() -> None:
    """Call once from main.py lifespan — loads model into memory."""
    global _retriever
    _retriever = Retriever()
    logger.info("Retriever ready")


def get_retriever() -> Retriever:
    """FastAPI dependency — returns the singleton retriever."""
    if _retriever is None:
        raise RuntimeError("Retriever not initialised. Call init_retriever() first.")
    return _retriever