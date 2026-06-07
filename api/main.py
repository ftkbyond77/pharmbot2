"""
api/main.py
-----------
FastAPI application entry point.

Startup sequence (lifespan):
  1. Load settings (sets LITELLM_DROP_PARAMS env var)
  2. Init SessionStore singleton
  3. Init Retriever singleton  (loads BGE-M3 model into memory)
  4. Warm-up LangGraph compile (get_graph())

Shutdown: nothing special needed for Phase 1
"""

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from api.config import get_settings
from api.session.memory import init_session_store
from api.knowledge.retriever import init_retriever
from api.agent.graph import get_graph
from api.routers import chat_router, health_router, test_cases_router



# ── lifespan ───────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── startup ───────────────────────────────────────────────
    cfg = get_settings()
    logger.info(f"Starting PharmBot API — model={cfg.gemini_model}")

    logger.info("Initialising session store...")
    init_session_store(
        max_sessions=cfg.session_max,
        ttl_minutes=cfg.session_ttl_minutes,
    )

    logger.info("Initialising retriever (loading BGE-M3)...")
    init_retriever()

    logger.info("Compiling LangGraph...")
    get_graph()   # compile + cache now, not on first request

    logger.info("PharmBot API ready ✓")
    yield

    # ── shutdown ──────────────────────────────────────────────
    logger.info("PharmBot API shutting down")


# ── app factory ────────────────────────────────────────────────

def create_app() -> FastAPI:
    cfg = get_settings()

    app = FastAPI(
        title="PharmBot API",
        description="เภสัชกร AI — RAG-powered clinical assistant",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # ── CORS ──────────────────────────────────────────────────
    # Phase 1: open for local dev; lock down origins in production
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── routers ───────────────────────────────────────────────
    app.include_router(health_router)
    app.include_router(chat_router, prefix="/api/v1")
    app.include_router(test_cases_router, prefix="/api/v1")

    return app


app = create_app()


# ── CLI entry ──────────────────────────────────────────────────

if __name__ == "__main__":
    cfg = get_settings()
    uvicorn.run(
        "api.main:app",
        host=cfg.api_host,
        port=cfg.api_port,
        reload=True,           # dev only — remove in production
        log_level="info",
    )