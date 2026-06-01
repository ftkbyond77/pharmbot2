"""
scripts/preflight.py
--------------------
Run BEFORE starting the system.
Checks every dependency and prints a clear pass/fail report.

Usage:
    python scripts/preflight.py
    python scripts/preflight.py --fix   # auto-install missing packages
"""

import argparse
import importlib
import os
import subprocess
import sys
from pathlib import Path

# ── colour helpers (no external deps) ────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

ok   = lambda s: f"{GREEN}✓{RESET} {s}"
fail = lambda s: f"{RED}✗{RESET} {s}"
warn = lambda s: f"{YELLOW}⚠{RESET} {s}"
info = lambda s: f"{CYAN}→{RESET} {s}"

errors:   list[str] = []
warnings: list[str] = []


def section(title: str) -> None:
    print(f"\n{BOLD}{CYAN}{'─'*50}{RESET}")
    print(f"{BOLD} {title}{RESET}")
    print(f"{BOLD}{CYAN}{'─'*50}{RESET}")


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


# ── 2. Required Python packages ───────────────────────────────
REQUIRED_PACKAGES = [
    # (import_name, pip_name, critical)
    ("fastapi",                   "fastapi",                  True),
    ("uvicorn",                   "uvicorn[standard]",        True),
    ("pydantic",                  "pydantic",                 True),
    ("pydantic_settings",         "pydantic-settings",        True),
    ("dotenv",                    "python-dotenv",            True),
    ("langgraph",                 "langgraph",                True),
    ("langchain",                 "langchain",                True),
    ("langchain_google_genai",    "langchain-google-genai",   True),
    ("langchain_community",       "langchain-community",      True),
    ("litellm",                   "litellm",                  True),
    ("openkb",                    "openkb",                   True),
    ("qdrant_client",             "qdrant-client",            True),
    ("sentence_transformers",     "sentence-transformers",    True),
    ("torch",                     "torch",                    True),
    ("fitz",                      "pymupdf",                  True),  # PyMuPDF
    ("unstructured",              "unstructured[pdf]",        False), # optional heavy dep
    ("pythainlp",                 "pythainlp",                False),
    ("tenacity",                  "tenacity",                 True),
    ("loguru",                    "loguru",                   True),
    ("httpx",                     "httpx",                    True),
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
        print(ok("Installed. Re-run preflight to verify."))
    elif missing_critical:
        errors.append(f"Missing packages: {', '.join(missing_critical)}")


# ── 3. Environment variables ──────────────────────────────────
def check_env() -> None:
    section("Environment Variables (.env)")

    env_path = Path(".env")
    if not env_path.exists():
        print(fail(".env file not found — copy .env.example → .env and fill in values"))
        errors.append(".env missing")
        return
    print(ok(".env file exists"))

    # load without pydantic-settings so preflight is standalone
    env_vars: dict[str, str] = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env_vars[k.strip()] = v.strip()

    REQUIRED_VARS = [
        ("GEMINI_API_KEY",    True,  "Gemini API key from https://aistudio.google.com"),
        ("QDRANT_URL",        True,  "e.g. http://localhost:6333"),
        ("OPENKB_URL",        True,  "e.g. http://localhost:3000"),
        ("EMBEDDING_MODEL",   False, "default: BAAI/bge-m3"),
        ("LITELLM_DROP_PARAMS", True, "must be 'True' for Gemini"),
    ]

    for var, critical, hint in REQUIRED_VARS:
        val = env_vars.get(var) or os.environ.get(var, "")
        if val:
            # mask secrets
            display = val[:6] + "..." if "KEY" in var or "SECRET" in var else val
            print(ok(f"{var} = {display}"))
        elif critical:
            print(fail(f"{var} not set  →  {hint}"))
            errors.append(f"Missing env: {var}")
        else:
            print(warn(f"{var} not set (optional)  →  {hint}"))

    # check LITELLM specifically
    val = env_vars.get("LITELLM_DROP_PARAMS", "")
    if val.lower() != "true":
        print(fail("LITELLM_DROP_PARAMS must be 'True' (Gemini will fail otherwise)"))
        errors.append("LITELLM_DROP_PARAMS not True")


# ── 4. Docker + services ──────────────────────────────────────
def check_docker() -> None:
    section("Docker & Services")

    # docker binary
    ok_d, out_d = _run_cmd("docker --version")
    if ok_d:
        print(ok(f"Docker: {out_d}"))
    else:
        print(fail("Docker not found — install from https://docker.com"))
        errors.append("Docker not installed")
        return

    # docker compose v2
    ok_c, out_c = _run_cmd("docker compose version")
    if ok_c:
        print(ok(f"Docker Compose: {out_c}"))
    else:
        ok_c2, out_c2 = _run_cmd("docker-compose --version")
        if ok_c2:
            print(ok(f"docker-compose: {out_c2}"))
        else:
            print(fail("Neither 'docker compose' nor 'docker-compose' found"))
            errors.append("Docker Compose not found")


def check_services() -> None:
    section("Running Services (Qdrant + OpenKB)")
    import urllib.request
    import urllib.error

    SERVICES = [
        ("Qdrant", "http://localhost:6333/healthz"),
        # OpenKB is a pip library — no service to check
    ]

    for name, url in SERVICES:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                print(ok(f"{name} reachable at {url}  [{r.status}]"))
        except urllib.error.HTTPError as e:
            # any HTTP response means service is up
            print(ok(f"{name} reachable at {url}  [{e.code}]"))
        except Exception as e:
            print(warn(f"{name} not reachable at {url} — start with: docker compose up -d"))
            warnings.append(f"{name} not running")


# ── 5. File structure ─────────────────────────────────────────
def check_files() -> None:
    section("Project File Structure")

    REQUIRED_FILES = [
        "api/main.py",
        "api/config.py",
        "api/agent/graph.py",
        "api/agent/state.py",
        "api/agent/nodes/classify.py",
        "api/agent/nodes/clarify.py",
        "api/agent/nodes/retrieve.py",
        "api/agent/nodes/clinical_reason.py",
        "api/agent/nodes/safety_gate.py",
        "api/agent/nodes/recommendation.py",
        "api/agent/nodes/format.py",
        "api/knowledge/ingest.py",
        "api/knowledge/retriever.py",
        "api/session/memory.py",
        "api/prompts/pharmacist.py",
        "api/routers/chat.py",
        "api/routers/health.py",
        "requirements.txt",
        "docker-compose.yml",
        ".env",
    ]

    REQUIRED_DIRS = [
        "data/guidelines",
        "web",
    ]

    for f in REQUIRED_FILES:
        p = Path(f)
        if p.exists():
            print(ok(f))
        else:
            print(fail(f"Missing: {f}"))
            errors.append(f"Missing file: {f}")

    for d in REQUIRED_DIRS:
        p = Path(d)
        if p.is_dir():
            print(ok(f"{d}/"))
        else:
            print(warn(f"Missing dir: {d}/  (create it)"))
            warnings.append(f"Missing dir: {d}")

    # check PDFs exist
    pdf_files = list(Path("data/guidelines").glob("*.pdf")) if Path("data/guidelines").is_dir() else []
    if pdf_files:
        print(ok(f"PDFs found: {[p.name for p in pdf_files]}"))
    else:
        print(warn("No PDFs in data/guidelines/ — run ingest after adding them"))
        warnings.append("No PDFs found")


# ── 6. Quick import smoke test ────────────────────────────────
def check_imports() -> None:
    section("Critical Import Smoke Test")

    tests = [
        ("langgraph.graph",          "StateGraph"),
        ("langchain_google_genai",   "ChatGoogleGenerativeAI"),
        ("qdrant_client",            "QdrantClient"),
        ("sentence_transformers",    "SentenceTransformer"),
    ]

    for module, cls in tests:
        try:
            mod = importlib.import_module(module)
            getattr(mod, cls)
            print(ok(f"from {module} import {cls}"))
        except (ImportError, AttributeError) as e:
            print(fail(f"from {module} import {cls}  →  {e}"))
            errors.append(f"Import failed: {module}.{cls}")


# ── 7. Node.js / npm ──────────────────────────────────────────
def _run_cmd(cmd: str) -> tuple[bool, str]:
    """Cross-platform command runner — uses shell=True on Windows."""
    try:
        result = subprocess.run(
            cmd,
            shell=True,           # ← Windows needs this for npm/node in PATH
            capture_output=True,
            text=True,
        )
        return result.returncode == 0, result.stdout.strip() or result.stderr.strip()
    except (FileNotFoundError, OSError):
        return False, "not found"


def check_node() -> None:
    section("Node.js (Frontend)")

    for cmd, label in [("node --version", "Node.js"), ("npm --version", "npm")]:
        success, output = _run_cmd(cmd)
        if success:
            print(ok(f"{label}: {output}"))
        else:
            print(warn(f"{label} not found — needed only for frontend dev"))
            warnings.append(f"{label} not found")

    # check web/node_modules
    if Path("web/node_modules").is_dir():
        print(ok("web/node_modules exists"))
    else:
        print(warn("web/node_modules missing — run: cd web && npm install"))
        warnings.append("npm install not done")


# ── summary ───────────────────────────────────────────────────
def print_summary() -> None:
    section("Summary")

    if not errors and not warnings:
        print(f"\n{GREEN}{BOLD}✅ All checks passed — system ready to run!{RESET}\n")
    elif not errors:
        print(f"\n{YELLOW}{BOLD}⚠️  {len(warnings)} warning(s) — system should work but check above{RESET}")
        for w in warnings:
            print(f"  {YELLOW}•{RESET} {w}")
        print()
    else:
        print(f"\n{RED}{BOLD}❌ {len(errors)} error(s) must be fixed before running:{RESET}")
        for e in errors:
            print(f"  {RED}•{RESET} {e}")
        if warnings:
            print(f"\n{YELLOW}{BOLD}⚠️  {len(warnings)} warning(s):{RESET}")
            for w in warnings:
                print(f"  {YELLOW}•{RESET} {w}")
        print()
        sys.exit(1)


# ── entrypoint ────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PharmBot preflight checker")
    parser.add_argument("--fix", action="store_true", help="Auto-install missing Python packages")
    parser.add_argument("--skip-services", action="store_true", help="Skip Qdrant/OpenKB connectivity check")
    args = parser.parse_args()

    print(f"\n{BOLD}{CYAN}{'═'*50}{RESET}")
    print(f"{BOLD}{CYAN}  PharmBot — Pre-flight Check{RESET}")
    print(f"{BOLD}{CYAN}{'═'*50}{RESET}")

    check_python()
    check_packages(auto_fix=args.fix)
    check_env()
    check_docker()
    if not args.skip_services:
        check_services()
    check_files()
    check_imports()
    check_node()
    print_summary()