"""
routers/health.py
-----------------
GET /health  — liveness probe (Docker / k8s)
GET /ready   — readiness probe (checks Qdrant + session store)
"""

from fastapi import APIRouter
from qdrant_client import QdrantClient

from api.config import get_settings
from api.session.memory import get_store

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    """Liveness: always 200 if process is running."""
    return {"status": "ok"}


@router.get("/ready")
async def ready():
    """
    Readiness: verify downstream dependencies are reachable.
    Returns 200 if all checks pass, 503 otherwise.
    """
    cfg = get_settings()
    checks: dict[str, str] = {}

    # ── Qdrant ────────────────────────────────────────────────
    try:
        client = QdrantClient(url=cfg.qdrant_url, api_key=cfg.qdrant_api_key, timeout=3)
        collections = client.get_collections().collections
        checks["qdrant"] = f"ok ({len(collections)} collections)"
    except Exception as e:
        checks["qdrant"] = f"error: {e}"

    # ── Session store ─────────────────────────────────────────
    try:
        store = get_store()
        checks["session_store"] = f"ok ({store.active_count} active sessions)"
    except Exception as e:
        checks["session_store"] = f"error: {e}"

    all_ok = all("error" not in v for v in checks.values())

    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={"status": "ready" if all_ok else "degraded", "checks": checks},
    )