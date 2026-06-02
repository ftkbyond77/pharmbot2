"""
scripts/debug_backend.py
------------------------
ทดสอบ pipeline ทีละ step โดยไม่ต้องผ่าน HTTP

Usage:
    python scripts/debug_backend.py
    python scripts/debug_backend.py --query "ปวดหัว มีไข้"
    python scripts/debug_backend.py --step embed
    python scripts/debug_backend.py --step llm
    python scripts/debug_backend.py --step retrieve
    python scripts/debug_backend.py --step graph
"""

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── load .env ─────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv()
os.environ["LITELLM_DROP_PARAMS"] = "True"

GREEN = "\033[92m"; RED = "\033[91m"; YELLOW = "\033[93m"
CYAN  = "\033[96m"; BOLD = "\033[1m"; RESET  = "\033[0m"

ok   = lambda s: print(f"{GREEN}✓{RESET} {s}")
fail = lambda s: print(f"{RED}✗{RESET} {s}")
info = lambda s: print(f"{CYAN}→{RESET} {s}")
head = lambda s: print(f"\n{BOLD}{CYAN}{'─'*50}\n  {s}\n{'─'*50}{RESET}")


# ── Step 1: LLM raw response ───────────────────────────────────
def test_llm():
    head("Step 1: Gemini LLM Raw Response")
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        from api.config import get_settings
        from api.prompts.pharmacist import extract_text, strip_fences

        cfg = get_settings()
        info(f"Model: {cfg.gemini_model}")

        llm = ChatGoogleGenerativeAI(
            model=cfg.gemini_model,
            google_api_key=cfg.gemini_api_key,
            temperature=0,
        )

        # test 1: plain text
        r = llm.invoke([{"role": "user", "content": "ตอบว่า OK เท่านั้น"}])
        info(f"content type : {type(r.content)}")
        info(f"content raw  : {repr(r.content)}")
        extracted = extract_text(r.content)
        info(f"extract_text : {repr(extracted)}")
        ok("plain text ผ่าน")

        # test 2: JSON response
        r2 = llm.invoke([{
            "role": "user",
            "content": 'ตอบ JSON เท่านั้น: {"status": "ok", "value": 42}'
        }])
        stripped = strip_fences(r2.content)
        info(f"strip_fences : {repr(stripped)}")
        parsed = json.loads(stripped)
        ok(f"JSON parse ผ่าน: {parsed}")

    except Exception as e:
        fail(f"LLM error: {e}")
        import traceback; traceback.print_exc()


# ── Step 2: Embedding ──────────────────────────────────────────
def test_embed(query: str):
    head("Step 2: BGE-M3 Embedding")
    try:
        from sentence_transformers import SentenceTransformer
        from api.config import get_settings

        cfg = get_settings()
        info(f"Model: {cfg.embedding_model}  device: {cfg.embedding_device}")

        model = SentenceTransformer(cfg.embedding_model, device=cfg.embedding_device)
        vec = model.encode(query, normalize_embeddings=True)
        ok(f"Encoded '{query[:40]}' → dim={len(vec)}, sample={vec[:3].tolist()}")

    except Exception as e:
        fail(f"Embed error: {e}")
        import traceback; traceback.print_exc()


# ── Step 3: Retrieval ──────────────────────────────────────────
def test_retrieve(query: str):
    head("Step 3: Qdrant Retrieval")
    try:
        from api.knowledge.retriever import Retriever
        from api.config import get_settings

        cfg = get_settings()
        retriever = Retriever()
        chunks = retriever.search(query, top_k=3, score_threshold=0.2)

        if not chunks:
            fail("No chunks returned — check if ingestion was done")
            return

        ok(f"Retrieved {len(chunks)} chunks")
        for i, c in enumerate(chunks, 1):
            info(f"[{i}] score={c['score']}  source={c['source']}")
            info(f"     text={c['text'][:100]}...")

        context = retriever.format_context(chunks)
        info(f"\nFormatted context preview:\n{context[:300]}...")

    except Exception as e:
        fail(f"Retrieve error: {e}")
        import traceback; traceback.print_exc()


# ── Step 4: Classify node ──────────────────────────────────────
def test_classify(query: str):
    head("Step 4: Classify Node")
    try:
        from api.agent.nodes.classify import classify_node

        state = _base_state(query)
        result = classify_node(state)
        ok(f"intent={result['intent']}  next_action={result['next_action']}")

    except Exception as e:
        fail(f"Classify error: {e}")
        import traceback; traceback.print_exc()


# ── Step 5: Full graph ─────────────────────────────────────────
async def _run_graph(query: str):
    from api.agent.graph import get_graph
    from api.knowledge.retriever import init_retriever
    from api.session.memory import init_session_store
    from api.config import get_settings

    cfg = get_settings()
    init_session_store(cfg.session_max, cfg.session_ttl_minutes)
    init_retriever()

    graph = get_graph()
    state = _base_state(query)
    result = await graph.ainvoke(state)
    return result


def test_graph(query: str):
    head("Step 5: Full LangGraph Pipeline")
    try:
        result = asyncio.run(_run_graph(query))
        final = result.get("final_response", {})

        ok(f"Graph completed")
        info(f"type          : {final.get('type')}")
        info(f"refer         : {final.get('refer_to_doctor')}")
        info(f"message       :\n{final.get('message', '')[:300]}")

        ddx = final.get("diagnosis", [])
        if ddx:
            info(f"diagnosis     : {ddx}")

        sources = final.get("sources", [])
        if sources:
            info(f"sources       : {sources}")

        red_flags = final.get("red_flags", [])
        if red_flags:
            info(f"red_flags     : {red_flags}")

    except Exception as e:
        fail(f"Graph error: {e}")
        import traceback; traceback.print_exc()


# ── helpers ────────────────────────────────────────────────────
def _base_state(query: str) -> dict:
    return {
        "session_id":             "debug-001",
        "user_message":           query,
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


# ── entrypoint ────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PharmBot backend pipeline debugger")
    parser.add_argument("--query", default="ไอมา 3 วัน มีน้ำมูกใส ไม่มีไข้", help="Test query")
    parser.add_argument("--step", choices=["llm", "embed", "retrieve", "classify", "graph"],
                        help="Run specific step only (default: all)")
    args = parser.parse_args()

    print(f"\n{BOLD}{CYAN}{'═'*50}")
    print(f"  PharmBot Backend Debug")
    print(f"  Query: {args.query}")
    print(f"{'═'*50}{RESET}")

    run_all = args.step is None

    if run_all or args.step == "llm":      test_llm()
    if run_all or args.step == "embed":    test_embed(args.query)
    if run_all or args.step == "retrieve": test_retrieve(args.query)
    if run_all or args.step == "classify": test_classify(args.query)
    if run_all or args.step == "graph":    test_graph(args.query)

    print(f"\n{BOLD}Done.{RESET}\n")