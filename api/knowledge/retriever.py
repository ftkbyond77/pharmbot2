"""
knowledge/retriever.py
----------------------
Semantic retrieval from Qdrant with:
  1. Query expansion   — expand user query into N sub-queries (LLM)
  2. Multi-query search — run each sub-query against Qdrant
  3. Dedup + merge      — union of all results, deduplicated by text hash
  4. Cross-encoder rerank (flashrank) — reorder by relevance to original query
  5. Top-k return       — trimmed final list

Phase 2 hook: KG walk placeholder preserved.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from typing import Any

from loguru import logger
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue
from sentence_transformers import SentenceTransformer

from api.config import get_settings
from api.agent.state import RetrievedChunk


# ── Reranker (lazy init) ───────────────────────────────────────

_reranker = None

def _get_reranker():
    global _reranker
    if _reranker is None:
        cfg = get_settings()
        if not cfg.reranker_enabled:
            return None
        try:
            from flashrank import Ranker
            _reranker = Ranker(model_name=cfg.reranker_model, cache_dir="/tmp/flashrank")
            logger.info(f"[reranker] loaded: {cfg.reranker_model}")
        except Exception as exc:
            logger.warning(f"[reranker] failed to load, skipping rerank: {exc}")
            _reranker = None
    return _reranker


# ── Query expansion (LLM) ──────────────────────────────────────

def expand_query(original_query: str, n: int = 2) -> list[str]:
    """
    Generate n alternative phrasings of the query for broader semantic coverage.
    Falls back to [original_query] on any error.
    """
    cfg = get_settings()
    if not cfg.query_expansion_enabled or n <= 0:
        return [original_query]

    try:
        import json
        from langchain_google_genai import ChatGoogleGenerativeAI
        from api.prompts.pharmacist import strip_fences

        llm = ChatGoogleGenerativeAI(
            model=cfg.gemini_model,
            google_api_key=cfg.gemini_api_key,
            temperature=0.3,
        )
        prompt = (
            f"คุณเป็น Query Expansion Specialist สำหรับระบบค้นหาทางเภสัชกรรม\n\n"
            f"ขยาย query ต่อไปนี้เป็น {n} รูปแบบที่แตกต่างกัน ครอบคลุมคำศัพท์ทั้งภาษาไทยและอังกฤษ:\n"
            f"Original: \"{original_query}\"\n\n"
            f"ตอบด้วย JSON เท่านั้น: {{\"queries\": [\"<query 1>\", \"<query 2>\"]}}"
        )
        resp = llm.invoke([{"role": "user", "content": prompt}])
        data = json.loads(strip_fences(resp.content))
        extras: list[str] = data.get("queries", [])[:n]
        result = [original_query] + [q for q in extras if q and q != original_query]
        logger.debug(f"[expand_query] {len(result)} queries: {result}")
        return result
    except Exception as exc:
        logger.debug(f"[expand_query] failed ({exc}), using original only")
        return [original_query]


# ── Retriever class ────────────────────────────────────────────

class Retriever:
    """
    Wraps Qdrant + BGE-M3 + flashrank into a single search interface.
    Instantiate once (singleton via get_retriever()) and reuse.
    """

    def __init__(self) -> None:
        cfg = get_settings()
        logger.info(f"[retriever] init — model={cfg.embedding_model} device={cfg.embedding_device}")
        self._model      = SentenceTransformer(cfg.embedding_model, device=cfg.embedding_device)
        self._client     = QdrantClient(url=cfg.qdrant_url, api_key=cfg.qdrant_api_key)
        self._collection = cfg.qdrant_collection

    # ── public API ─────────────────────────────────────────────

    def search(
        self,
        query: str,
        top_k: int | None = None,
        final_k: int | None = None,
        disease_cat: str | None = None,
        lang: str | None = None,
        score_threshold: float | None = None,
        expand: bool = True,
    ) -> list[RetrievedChunk]:
        """
        Full semantic search pipeline:
          expand → multi-query Qdrant → dedup → rerank → return final_k

        Args:
            query           : natural language query (Thai/English)
            top_k           : candidates per sub-query from Qdrant
            final_k         : returned after rerank
            disease_cat     : optional metadata filter
            lang            : optional lang filter
            score_threshold : min cosine similarity before rerank
            expand          : whether to run query expansion
        """
        cfg = get_settings()
        top_k           = top_k          or cfg.retrieval_top_k
        final_k         = final_k        or cfg.retrieval_final_k
        score_threshold = score_threshold if score_threshold is not None \
                          else cfg.retrieval_score_threshold

        # 1. query expansion
        queries = expand_query(query, cfg.query_expansion_count) if expand else [query]

        # 2. multi-query vector search
        all_hits: dict[str, dict] = {}  # dedup by text hash
        for q in queries:
            hits = self._vector_search(q, top_k, disease_cat, lang, score_threshold)
            for hit in hits:
                key = hashlib.md5(hit["text"].encode()).hexdigest()
                if key not in all_hits:
                    all_hits[key] = hit
                else:
                    # keep highest score across queries
                    if hit["score"] > all_hits[key]["score"]:
                        all_hits[key] = hit

        candidates = list(all_hits.values())
        logger.debug(
            f"[retriever] {len(queries)} queries → {len(candidates)} unique candidates"
        )

        if not candidates:
            logger.warning(f"[retriever] 0 candidates for query: '{query[:80]}'")
            return []

        # 3. rerank
        reranked = self._rerank(query, candidates, final_k)

        logger.info(
            f"[retriever] final {len(reranked)} chunks | "
            f"top_score={reranked[0]['score'] if reranked else 'n/a'}"
        )
        return reranked

    def format_context(self, chunks: list[RetrievedChunk]) -> str:
        """
        Format chunks into a numbered prompt-ready context block.
        Each chunk is tagged with its source for downstream citation.
        """
        if not chunks:
            return "(ไม่พบข้อมูลที่เกี่ยวข้องใน guideline)"
        parts = []
        for i, chunk in enumerate(chunks, start=1):
            parts.append(f"[{i}] {chunk['source']}\n{chunk['text']}")
        return "\n\n---\n\n".join(parts)

    # ── Phase 2 hook ───────────────────────────────────────────

    def search_with_kg(
        self,
        query: str,
        top_k: int | None = None,
        # kg: NetworkXGraph  ← Phase 2 injection
    ) -> list[RetrievedChunk]:
        """
        Placeholder for hybrid retrieval (vector + KG walk).
        Phase 1: delegates to plain vector search.
        Phase 2: uncomment KG walk + merge + rerank logic.
        """
        # Phase 2:
        # kg_chunks = kg.walk(entities=extract_entities(query))
        # vector_chunks = self.search(query, top_k=top_k)
        # return self._rerank(query, vector_chunks + kg_chunks, final_k)
        return self.search(query, top_k=top_k)

    # ── private ────────────────────────────────────────────────

    def _vector_search(
        self,
        query: str,
        top_k: int,
        disease_cat: str | None,
        lang: str | None,
        score_threshold: float,
    ) -> list[dict]:
        """Single Qdrant query. Returns raw dicts (not RetrievedChunk yet)."""
        query_vec    = self._embed(query)
        qdrant_filter = self._build_filter(disease_cat=disease_cat, lang=lang)

        try:
            results = self._client.query_points(
                collection_name=self._collection,
                query=query_vec,
                limit=top_k,
                query_filter=qdrant_filter,
                score_threshold=score_threshold,
                with_payload=True,
            )
        except Exception as exc:
            logger.error(f"[retriever] Qdrant query failed: {exc}")
            return []

        hits = []
        for point in results.points:
            payload = point.payload or {}
            hits.append({
                "text":   payload.get("text", ""),
                "source": self._format_source(payload),
                "score":  round(point.score, 4),
                "payload": payload,  # keep for potential debug
            })
        return hits

    def _rerank(
        self,
        query: str,
        candidates: list[dict],
        final_k: int,
    ) -> list[RetrievedChunk]:
        """
        Cross-encoder rerank with flashrank.
        Falls back to score-sorted if reranker unavailable.
        """
        ranker = _get_reranker()

        if ranker is None or len(candidates) <= 1:
            # fallback: sort by original vector score
            sorted_candidates = sorted(candidates, key=lambda c: c["score"], reverse=True)
            return [
                RetrievedChunk(text=c["text"], source=c["source"], score=c["score"])
                for c in sorted_candidates[:final_k]
            ]

        try:
            from flashrank import RerankRequest
            passages = [{"id": i, "text": c["text"]} for i, c in enumerate(candidates)]
            request  = RerankRequest(query=query, passages=passages)
            results  = ranker.rerank(request)

            reranked: list[RetrievedChunk] = []
            for r in results[:final_k]:
                c = candidates[r["id"]]
                reranked.append(RetrievedChunk(
                    text=c["text"],
                    source=c["source"],
                    score=round(r.get("score", c["score"]), 4),
                ))
            return reranked
        except Exception as exc:
            logger.warning(f"[reranker] rerank failed ({exc}), using vector scores")
            sorted_candidates = sorted(candidates, key=lambda c: c["score"], reverse=True)
            return [
                RetrievedChunk(text=c["text"], source=c["source"], score=c["score"])
                for c in sorted_candidates[:final_k]
            ]

    def _embed(self, text: str) -> list[float]:
        return self._model.encode(
            text,
            normalize_embeddings=True,
            show_progress_bar=False,
        ).tolist()

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
        return Filter(must=conditions) if conditions else None

    @staticmethod
    def _format_source(payload: dict[str, Any]) -> str:
        """Build human-readable citation string from chunk metadata."""
        source  = payload.get("source", "Unknown").replace(".pdf", "")
        page    = payload.get("page")
        version = payload.get("guideline_version", "")
        extractor = payload.get("extractor", "")

        parts = [source]
        if version and version != "unknown":
            parts[0] += f" ({version})"
        if page:
            parts.append(f"p.{page}")
        if extractor:
            parts.append(f"[{extractor}]")
        return ", ".join(parts)


# ── module-level singleton ─────────────────────────────────────

_retriever: Retriever | None = None


def init_retriever() -> None:
    """Call once from main.py lifespan — loads model into memory."""
    global _retriever
    _retriever = Retriever()
    # warm up reranker too
    _get_reranker()
    logger.info("[retriever] ready")


def get_retriever() -> Retriever:
    """FastAPI dependency — returns the singleton retriever."""
    if _retriever is None:
        raise RuntimeError("Retriever not initialised. Call init_retriever() first.")
    return _retriever