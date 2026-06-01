"""
scripts/debug.py
----------------
Runtime debug tool — test each layer independently.

Usage:
    python scripts/debug.py --all
    python scripts/debug.py --gemini
    python scripts/debug.py --qdrant
    python scripts/debug.py --embed
    python scripts/debug.py --graph
    python scripts/debug.py --api
"""

import argparse
import asyncio
import json
import os
import sys
import urllib.request

# ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GREEN = "\033[92m"; RED = "\033[91m"; YELLOW = "\033[93m"
CYAN  = "\033[96m"; BOLD = "\033[1m"; RESET  = "\033[0m"

ok   = lambda s: print(f"{GREEN}✓{RESET} {s}")
fail = lambda s: print(f"{RED}✗{RESET} {s}")
info = lambda s: print(f"{CYAN}→{RESET} {s}")
head = lambda s: print(f"\n{BOLD}{CYAN}── {s} ──{RESET}")


# ── load .env early ───────────────────────────────────────────
def load_env() -> None:
    env_path = ".env"
    if os.path.exists(env_path):
        for line in open(env_path).readlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
    # critical Gemini fix
    os.environ["LITELLM_DROP_PARAMS"] = "True"


# ── 1. Gemini ─────────────────────────────────────────────────
def debug_gemini() -> None:
    head("Gemini LLM")
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        from api.config import get_settings
        cfg = get_settings()

        info(f"Model: {cfg.gemini_model}")
        llm = ChatGoogleGenerativeAI(
            model=cfg.gemini_model,
            google_api_key=cfg.gemini_api_key,
            temperature=0,
        )
        response = llm.invoke([{"role": "user", "content": "ตอบว่า 'สวัสดี' เท่านั้น"}])
        ok(f"Response: {response.content.strip()}")
    except Exception as e:
        fail(f"Gemini error: {e}")
        _hint_gemini(e)


def _hint_gemini(e: Exception) -> None:
    msg = str(e).lower()
    if "api_key" in msg or "api key" in msg:
        print(f"  {YELLOW}Hint: GEMINI_API_KEY missing or invalid in .env{RESET}")
    elif "litellm" in msg or "drop_params" in msg:
        print(f"  {YELLOW}Hint: set LITELLM_DROP_PARAMS=True in .env{RESET}")
    elif "quota" in msg:
        print(f"  {YELLOW}Hint: Gemini quota exceeded — check aistudio.google.com{RESET}")
    elif "model" in msg:
        print(f"  {YELLOW}Hint: check GEMINI_MODEL in .env (try gemini-1.5-flash){RESET}")


# ── 2. Qdrant ─────────────────────────────────────────────────
def debug_qdrant() -> None:
    head("Qdrant Vector Store")
    try:
        from qdrant_client import QdrantClient
        from api.config import get_settings
        cfg = get_settings()

        info(f"URL: {cfg.qdrant_url}")
        client = QdrantClient(url=cfg.qdrant_url, timeout=5)
        collections = client.get_collections().collections
        ok(f"Connected — {len(collections)} collection(s): {[c.name for c in collections]}")

        if cfg.qdrant_collection in [c.name for c in collections]:
            info_data = client.get_collection(cfg.qdrant_collection)
            count = info_data.points_count
            ok(f"Collection '{cfg.qdrant_collection}' has {count} vectors")
            if count == 0:
                print(f"  {YELLOW}Hint: run ingestion first:{RESET}")
                print(f"  {YELLOW}  python -m api.knowledge.ingest --dir data/guidelines/{RESET}")
        else:
            print(f"  {YELLOW}Collection '{cfg.qdrant_collection}' not found — run ingestion{RESET}")
    except Exception as e:
        fail(f"Qdrant error: {e}")
        print(f"  {YELLOW}Hint: docker compose up -d qdrant{RESET}")


# ── 3. Embedding model ────────────────────────────────────────
def debug_embed() -> None:
    head("BGE-M3 Embedding Model")
    try:
        from sentence_transformers import SentenceTransformer
        from api.config import get_settings
        cfg = get_settings()

        info(f"Loading model: {cfg.embedding_model} (device={cfg.embedding_device})")
        model = SentenceTransformer(cfg.embedding_model, device=cfg.embedding_device)
        ok("Model loaded")

        sentences = ["ไอเรื้อรัง น้ำมูกใส", "cough with runny nose"]
        vecs = model.encode(sentences, normalize_embeddings=True)
        sim = model.similarity(vecs, vecs)
        ok(f"Encoded {len(sentences)} sentences — dim={vecs.shape[1]}")
        ok(f"Similarity matrix shape: {sim.shape}")
        info(f"Cross-lingual similarity (TH↔EN): {sim[0][1]:.3f}")
    except Exception as e:
        fail(f"Embedding error: {e}")
        print(f"  {YELLOW}Hint: pip install sentence-transformers torch{RESET}")


# ── 4. LangGraph agent ────────────────────────────────────────
async def _run_graph(query: str) -> None:
    from api.agent.graph import get_graph
    from api.agent.state import AgentState
    from api.knowledge.retriever import init_retriever
    from api.session.memory import init_session_store
    from api.config import get_settings

    cfg = get_settings()
    init_session_store(cfg.session_max, cfg.session_ttl_minutes)
    init_retriever()

    state: dict = {
        "session_id":             "debug-session",
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

    graph = get_graph()
    result = await graph.ainvoke(state)
    final = result.get("final_response", {})

    ok(f"Graph completed — type={final.get('type')}")
    info(f"Message: {final.get('message', '')[:120]}...")

    ddx = final.get("diagnosis", [])
    if ddx:
        info(f"DDx: {[d['name'] for d in ddx]}")

    sources = final.get("sources", [])
    if sources:
        info(f"Sources: {sources}")


def debug_graph() -> None:
    head("LangGraph Agent (end-to-end)")
    try:
        query = "ไอมา 3 วัน มีน้ำมูกใส ไม่มีไข้"
        info(f"Test query: '{query}'")
        asyncio.run(_run_graph(query))
    except Exception as e:
        fail(f"Graph error: {e}")
        import traceback
        traceback.print_exc()


# ── 5. FastAPI endpoints ──────────────────────────────────────
def debug_api() -> None:
    head("FastAPI Endpoints")
    import urllib.error

    BASE = "http://localhost:8000"

    # health
    for path, label in [("/health", "liveness"), ("/ready", "readiness")]:
        url = BASE + path
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                body = json.loads(r.read())
                ok(f"{label} {url} → {body}")
        except urllib.error.HTTPError as e:
            body = json.loads(e.read())
            print(f"{YELLOW}⚠{RESET} {label} {url} → {e.code} {body}")
        except Exception as e:
            fail(f"{label} {url} → {e}")
            print(f"  {YELLOW}Hint: uvicorn api.main:app --reload{RESET}")
            return

    # chat endpoint
    info("Testing POST /api/v1/chat ...")
    import urllib.request as req
    data = json.dumps({"message": "ปวดหัวเล็กน้อย"}).encode()
    request = req.Request(
        f"{BASE}/api/v1/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with req.urlopen(request, timeout=30) as r:
            body = json.loads(r.read())
            ok(f"POST /chat → type={body.get('type')} session_id={body.get('session_id', '')[:8]}...")
            info(f"  message: {body.get('message', '')[:100]}")
    except Exception as e:
        fail(f"POST /chat → {e}")


# ── entrypoint ────────────────────────────────────────────────
if __name__ == "__main__":
    load_env()

    parser = argparse.ArgumentParser(description="PharmBot runtime debugger")
    parser.add_argument("--all",    action="store_true")
    parser.add_argument("--gemini", action="store_true")
    parser.add_argument("--qdrant", action="store_true")
    parser.add_argument("--embed",  action="store_true")
    parser.add_argument("--graph",  action="store_true")
    parser.add_argument("--api",    action="store_true")
    args = parser.parse_args()

    run_all = args.all or not any([args.gemini, args.qdrant, args.embed, args.graph, args.api])

    if run_all or args.gemini: debug_gemini()
    if run_all or args.qdrant: debug_qdrant()
    if run_all or args.embed:  debug_embed()
    if run_all or args.graph:  debug_graph()
    if run_all or args.api:    debug_api()

    print(f"\n{BOLD}Debug complete.{RESET}\n")