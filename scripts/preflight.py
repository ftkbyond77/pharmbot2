"""
scripts/preflight.py
--------------------
Pre-flight check — run before starting the server:
    python scripts/preflight.py
    python scripts/preflight.py --fix    # auto-install missing packages
"""

from __future__ import annotations

import importlib
import subprocess
import sys
import warnings as stdlib_warnings

errors:   list[str] = []
warnings: list[str] = []

# ── ANSI ──────────────────────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def ok(msg: str)   -> str: return f"  {GREEN}✓{RESET} {msg}"
def warn(msg: str) -> str: return f"  {YELLOW}⚠{RESET} {msg}"
def fail(msg: str) -> str: return f"  {RED}✗{RESET} {msg}"
def info(msg: str) -> str: return f"    {msg}"
def section(title: str) -> None:
    print(f"\n{BOLD}{title}{RESET}")
    print("─" * 50)


# ── 1. Python version ─────────────────────────────────────────
def check_python() -> None:
    section("Python Version")
    major, minor = sys.version_info[:2]
    ver = f"{major}.{minor}"
    if (major, minor) >= (3, 11):
        print(ok(f"Python {ver}"))
    elif (major, minor) >= (3, 10):
        print(warn(f"Python {ver} — recommend 3.11+"))
        warnings.append("Python < 3.11")
    else:
        print(fail(f"Python {ver} — need ≥ 3.10"))
        errors.append("Python version too old")


# ── 2. Required packages ──────────────────────────────────────
# (import_name, pip_name, critical)
REQUIRED_PACKAGES = [
    ("fastapi",               "fastapi",                    True),
    ("uvicorn",               "uvicorn[standard]",          True),
    ("pydantic",              "pydantic",                   True),
    ("pydantic_settings",     "pydantic-settings",          True),
    ("dotenv",                "python-dotenv",              True),
    ("langgraph",             "langgraph",                  True),
    ("langchain",             "langchain",                  True),
    ("langchain_google_genai","langchain-google-genai",     True),
    ("langchain_community",   "langchain-community",        True),
    ("qdrant_client",         "qdrant-client",              True),
    ("sentence_transformers", "sentence-transformers",      True),
    ("torch",                 "torch",                      True),
    ("docling",               "docling",                    True),   # replaces openkb
    ("fitz",                  "pymupdf",                    True),   # fallback parser
    ("flashrank",             "flashrank",                  False),  # reranker (optional)
    ("pythainlp",             "pythainlp",                  False),  # Thai NLP (Phase 2)
    ("tenacity",              "tenacity",                   True),
    ("loguru",                "loguru",                     True),
    ("httpx",                 "httpx",                      True),
]


def check_packages(auto_fix: bool) -> None:
    section("Python Packages")
    missing_critical: list[str] = []
    missing_optional: list[str] = []

    for import_name, pip_name, critical in REQUIRED_PACKAGES:
        try:
            importlib.import_module(import_name)
            print(ok(import_name))
        except ImportError:
            if critical:
                print(fail(f"{import_name}  →  pip install {pip_name}"))
                missing_critical.append(pip_name)
            else:
                print(warn(f"{import_name} (optional)  →  pip install {pip_name}"))
                missing_optional.append(pip_name)

    if auto_fix and missing_critical:
        print(info("Auto-installing missing critical packages..."))
        subprocess.check_call([
            sys.executable, "-m", "pip", "install",
            "--break-system-packages",
            *missing_critical,
        ])
        print(ok("Installed critical packages."))

    if missing_critical and not auto_fix:
        errors.append(f"Missing critical packages: {missing_critical}")
    if missing_optional:
        warnings.append(f"Missing optional packages: {missing_optional}")


# ── 3. Environment variables ──────────────────────────────────
REQUIRED_ENV = [
    ("GEMINI_API_KEY", True),
    ("QDRANT_URL",     False),
]

def check_env() -> None:
    import os
    from pathlib import Path

    section("Environment Variables")

    # try loading .env
    env_path = Path(".env")
    if env_path.exists():
        print(ok(f".env file found at {env_path.resolve()}"))
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass
    else:
        print(warn(".env file not found — using system env vars"))

    for key, required in REQUIRED_ENV:
        val = os.environ.get(key)
        if val:
            masked = val[:6] + "..." if len(val) > 6 else "***"
            print(ok(f"{key} = {masked}"))
        elif required:
            print(fail(f"{key} not set — REQUIRED"))
            errors.append(f"Missing env: {key}")
        else:
            print(warn(f"{key} not set (optional — will use default)"))


# ── 4. Qdrant connectivity ────────────────────────────────────
def check_qdrant() -> None:
    section("Qdrant Connection")
    try:
        from qdrant_client import QdrantClient
        from api.config import get_settings
        cfg = get_settings()
        client = QdrantClient(url=cfg.qdrant_url, timeout=5)
        collections = client.get_collections().collections
        print(ok(f"Connected to {cfg.qdrant_url} — {len(collections)} collection(s)"))

        if cfg.qdrant_collection in [c.name for c in collections]:
            info_data = client.get_collection(cfg.qdrant_collection)
            count = info_data.points_count
            if count and count > 0:
                print(ok(f"Collection '{cfg.qdrant_collection}' has {count} vectors"))
            else:
                print(warn(f"Collection '{cfg.qdrant_collection}' is EMPTY — run ingestion"))
                warnings.append("Qdrant collection empty")
        else:
            print(warn(f"Collection '{cfg.qdrant_collection}' does not exist — run ingestion"))
            warnings.append("Qdrant collection missing")

    except Exception as exc:
        print(fail(f"Qdrant error: {exc}"))
        print(info("Hint: docker compose up -d qdrant"))
        errors.append("Qdrant unreachable")


# ── 5. Embedding model ────────────────────────────────────────
def check_embedding() -> None:
    section("BGE-M3 Embedding Model")
    try:
        from sentence_transformers import SentenceTransformer
        from api.config import get_settings
        cfg = get_settings()
        print(info(f"Loading {cfg.embedding_model} on {cfg.embedding_device}..."))
        model = SentenceTransformer(cfg.embedding_model, device=cfg.embedding_device)
        vec = model.encode("test", normalize_embeddings=True)
        print(ok(f"Model loaded — dim={len(vec)}"))
    except Exception as exc:
        print(fail(f"Embedding error: {exc}"))
        errors.append("Embedding model failed")


# ── 6. Docling ────────────────────────────────────────────────
def check_docling() -> None:
    section("Docling PDF Parser")
    try:
        from docling.document_converter import DocumentConverter
        print(ok("docling import OK"))
    except ImportError as exc:
        print(fail(f"docling not available: {exc}"))
        print(info("pip install docling"))
        errors.append("docling missing")


# ── entrypoint ────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="PharmBot pre-flight check")
    parser.add_argument("--fix",     action="store_true", help="Auto-install missing packages")
    parser.add_argument("--skip-ml", action="store_true", help="Skip slow ML model checks")
    args = parser.parse_args()

    check_python()
    check_packages(auto_fix=args.fix)
    check_env()
    check_qdrant()
    check_docling()
    if not args.skip_ml:
        check_embedding()

    print(f"\n{'─'*50}")
    if errors:
        print(f"\n{RED}{BOLD}✗ {len(errors)} error(s):{RESET}")
        for e in errors:
            print(f"  {RED}• {e}{RESET}")
        sys.exit(1)
    elif warnings:
        print(f"\n{YELLOW}{BOLD}⚠ {len(warnings)} warning(s):{RESET}")
        for w in warnings:
            print(f"  {YELLOW}• {w}{RESET}")
        print(f"\n{GREEN}{BOLD}Pre-flight passed with warnings.{RESET}\n")
    else:
        print(f"\n{GREEN}{BOLD}✓ All checks passed.{RESET}\n")