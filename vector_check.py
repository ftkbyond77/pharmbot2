"""
vector_check.py
───────────────
ดึง vector จาก Qdrant แล้วแสดงหน้าตาใน 3 กลุ่ม:

  1. TEXT    — chunk ข้อความปกติ (extractor: docling / pymupdf)
  2. TABLE   — chunk ที่น่าจะมีตาราง (ตรวจจากเนื้อหา Markdown table)
  3. CHART   — chunk จาก Vision (แผนภูมิ / flowchart)

วิธีใช้:
  python vector_check.py                   # รันทั้ง 3 กลุ่ม
  python vector_check.py --group text      # ดูเฉพาะ text
  python vector_check.py --group table     # ดูเฉพาะตาราง
  python vector_check.py --group chart     # ดูเฉพาะแผนภูมิ
  python vector_check.py --search "หวัด"   # semantic search แล้วแสดงผล
  python vector_check.py --source "AAFP"   # filter เฉพาะไฟล์
  python vector_check.py --limit 5         # จำนวน chunk ต่อกลุ่ม (default 5)
"""

import sys
import re
import argparse
from textwrap import fill, indent

# ── ต้องรันจาก root ของโปรเจกต์ ──────────────────────────────
sys.path.insert(0, ".")

from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue, ScrollRequest

# ─────────────────────────────────────────────────────────────
#  Config (อ่านจาก .env เหมือน ingest)
# ─────────────────────────────────────────────────────────────
try:
    from api.config import get_settings
    cfg = get_settings()
    QDRANT_URL  = cfg.qdrant_url
    COLLECTION  = cfg.qdrant_collection
except Exception:
    QDRANT_URL  = "http://localhost:6333"
    COLLECTION  = "pharmbot_guidelines"


# ─────────────────────────────────────────────────────────────
#  Display helpers
# ─────────────────────────────────────────────────────────────

RESET  = "\033[0m"
BOLD   = "\033[1m"
CYAN   = "\033[36m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
MAGENTA= "\033[35m"
RED    = "\033[31m"
DIM    = "\033[2m"

def _color(text: str, code: str) -> str:
    return f"{code}{text}{RESET}"

def _header(title: str, color: str = CYAN) -> None:
    line = "─" * 70
    print(f"\n{_color(line, color)}")
    print(f"{_color(f'  {title}', BOLD + color)}")
    print(f"{_color(line, color)}")

def _chunk_box(
    idx: int,
    payload: dict,
    show_full: bool = False,
    highlight: str | None = None,
) -> None:
    """Print a single chunk with metadata header and text preview."""
    source   = payload.get("source", "?")
    page     = payload.get("page", "?")
    extractor= payload.get("extractor", "?")
    ctype    = payload.get("chunk_type", "?")
    lang     = payload.get("lang", "?")
    cat      = payload.get("disease_cat", "?")
    text     = payload.get("text", "")
    words    = len(text.split())

    # ── metadata line ──
    meta = (
        f"{_color(f'[{idx}]', BOLD)} "
        f"{_color(source, YELLOW)}  "
        f"p{_color(str(page), CYAN)}  "
        f"ext={_color(extractor, GREEN)}  "
        f"type={_color(ctype, MAGENTA)}  "
        f"lang={lang}  cat={cat}  {_color(str(words)+'w', DIM)}"
    )
    print(meta)

    # ── text body ──
    if show_full:
        body = text
    else:
        # First 400 chars — enough to see structure
        body = text[:400] + ("  …" if len(text) > 400 else "")

    # Highlight search term if given
    if highlight:
        body = body.replace(highlight, _color(highlight, RED + BOLD))

    # Indent and wrap long lines nicely
    lines = body.splitlines()
    for line in lines[:25]:                 # cap at 25 lines in preview
        print("  " + line)
    if len(lines) > 25:
        print(f"  {_color(f'  ... ({len(lines)-25} more lines)', DIM)}")
    print()


def _is_table_chunk(text: str) -> bool:
    """Heuristic: markdown table rows have | col | col | pattern."""
    pipe_lines = sum(1 for l in text.splitlines() if l.count("|") >= 2)
    return pipe_lines >= 2


def _is_chart_chunk(payload: dict) -> bool:
    ctype = payload.get("chunk_type", "")
    ext   = payload.get("extractor", "")
    text  = payload.get("text", "")
    if ext == "vision" or "vision" in ctype:
        return True
    # Also catch docling-extracted flowchart pages via keyword
    if re.search(r"แผนภูมิ|flowchart|algorithm|decision tree|ภาพที่\s*\d+", text, re.IGNORECASE):
        return True
    return False


# ─────────────────────────────────────────────────────────────
#  Qdrant helpers
# ─────────────────────────────────────────────────────────────

def scroll_all(client: QdrantClient, source_filter: str | None = None) -> list[dict]:
    """Fetch all points from collection (paginated)."""
    points = []
    offset = None
    flt = None
    if source_filter:
        flt = Filter(must=[FieldCondition(
            key="source",
            match=MatchValue(value=source_filter),
        )])
    while True:
        result, next_offset = client.scroll(
            collection_name=COLLECTION,
            scroll_filter=flt,
            limit=250,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        points.extend(result)
        if next_offset is None:
            break
        offset = next_offset
    return [p.payload for p in points]


def semantic_search(
    client: QdrantClient,
    query: str,
    top_k: int = 10,
    source_filter: str | None = None,
) -> list[tuple[float, dict]]:
    """Embed query and search Qdrant."""
    from sentence_transformers import SentenceTransformer
    try:
        model_name = cfg.embedding_model
    except Exception:
        model_name = "BAAI/bge-m3"

    print(f"{_color('Loading embedding model...', DIM)}", end=" ", flush=True)
    model = SentenceTransformer(model_name)
    print(_color("done", GREEN))

    vec = model.encode([query], normalize_embeddings=True)[0].tolist()

    flt = None
    if source_filter:
        flt = Filter(must=[FieldCondition(
            key="source",
            match=MatchValue(value=source_filter),
        )])

    results = client.query_points(
        collection_name=COLLECTION,
        query=vec,
        query_filter=flt,
        limit=top_k,
        with_payload=True,
    )
    return [(r.score, r.payload) for r in results.points]


# ─────────────────────────────────────────────────────────────
#  Report sections
# ─────────────────────────────────────────────────────────────

def report_overview(all_payloads: list[dict]) -> None:
    """Print collection-level statistics."""
    from collections import Counter

    total     = len(all_payloads)
    by_ext    = Counter(p.get("extractor","?")   for p in all_payloads)
    by_ctype  = Counter(p.get("chunk_type","?")  for p in all_payloads)
    by_source = Counter(p.get("source","?")      for p in all_payloads)
    by_lang   = Counter(p.get("lang","?")        for p in all_payloads)
    by_cat    = Counter(p.get("disease_cat","?") for p in all_payloads)

    n_table = sum(1 for p in all_payloads if _is_table_chunk(p.get("text","")))
    n_chart = sum(1 for p in all_payloads if _is_chart_chunk(p))
    n_text  = total - n_table - n_chart

    _header("COLLECTION OVERVIEW", CYAN)
    print(f"  Collection : {_color(COLLECTION, BOLD)}")
    print(f"  Qdrant URL : {QDRANT_URL}")
    print(f"  Total chunks : {_color(str(total), BOLD + GREEN)}")
    print()

    print(f"  {_color('By extractor:', BOLD)}")
    for k, v in by_ext.most_common():
        bar = "█" * min(40, v // max(1, total // 40))
        print(f"    {k:<18} {v:>5}  {_color(bar, CYAN)}")

    print(f"\n  {_color('By chunk type:', BOLD)}")
    for k, v in by_ctype.most_common():
        print(f"    {k:<22} {v:>5}")

    print(f"\n  {_color('By source file:', BOLD)}")
    for k, v in by_source.most_common():
        print(f"    {k:<40} {v:>5}")

    print(f"\n  {_color('By language:', BOLD)}")
    for k, v in by_lang.most_common():
        print(f"    {k:<10} {v:>5}")

    print(f"\n  {_color('Content groups (heuristic):', BOLD)}")
    print(f"    {'Text chunks':<20} {n_text:>5}")
    print(f"    {'Table chunks':<20} {n_table:>5}  ← contain Markdown tables")
    print(f"    {'Chart/Vision chunks':<20} {n_chart:>5}  ← Vision-extracted or flowchart")


def report_text(all_payloads: list[dict], limit: int = 5, source: str | None = None) -> None:
    """Show sample text chunks — check section boundaries."""
    _header("GROUP 1: TEXT CHUNKS  (section-aware chunking check)", GREEN)
    print(f"  Showing {limit} samples — verify section headings are intact\n")

    candidates = [
        p for p in all_payloads
        if p.get("extractor") in ("docling_text", "docling_ocr", "pymupdf")
        and not _is_table_chunk(p.get("text",""))
        and not _is_chart_chunk(p)
        and (source is None or source.lower() in p.get("source","").lower())
    ]
    # Sort by source + page for readable order
    candidates.sort(key=lambda p: (p.get("source",""), p.get("page") or 0))

    # Show samples: first few, a middle one, last one
    sample_idx = list(range(min(limit, len(candidates))))
    if len(candidates) > limit:
        mid = len(candidates) // 2
        sample_idx = list(range(min(3, limit))) + [mid, len(candidates)-1]
        sample_idx = sorted(set(sample_idx))[:limit]

    for i, idx in enumerate(sample_idx):
        _chunk_box(i + 1, candidates[idx])

    print(f"  {_color(f'Total text chunks: {len(candidates)}', DIM)}")

    # ── Section boundary check ──
    _header("  SECTION BOUNDARY CHECK", DIM)
    print("  Checking: do chunks start with section headings?\n")
    heading_re = re.compile(
        r"^(#{1,3} |บทนำ|สาเหตุ|การรักษา|การวินิจฉัย|ลักษณะอาการ|ภาวะแทรกซ้อน)"
    )
    continuation_re = re.compile(r"\[\.\.\.จากหัวข้อด้านบน\]")

    starts_with_heading = sum(
        1 for p in candidates if heading_re.search(p.get("text","")[:80])
    )
    has_continuation    = sum(
        1 for p in candidates if continuation_re.search(p.get("text",""))
    )
    solo_words = [
        p for p in candidates if len(p.get("text","").split()) < 5
    ]

    total_c = len(candidates)
    print(f"  Chunks starting with heading  : {starts_with_heading}/{total_c} "
          f"({100*starts_with_heading//max(1,total_c)}%)")
    print(f"  Continuation chunks (with prefix): {has_continuation}")
    print(f"  Suspiciously short chunks (<5w)  : {len(solo_words)}")

    if solo_words:
        print(f"\n  {_color('Short chunks (may be orphaned stubs):', YELLOW)}")
        for p in solo_words[:5]:
            print(f"    [{p.get('source','')} p{p.get('page','')}] '{p.get('text','')[:80]}'")


def report_table(all_payloads: list[dict], limit: int = 5, source: str | None = None) -> None:
    """Show table chunks — check table + heading stay together."""
    _header("GROUP 2: TABLE CHUNKS  (heading + rows intact check)", YELLOW)
    print(f"  Showing {limit} samples — verify ## heading + | rows in same chunk\n")

    candidates = [
        p for p in all_payloads
        if _is_table_chunk(p.get("text",""))
        and (source is None or source.lower() in p.get("source","").lower())
    ]
    candidates.sort(key=lambda p: (p.get("source",""), p.get("page") or 0))

    if not candidates:
        print(f"  {_color('No table chunks found.', YELLOW)}")
        print("  (Tables may be embedded in text chunks or not extracted yet)")
        return

    for i, p in enumerate(candidates[:limit]):
        _chunk_box(i + 1, p)

    print(f"  {_color(f'Total table chunks: {len(candidates)}', DIM)}")

    # ── Table integrity check ──
    _header("  TABLE INTEGRITY CHECK", DIM)
    print("  Checking: does each table chunk have its heading?\n")

    heading_with_table = sum(
        1 for p in candidates
        if re.search(r"(#{1,3} |ตารางที่|Table)", p.get("text","")[:150])
    )
    split_tables = sum(
        1 for p in candidates
        if re.search(r"\[\.\.\.จากหัวข้อด้านบน\]", p.get("text",""))
    )

    print(f"  Tables with heading in same chunk: {heading_with_table}/{len(candidates)} "
          f"({100*heading_with_table//max(1,len(candidates))}%)")
    print(f"  Split table continuations         : {split_tables}")

    if split_tables:
        print(f"\n  {_color('Split tables (check if content is still usable):', YELLOW)}")
        for p in candidates:
            if re.search(r"\[\.\.\.จากหัวข้อด้านบน\]", p.get("text","")):
                print(f"    [{p.get('source','')} p{p.get('page','')}] "
                      f"'{p.get('text','')[:100]}'")


def report_chart(all_payloads: list[dict], limit: int = 5, source: str | None = None) -> None:
    """Show chart/vision chunks — check flowchart logic is intact."""
    _header("GROUP 3: CHART / VISION CHUNKS  (flowchart integrity check)", MAGENTA)
    print(f"  Showing {limit} samples — verify hierarchy and → arrows preserved\n")

    candidates = [
        p for p in all_payloads
        if _is_chart_chunk(p)
        and (source is None or source.lower() in p.get("source","").lower())
    ]
    candidates.sort(key=lambda p: (p.get("source",""), p.get("page") or 0))

    if not candidates:
        print(f"  {_color('No chart/vision chunks found.', YELLOW)}")
        return

    # Show all vision chunks (usually small set) or up to limit
    show = candidates[:limit]
    for i, p in enumerate(show):
        _chunk_box(i + 1, p, show_full=True)   # full text for charts

    print(f"  {_color(f'Total chart/vision chunks: {len(candidates)}', DIM)}")

    # ── Chart integrity check ──
    _header("  CHART INTEGRITY CHECK", DIM)
    print("  Checking: flowchart structure signals\n")

    has_title      = sum(1 for p in candidates if re.search(r"##\s*(แผนภูมิ|ภาพที่|Figure|ตาราง)", p.get("text","")))
    has_arrows     = sum(1 for p in candidates if "→" in p.get("text",""))
    has_conditions = sum(1 for p in candidates if re.search(r"ถ้าใช่|ถ้าไม่|YES|NO|\[if\]", p.get("text",""), re.IGNORECASE))
    has_diagnosis  = sum(1 for p in candidates if re.search(r"วินิจฉัย|Diagnosis|Common cold|Sinusitis|Otitis", p.get("text",""), re.IGNORECASE))
    split_charts   = sum(1 for p in candidates if re.search(r"\[\.\.\.จากหัวข้อด้านบน\]", p.get("text","")))

    total_c = len(candidates)
    print(f"  Chunks with ## title heading    : {has_title}/{total_c}")
    print(f"  Chunks with → flow arrows       : {has_arrows}/{total_c}")
    print(f"  Chunks with decision conditions : {has_conditions}/{total_c}")
    print(f"  Chunks with diagnosis outcomes  : {has_diagnosis}/{total_c}")
    print(f"  Split chart continuations       : {split_charts}  "
          f"{_color('(should be 0 if size=400)', DIM)}")

    if has_title < total_c:
        missing = [
            p for p in candidates
            if not re.search(r"##\s*(แผนภูมิ|ภาพที่|Figure|ตาราง)", p.get("text",""))
        ]
        print(f"\n  {_color('Chunks missing ## title (may need re-ingest):', YELLOW)}")
        for p in missing[:3]:
            print(f"    [{p.get('source','')} p{p.get('page','')}] "
                  f"'{p.get('text','')[:100]}'")


def report_search(
    client: QdrantClient,
    query: str,
    top_k: int = 8,
    source: str | None = None,
) -> None:
    """Semantic search and display results."""
    _header(f"SEMANTIC SEARCH: '{query}'", CYAN)
    print(f"  top_k={top_k}  source_filter={source or 'all'}\n")

    results = semantic_search(client, query, top_k=top_k, source_filter=source)

    if not results:
        print("  No results found.")
        return

    for i, (score, payload) in enumerate(results):
        score_color = GREEN if score > 0.7 else (YELLOW if score > 0.5 else RED)
        print(f"{_color(f'  score={score:.4f}', score_color)}  ", end="")
        _chunk_box(i + 1, payload, highlight=query)


# ─────────────────────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect PharmBot Qdrant vector chunks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--group",
        choices=["text", "table", "chart", "all"],
        default="all",
        help="Which group to inspect (default: all)",
    )
    parser.add_argument(
        "--search",
        metavar="QUERY",
        help="Run semantic search instead of group view",
    )
    parser.add_argument(
        "--source",
        metavar="FILENAME",
        help="Filter by source filename (partial match)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Max samples per group (default: 5)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Show full chunk text (not just preview)",
    )
    args = parser.parse_args()

    # ── Connect ──────────────────────────────────────────────
    print(f"\n{_color('Connecting to Qdrant...', DIM)}", end=" ", flush=True)
    client = QdrantClient(url=QDRANT_URL, timeout=30)
    info   = client.get_collection(COLLECTION)
    print(_color(f"OK  ({info.points_count} points)", GREEN))

    # ── Semantic search mode ──────────────────────────────────
    if args.search:
        report_search(client, args.search, top_k=args.limit or 8, source=args.source)
        return

    # ── Fetch all payloads ────────────────────────────────────
    print(f"{_color('Fetching all payloads...', DIM)}", end=" ", flush=True)
    all_payloads = scroll_all(client, source_filter=args.source)
    print(_color(f"done ({len(all_payloads)} chunks)", GREEN))

    # ── Overview always shown ─────────────────────────────────
    report_overview(all_payloads)

    # ── Group reports ─────────────────────────────────────────
    grp = args.group

    if grp in ("text", "all"):
        report_text(all_payloads, limit=args.limit, source=args.source)

    if grp in ("table", "all"):
        report_table(all_payloads, limit=args.limit, source=args.source)

    if grp in ("chart", "all"):
        report_chart(all_payloads, limit=args.limit, source=args.source)

    print(f"\n{_color('Done.', BOLD + GREEN)}\n")


if __name__ == "__main__":
    main()