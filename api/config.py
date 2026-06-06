"""
api/config.py
-------------
All runtime configuration loaded from .env / environment variables.
No hard-coded values outside this file — every tunable is here.

Sections:
  - LLM (chat agent)
  - LLM (ingest vision — separate model, only used during ingestion)
  - Qdrant
  - Embeddings
  - Ingestion pipeline
  - Retrieval
  - Reranker
  - Agent thresholds
  - Session
  - API server
"""

import os
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── LLM (chat agent) ──────────────────────────────────────
    gemini_api_key: str
    gemini_model: str           = "gemini-3.1-flash-lite"
    litellm_drop_params: str    = "True"   # required for Gemini via LangChain
    llm_temp_classify: float    = 0.0
    llm_temp_clarify: float     = 0.2
    llm_temp_clinical: float    = 0.1
    llm_temp_safety: float      = 0.0
    llm_temp_recommend: float   = 0.3

    # ── LLM (ingestion — vision model for charts/diagrams) ────
    # Used ONLY at ingest time — never loaded at runtime
    ingest_vision_model: str              = "gemini-2.5-pro"
    ingest_vision_max_tokens: int         = 1024
    # Page-level text confidence below this → send page image to vision LLM
    ingest_vision_confidence_threshold: float = 0.4
    # DPI for rendering PDF pages as images (higher = better, more memory)
    ingest_vision_dpi: int                = 150

    # ── Qdrant ───────────────────────────────────────────────
    qdrant_url: str         = "http://localhost:6333"
    qdrant_collection: str  = "pharmbot_guidelines"

    # ── Embeddings ───────────────────────────────────────────
    embedding_model: str    = "BAAI/bge-m3"
    embedding_device: str   = "cuda"
    vector_dim: int         = 1024

    # ── Ingestion pipeline ────────────────────────────────────
    chunk_size: int             = 400
    chunk_overlap: int          = 60
    ingest_batch_size: int      = 16
    docling_device: str         = "cuda"
    ingest_batch_pages: int        = 15    # pages per docling OCR batch (OOM guard)
    ingest_min_chunks_ratio: float = 0.20  # below this ratio → trigger fallback
    # Tier 1 (full-file docling) is skipped for PDFs larger than this.
    # Large files cause std::bad_alloc in layout model → go straight to batched OCR.
    ingest_tier1_max_pages: int    = 20    # e.g. AAFP=9 (runs T1), Thai URI=72 (skips T1)

    # ── Retrieval ─────────────────────────────────────────────
    retrieval_top_k: int                = 8
    retrieval_final_k: int              = 4
    retrieval_score_threshold: float    = 0.25
    query_expansion_enabled: bool       = True
    query_expansion_count: int          = 2

    # ── Reranker ──────────────────────────────────────────────
    reranker_enabled: bool  = True
    reranker_model: str     = "ms-marco-MiniLM-L-12-v2"

    # ── Agent thresholds ─────────────────────────────────────
    max_clarify_rounds: int         = 3
    completeness_threshold: float   = 0.65
    no_clarify_intents: list[str]   = ["drug_info"]

    # ── Session ──────────────────────────────────────────────
    session_max: int         = 200
    session_ttl_minutes: int = 30

    # ── API server ───────────────────────────────────────────
    api_host: str           = "0.0.0.0"
    api_port: int           = 8000
    cors_origins: list[str] = ["*"]


@lru_cache
def get_settings() -> Settings:
    """Singleton — call get_settings() anywhere in the app."""
    s = Settings()
    # Apply immediately so LangChain/LiteLLM picks it up before any LLM call
    os.environ["LITELLM_DROP_PARAMS"] = s.litellm_drop_params
    return s