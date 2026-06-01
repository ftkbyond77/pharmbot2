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

    # ── LLM ──────────────────────────────────────────────────
    gemini_api_key: str
    gemini_model: str = "gemini-3.1-flash-lite"

    # ── LiteLLM fix (must be set before openkb imports) ──────
    litellm_drop_params: str = "True"

    # ── Qdrant ───────────────────────────────────────────────
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "pharmbot_guidelines"

    # ── OpenKB (pip library — local cache dir, no server) ────
    openkb_cache_dir: str = ".openkb_cache"

    # ── Embeddings ───────────────────────────────────────────
    embedding_model: str = "BAAI/bge-m3"
    embedding_device: str = "cuda"

    # ── Agent thresholds ─────────────────────────────────────
    max_clarify_rounds: int = 3
    completeness_threshold: float = 0.6   # 0.0–1.0 score

    # ── Session ──────────────────────────────────────────────
    session_max: int = 200
    session_ttl_minutes: int = 30

    # ── API server ───────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8000


@lru_cache
def get_settings() -> Settings:
    """Singleton — call get_settings() anywhere in the app."""
    settings = Settings()
    # Apply LiteLLM env var immediately after load (Gemini fix)
    os.environ["LITELLM_DROP_PARAMS"] = settings.litellm_drop_params
    return settings