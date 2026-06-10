"""
routers/chat.py  (v4)
------------------------------
POST   /chat                        — main chat endpoint
GET    /chat/{session_id}/history   — retrieve session history
DELETE /chat/{session_id}           — clear session

CHANGES v4:
- _empty_state: เพิ่ม user_lang: "th"  (default Thai)
  classify_node จะ overwrite ค่านี้ทุกครั้งที่ user ส่งข้อความ
- ไม่มีการเปลี่ยนแปลงอื่น (backward compatible กับ v3)
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from api.agent.graph import get_graph
from api.agent.state import AgentState
from api.session.memory import SessionStore, get_store

router = APIRouter(prefix="/chat", tags=["chat"])


# ── Schemas ────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message:    str       = Field(..., min_length=1, max_length=2000)
    session_id: str | None = Field(
        default=None,
        description="Omit on first message — server generates one",
    )


class DiagnosisItem(BaseModel):
    name:       str
    confidence: str           # "high" | "medium" | "low"
    reasoning:  str = ""


class ChatResponse(BaseModel):
    session_id:           str
    type:                 str               # "clarify" | "normal" | "refer"
    message:              str
    diagnosis:            list[DiagnosisItem] = []
    recommendation:       str | None = None
    sources:              list[str]  = []
    red_flags:            list[str]  = []
    refer_to_doctor:      bool = False
    clarifying_question:  str | None = None

    # v2 extras
    first_line_drug:      str | None = None
    alternatives:         list[str]  = []
    diagnosis_flow:       str | None = None
    antibiotic_indicated: bool = False
    pushback_message:     str | None = None
    supportive_care:      list[str]  = []
    when_to_see_doctor:   str | None = None
    clinical_scores:      dict | None = None
    augmented_notes:      str | None = None


# ── Helpers ────────────────────────────────────────────────────

def _get_or_create_session(
    session_id: str | None,
    store: SessionStore,
) -> tuple[str, dict[str, Any]]:
    if session_id and store.exists(session_id):
        state = store.get(session_id)
        logger.debug(f"[chat] resumed session={session_id[:8]} rounds={state.get('clarify_round')}")
        return session_id, state

    sid   = session_id or str(uuid.uuid4())
    state = _empty_state(sid)
    logger.debug(f"[chat] new session={sid[:8]}")
    return sid, state


def _empty_state(session_id: str) -> dict[str, Any]:
    return {
        # conversation
        "session_id":             session_id,
        "user_message":           "",
        "history":                [],

        # intent
        "intent":                 "unknown",
        "topic_shift":            False,
        "user_lang":              "th",     # v4: default Thai; overwritten by classify_node

        # clarify
        "clarify_round":          0,
        "completeness_score":     0.0,
        "clarifying_question":    None,
        "symptom_domain":         "general",
        "symptom_complexity":     "moderate",

        # retrieval
        "retrieved_chunks":       [],

        # clinical reason
        "symptom_summary":        [],
        "differential_diagnosis": [],
        "clinical_rationale":     [],
        "red_flags_found":        [],
        "knowledge_gaps":         [],
        "clinical_scores":        {},

        # negative case
        "needs_pushback":         False,
        "pushback_reason":        None,

        # allergy flag (v3)
        "allergy_detail_incomplete": False,

        # safety gate
        "refer_to_doctor":        False,
        "refer_reason":           None,

        # recommendation
        "recommendation":         None,
        "sources":                [],
        "first_line_drug":        None,
        "alternatives":           [],
        "when_to_see_doctor":     None,

        # recommendation extras
        "diagnosis_flow":         None,
        "antibiotic_indicated":   False,
        "supportive_care":        [],
        "pushback_message":       None,
        "augmented_notes":        None,

        # flow control
        "next_action":            "clarify",

        # terminal
        "final_response":         None,
    }


def _append_history(state: dict, role: str, content: str) -> None:
    state.setdefault("history", [])
    state["history"].append({"role": role, "content": content})


# ── Endpoints ──────────────────────────────────────────────────

@router.post("", response_model=ChatResponse)
async def chat(
    req:   ChatRequest,
    store: SessionStore = Depends(get_store),
):
    sid, state = _get_or_create_session(req.session_id, store)

    # append user turn BEFORE graph (graph reads history)
    _append_history(state, "user", req.message)
    state["user_message"] = req.message

    logger.info(
        f"[chat] session={sid[:8]} round={state.get('clarify_round', 0)} "
        f"msg='{req.message[:80]}'"
    )

    graph = get_graph()
    try:
        result: dict[str, Any] = await graph.ainvoke(state)
    except Exception as exc:
        logger.exception(f"[chat] graph error: {exc}")
        raise HTTPException(status_code=500, detail="เกิดข้อผิดพลาดภายใน กรุณาลองใหม่อีกครั้ง")

    final: dict[str, Any] = result.get("final_response") or {}
    bot_message = final.get("message", "ขออภัย ไม่สามารถประมวลผลได้ในขณะนี้")

    _append_history(result, "assistant", bot_message)

    # persist updated state
    store.set(sid, result)

    # Build DiagnosisItem list
    diagnosis_items = []
    for d in final.get("diagnosis", []):
        if isinstance(d, dict):
            diagnosis_items.append(DiagnosisItem(
                name=d.get("name", ""),
                confidence=d.get("confidence", "low"),
                reasoning=d.get("reasoning", ""),
            ))

    return ChatResponse(
        session_id           = sid,
        type                 = final.get("type", "normal"),
        message              = bot_message,
        diagnosis            = diagnosis_items,
        recommendation       = final.get("recommendation"),
        sources              = final.get("sources", []),
        red_flags            = final.get("red_flags", []),
        refer_to_doctor      = final.get("refer_to_doctor", False),
        clarifying_question  = final.get("clarifying_question"),
        # v2 fields
        first_line_drug      = final.get("first_line_drug"),
        alternatives         = final.get("alternatives", []),
        diagnosis_flow       = final.get("diagnosis_flow"),
        antibiotic_indicated = final.get("antibiotic_indicated", False),
        pushback_message     = final.get("pushback_message"),
        supportive_care      = final.get("supportive_care", []),
        when_to_see_doctor   = final.get("when_to_see_doctor"),
        clinical_scores      = final.get("clinical_scores"),
        augmented_notes      = final.get("augmented_notes"),
    )


@router.get("/{session_id}/history")
async def get_history(
    session_id: str,
    store:      SessionStore = Depends(get_store),
):
    state = store.get(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    return {
        "session_id":  session_id,
        "history":     state.get("history", []),
        "rounds":      state.get("clarify_round", 0),
        "intent":      state.get("intent", "unknown"),
        "domain":      state.get("symptom_domain", "general"),
        "topic_shift": state.get("topic_shift", False),
        "user_lang":   state.get("user_lang", "th"),   # v4: expose for debug
    }


@router.delete("/{session_id}")
async def clear_session(
    session_id: str,
    store:      SessionStore = Depends(get_store),
):
    store.delete(session_id)
    return {"session_id": session_id, "status": "cleared"}