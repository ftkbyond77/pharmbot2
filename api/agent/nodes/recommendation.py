"""
agent/nodes/recommendation.py
------------------------------
Node 6: Recommendation — Augmented Generation

Improvements:
- Full conversation history passed to prompt
- Structured output includes: first_line_drug, alternatives,
  when_to_see_doctor, augmented_notes
- Grounding citations from [N] in prompt preserved in sources
- Robust parse with field-level fallback

Input  : state.symptom_summary, state.differential_diagnosis,
         state.clinical_rationale, state.retrieved_chunks, state.history
Output : state.recommendation, state.sources, state.next_action
"""

from __future__ import annotations

import json

from langchain_google_genai import ChatGoogleGenerativeAI
from loguru import logger

from api.agent.state import AgentState
from api.config import get_settings
from api.knowledge.retriever import get_retriever
from api.prompts.pharmacist import (
    SYSTEM_PROMPT,
    recommendation_prompt,
    _format_history_full,
    strip_fences,
)


def recommendation_node(state: AgentState) -> dict:
    cfg = get_settings()
    llm = ChatGoogleGenerativeAI(
        model=cfg.gemini_model,
        google_api_key=cfg.gemini_api_key,
        temperature=cfg.llm_temp_recommend,
    )

    retriever     = get_retriever()
    history       = state.get("history", [])
    history_text  = _format_history_full(history, max_turns=6)

    symptom_text   = " | ".join(state.get("symptom_summary", [state["user_message"]]))
    ddx_text       = _format_ddx(state.get("differential_diagnosis", []))
    rationale_text = "\n".join(
        f"- {r}" for r in state.get("clinical_rationale", [])
    ) or "(ไม่มีข้อมูลเพิ่มเติม)"
    context_text   = retriever.format_context(state.get("retrieved_chunks", []))

    prompt = recommendation_prompt(
        symptom_summary=symptom_text,
        ddx_text=ddx_text,
        rationale_text=rationale_text,
        retrieved_context=context_text,
        history_text=history_text,
    )
    response = llm.invoke([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": prompt},
    ])

    # ── parse ─────────────────────────────────────────────────
    recommendation    = ""
    sources: list[str] = []
    first_line        = None
    alternatives: list[str] = []
    when_to_see       = ""
    augmented_notes   = None

    try:
        raw  = strip_fences(response.content)
        data = json.loads(raw)

        recommendation  = str(data.get("recommendation", "")).strip()
        sources         = [str(s) for s in data.get("sources", []) if s]
        first_line      = data.get("first_line_drug")
        alternatives    = [str(a) for a in data.get("alternatives", []) if a]
        when_to_see     = str(data.get("when_to_see_doctor", "")).strip()
        augmented_notes = data.get("augmented_notes")

    except json.JSONDecodeError as exc:
        logger.warning(f"[recommendation] JSON parse failed: {exc} — using raw text")
        recommendation = response.content.strip()
        sources = [c["source"] for c in state.get("retrieved_chunks", [])]
    except Exception as exc:
        logger.error(f"[recommendation] unexpected error: {exc}")
        recommendation = "(เกิดข้อผิดพลาดในการสร้างคำแนะนำ)"

    # Append when_to_see_doctor to recommendation if not already included
    if when_to_see and when_to_see not in recommendation:
        recommendation = recommendation + f"\n\n⚠️ **ควรพบแพทย์เมื่อ:** {when_to_see}"

    if augmented_notes:
        logger.debug(f"[recommendation] augmented_notes: {augmented_notes[:120]}")

    logger.info(
        f"[recommendation] {len(recommendation)} chars | "
        f"{len(sources)} sources | first_line={first_line}"
    )

    # Fallback sources from retrieved chunks if empty
    if not sources:
        sources = [c["source"] for c in state.get("retrieved_chunks", [])]

    return {
        "recommendation": recommendation,
        "sources":        sources,
        "next_action":    "format",
        # store extras in state for potential frontend use
        "_first_line_drug": first_line,
        "_alternatives":    alternatives,
    }


def _format_ddx(ddx_list: list[dict]) -> str:
    if not ddx_list:
        return "(ยังไม่มีการวินิจฉัย)"
    conf_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}
    lines = []
    for item in ddx_list:
        emoji = conf_emoji.get(item.get("confidence", "low"), "⚪")
        lines.append(f"{emoji} {item.get('name', '')} ({item.get('confidence', '')})")
    return "\n".join(lines)