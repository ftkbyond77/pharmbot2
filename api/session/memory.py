"""
session/memory.py
-----------------
LRU in-memory session store.

Design goals:
- OOM-safe: max N sessions, evict oldest when full
- TTL: expire idle sessions after X minutes
- Redis-ready: same get/set/delete interface → Phase 3 drop-in
"""

import time
from collections import OrderedDict
from typing import Any

from loguru import logger


class SessionStore:
    """
    Thread-safe* LRU session store.

    * FastAPI runs in async context with a single event loop thread,
      so no lock is needed for standard usage. Add asyncio.Lock if
      you ever use run_in_executor with threads.
    """

    def __init__(self, max_sessions: int = 200, ttl_minutes: int = 30) -> None:
        self._store: OrderedDict[str, dict] = OrderedDict()
        self._max = max_sessions
        self._ttl = ttl_minutes * 60  # convert to seconds
        logger.info(
            f"SessionStore initialised — max={max_sessions}, ttl={ttl_minutes}min"
        )

    # ── public interface (mirrors Redis semantics) ────────────

    def get(self, session_id: str) -> dict[str, Any] | None:
        """Return session state or None if missing/expired."""
        item = self._store.get(session_id)
        if item is None:
            return None

        if self._is_expired(item):
            del self._store[session_id]
            logger.debug(f"Session expired: {session_id}")
            return None

        # Move to end = most-recently-used
        self._store.move_to_end(session_id)
        return item["state"]

    def set(self, session_id: str, state: dict[str, Any]) -> None:
        """Upsert session state. Evicts oldest if at capacity."""
        if session_id in self._store:
            self._store.move_to_end(session_id)
        elif len(self._store) >= self._max:
            evicted_id, _ = self._store.popitem(last=False)
            logger.debug(f"Session evicted (LRU): {evicted_id}")

        self._store[session_id] = {"state": state, "ts": time.time()}

    def delete(self, session_id: str) -> None:
        """Explicitly remove a session (e.g. user logout)."""
        self._store.pop(session_id, None)

    def exists(self, session_id: str) -> bool:
        """Check existence without updating LRU order."""
        item = self._store.get(session_id)
        if item is None:
            return False
        if self._is_expired(item):
            del self._store[session_id]
            return False
        return True

    # ── introspection helpers ─────────────────────────────────

    @property
    def active_count(self) -> int:
        """Number of non-expired sessions currently held."""
        now = time.time()
        return sum(
            1 for item in self._store.values()
            if (now - item["ts"]) <= self._ttl
        )

    # ── private ───────────────────────────────────────────────

    def _is_expired(self, item: dict) -> bool:
        return (time.time() - item["ts"]) > self._ttl


# ── module-level singleton ─────────────────────────────────────
# Imported once at startup; config values injected from settings.
_store: SessionStore | None = None


def init_session_store(max_sessions: int, ttl_minutes: int) -> None:
    """Call once from main.py lifespan."""
    global _store
    _store = SessionStore(max_sessions=max_sessions, ttl_minutes=ttl_minutes)


def get_store() -> SessionStore:
    """FastAPI dependency — returns the singleton store."""
    if _store is None:
        raise RuntimeError("SessionStore not initialised. Call init_session_store() first.")
    return _store