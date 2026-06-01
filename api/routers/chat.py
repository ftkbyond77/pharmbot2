"""
routers/chat.py
---------------
POST /chat              — main chat endpoint
GET  /chat/{session_id}/history — retrieve session history
DELETE /chat/{session_id}       — clear session
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from loguru import logger

from api.agent.graph import get_graph
from api.agent.state import AgentState
from api.session.memory import SessionStore, get_store

router = APIRouter(prefix="/chat", tags=["chat"])


# ── request / response schemas ─────────────────────────────────

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    session_id: str | None = Field(
        default=None,
        description="Omit on first message — server will generate one",
    )


class DiagnosisItem(BaseModel):
    name: str
    confidence: str   # "high" | "medium" | "low"


class ChatResponse(BaseModel):
    session_id: str
    type: str                           # "clarify" | "recommendation" | "refer"
    message: str
    diagnosis: list[DiagnosisItem] = []
    recommendation: str | None = None
    sources: list[str] = []
    red_flags: list[str] = []
    refer_to_doctor: bool = False
    clarifying_question: str | None = None


# ── helpers ────────────────────────────────────────────────────

def _get_or_create_session(
    session_id: str | None,
    store: SessionStore,
) -> tuple[str, dict[str, Any]]:
    """Return (session_id, state_dict). Creates new session if needed."""
    if session_id and store.exists(session_id):
        state = store.get(session_id)
        return session_id, state

    sid = session_id or str(uuid.uuid4())
    state = _empty_state(sid)
    return sid, state


def _empty_state(session_id: str) -> dict[str, Any]:
    return {
        "session_id":             session_id,
        "user_message":           "",
        "history":                [],
        "intent":                 "unknown",
        "clarify_round":          0,
        "completeness_score":     0.0,
        "clarifying_question":    None,
        "retrieved_chunks":       [],
        "symptom_summary":        [],
        "differential_diagnosis": [],
        "clinical_rationale":     [],
        "red_flags_found":        [],
        "recommendation":         None,
        "sources":                [],
        "refer_to_doctor":        False,
        "refer_reason":           None,
        "next_action":            "clarify",
        "final_response":         None,
    }


def _append_history(state: dict, role: str, content: str) -> None:
    """Mutate state history in-place (before graph run)."""
    state["history"].append({"role": role, "content": content})


# ── endpoints ──────────────────────────────────────────────────

@router.post("", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    store: SessionStore = Depends(get_store),
):
    sid, state = _get_or_create_session(req.session_id, store)

    # append user turn BEFORE running graph (graph reads history)
    _append_history(state, "user", req.message)
    state["user_message"] = req.message

    logger.info(f"[chat] session={sid} round={state['clarify_round']} msg='{req.message[:60]}'")

    # ── run LangGraph ─────────────────────────────────────────
    graph = get_graph()
    try:
        result: dict[str, Any] = await graph.ainvoke(state)
    except Exception as e:
        logger.error(f"[chat] graph error: {e}")
        raise HTTPException(status_code=500, detail="เกิดข้อผิดพลาดภายใน กรุณาลองใหม่อีกครั้ง")

    # ── extract final_response ────────────────────────────────
    final: dict[str, Any] = result.get("final_response") or {}
    bot_message = final.get("message", "ขออภัย ไม่สามารถประมวลผลได้ในขณะนี้")

    # append assistant turn to history
    _append_history(result, "assistant", bot_message)

    # ── persist updated state ─────────────────────────────────
    store.set(sid, result)

    # ── build response ────────────────────────────────────────
    return ChatResponse(
        session_id=sid,
        type=final.get("type", "recommendation"),
        message=bot_message,
        diagnosis=[DiagnosisItem(**d) for d in final.get("diagnosis", [])],
        recommendation=final.get("recommendation"),
        sources=final.get("sources", []),
        red_flags=final.get("red_flags", []),
        refer_to_doctor=final.get("refer_to_doctor", False),
        clarifying_question=final.get("clarifying_question"),
    )


@router.get("/{session_id}/history")
async def get_history(
    session_id: str,
    store: SessionStore = Depends(get_store),
):
    state = store.get(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    return {
        "session_id": session_id,
        "history":    state.get("history", []),
        "rounds":     state.get("clarify_round", 0),
    }


@router.delete("/{session_id}")
async def clear_session(
    session_id: str,
    store: SessionStore = Depends(get_store),
):
    store.delete(session_id)
    return {"session_id": session_id, "status": "cleared"}


# ── routers/__init__.py ────────────────────────────────────────