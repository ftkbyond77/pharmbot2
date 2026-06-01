"""
agent/nodes/clinical_reason.py
-------------------------------
Node 4: Clinical reasoning

- Summarises symptoms from full conversation
- Generates differential diagnosis (DDx) list
- Produces human-readable rationale (internal CoT hidden)
- Detects red flags early (feeds into safety_gate)

Input  : state.user_message, state.history, state.retrieved_chunks
Output : state.symptom_summary, state.differential_diagnosis,
         state.clinical_rationale, state.red_flags_found, state.next_action
"""

import json
from loguru import logger
from langchain_google_genai import ChatGoogleGenerativeAI

from api.agent.state import AgentState, DDxItem
from api.config import get_settings
from api.knowledge.retriever import get_retriever
from api.prompts.pharmacist import SYSTEM_PROMPT, clinical_reason_prompt


def clinical_reason_node(state: AgentState) -> dict:
    cfg = get_settings()
    llm = ChatGoogleGenerativeAI(
        model=cfg.gemini_model,
        google_api_key=cfg.gemini_api_key,
        temperature=0.2,
    )

    # ── build symptom summary string from history ─────────────
    symptom_text = _summarise_symptoms(state)

    # ── format retrieved context ──────────────────────────────
    retriever = get_retriever()
    context_text = retriever.format_context(state.get("retrieved_chunks", []))

    # ── call LLM ──────────────────────────────────────────────
    prompt = clinical_reason_prompt(symptom_text, context_text)
    response = llm.invoke([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": prompt},
    ])

    # ── parse response ────────────────────────────────────────
    symptom_summary: list[str] = []
    ddx: list[DDxItem] = []
    rationale: list[str] = []
    red_flags: list[str] = []

    try:
        raw = _strip_fences(response.content)
        data = json.loads(raw)
        symptom_summary = data.get("symptom_summary", [])
        ddx             = data.get("differential_diagnosis", [])
        rationale       = data.get("clinical_rationale", [])
        red_flags       = data.get("red_flags", [])
    except Exception as e:
        logger.warning(f"clinical_reason_node parse error: {e}")
        # graceful degradation: carry on with empty fields
        symptom_summary = [state["user_message"]]

    logger.info(
        f"[clinical_reason] symptoms={len(symptom_summary)} "
        f"ddx={len(ddx)} red_flags={red_flags}"
    )

    # route: if red flags found → safety_gate will handle termination
    next_action = "safety_gate" if red_flags else "safety_gate"  # always check

    return {
        "symptom_summary":         symptom_summary,
        "differential_diagnosis":  ddx,
        "clinical_rationale":      rationale,
        "red_flags_found":         red_flags,
        "next_action":             next_action,
    }


def _summarise_symptoms(state: AgentState) -> str:
    """
    Concatenate all user turns into a readable symptom narrative.
    """
    history = state.get("history", [])
    user_turns = [
        h["content"] for h in history if h.get("role") == "user"
    ]
    user_turns.append(state["user_message"])
    return "\n".join(f"- {t}" for t in user_turns)


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return text.strip()