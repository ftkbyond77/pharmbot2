"""
agent/nodes/recommendation.py
------------------------------
Node 6: Recommendation

Generates OTC advice, self-care instructions, and
when-to-see-a-doctor guidance — all grounded in retrieved chunks.

Input  : state.symptom_summary, state.differential_diagnosis,
         state.clinical_rationale, state.retrieved_chunks
Output : state.recommendation, state.sources, state.next_action
"""

import json
from loguru import logger
from langchain_google_genai import ChatGoogleGenerativeAI

from api.agent.state import AgentState
from api.config import get_settings
from api.knowledge.retriever import get_retriever
from api.prompts.pharmacist import SYSTEM_PROMPT, recommendation_prompt, strip_fences


def recommendation_node(state: AgentState) -> dict:
    cfg = get_settings()
    llm = ChatGoogleGenerativeAI(
        model=cfg.gemini_model,
        google_api_key=cfg.gemini_api_key,
        temperature=0.4,    # slight creativity for natural language
    )

    # ── format inputs ─────────────────────────────────────────
    symptom_text  = " | ".join(state.get("symptom_summary", [state["user_message"]]))
    ddx_text      = _format_ddx(state.get("differential_diagnosis", []))
    rationale_text = "\n".join(
        f"- {r}" for r in state.get("clinical_rationale", [])
    ) or "(ไม่มีข้อมูลเพิ่มเติม)"

    retriever     = get_retriever()
    context_text  = retriever.format_context(state.get("retrieved_chunks", []))

    # ── call LLM ──────────────────────────────────────────────
    prompt = recommendation_prompt(
        symptom_summary=symptom_text,
        ddx_text=ddx_text,
        rationale_text=rationale_text,
        retrieved_context=context_text,
    )
    response = llm.invoke([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": prompt},
    ])

    # ── parse ─────────────────────────────────────────────────
    recommendation = ""
    sources: list[str] = []
    try:
        raw = strip_fences(response.content)
        data = json.loads(raw)
        recommendation = data.get("recommendation", "")
        sources        = data.get("sources", [])
    except Exception as e:
        logger.warning(f"recommendation_node parse error: {e} — using raw text")
        recommendation = response.content.strip()
        # fallback: pull source strings from chunks directly
        sources = [c["source"] for c in state.get("retrieved_chunks", [])]

    logger.info(f"[recommendation] generated {len(recommendation)} chars, {len(sources)} sources")

    return {
        "recommendation": recommendation,
        "sources":        sources,
        "next_action":    "format",
    }


def _format_ddx(ddx_list: list[dict]) -> str:
    if not ddx_list:
        return "(ยังไม่มีการวินิจฉัย)"
    lines = []
    for item in ddx_list:
        conf_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(
            item.get("confidence", "low"), "⚪"
        )
        lines.append(f"{conf_emoji} {item.get('name', '')} ({item.get('confidence', '')})")
    return "\n".join(lines)