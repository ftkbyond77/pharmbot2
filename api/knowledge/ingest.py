"""
knowledge/ingest.py
-------------------
PDF ingestion pipeline (Phase 1):

  PDF file(s)
      │
      ▼
  OpenKB converter  ── PDF → Markdown + extract text ──▶  pages[]
  (openkb.converter.convert_document)
      │
      ▼
  Manual chunker  ──  split by CHUNK_SIZE words
      │
      ▼
  BGE-M3 embedding  (SentenceTransformer)
      │
      ▼
  Qdrant upsert

Run once (or whenever guidelines are updated):
    python -m api.knowledge.ingest --dir data/guidelines/
"""

import re
import uuid
from pathlib import Path

import fitz  # PyMuPDF — fallback & page splitting
from loguru import logger
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from sentence_transformers import SentenceTransformer

from api.config import get_settings

# ── constants ──────────────────────────────────────────────────
CHUNK_SIZE    = 450   # words per chunk
CHUNK_OVERLAP = 50    # word overlap
BATCH_SIZE    = 16    # embed N chunks at once
VECTOR_DIM    = 1024  # BGE-M3 output dim


# ── text helpers ───────────────────────────────────────────────

def _chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    words = text.split()
    chunks, start = [], 0
    while start < len(words):
        chunk = " ".join(words[start: start + size])
        if chunk.strip():
            chunks.append(chunk)
        start += size - overlap
    return chunks


def _detect_lang(text: str) -> str:
    thai = sum(1 for c in text if "\u0e00" <= c <= "\u0e7f")
    return "th" if thai / max(len(text), 1) > 0.1 else "en"


def _infer_category(text: str, filename: str) -> str:
    kw: dict[str, list[str]] = {
        "respiratory": ["ไอ", "cough", "asthma", "rhinitis", "copd", "หอบ", "หืด"],
        "allergy":     ["allerg", "แพ้", "antihistamine", "urticaria"],
        "pain":        ["ปวด", "pain", "nsaid", "analgesic", "fever", "ไข้"],
        "gi":          ["ท้อง", "gastric", "antacid", "diarrhea", "ท้องเสีย"],
        "dermatology": ["ผิวหนัง", "skin", "rash", "eczema", "แผล"],
    }
    combined = (text + filename).lower()
    for cat, words in kw.items():
        if any(w in combined for w in words):
            return cat
    return "general"


def _extract_version(filename: str) -> str:
    m = re.search(r"(20\d{2})", filename)
    return m.group(1) if m else "unknown"


# ── OpenKB converter (primary path) ───────────────────────────

def _extract_via_openkb(pdf_path: Path, kb_dir: Path) -> list[dict]:
    """
    Use openkb.converter.convert_document to turn PDF → Markdown,
    then chunk the markdown text.

    kb_dir: temp working directory for openkb state files.
    """
    try:
        from openkb.converter import convert_document

        kb_dir.mkdir(parents=True, exist_ok=True)
        result = convert_document(src=pdf_path, kb_dir=kb_dir)

        if result.skipped:
            logger.info(f"[openkb] {pdf_path.name} already indexed (hash match) — skipping")
            return []

        # Long doc (≥20 pages) → source_path is None, raw_path has the PDF
        # Fall through to PyMuPDF for these
        if result.is_long_doc:
            logger.info(f"[openkb] {pdf_path.name} is long doc — using PyMuPDF page splitter")
            return _extract_via_pymupdf(pdf_path)

        # Normal doc — source_path is the converted .md file
        if result.source_path and result.source_path.exists():
            md_text = result.source_path.read_text(encoding="utf-8")
            chunks = []
            for chunk_text in _chunk_text(md_text):
                chunks.append({
                    "text": chunk_text,
                    "metadata": {
                        "source":             pdf_path.name,
                        "lang":               _detect_lang(chunk_text),
                        "disease_cat":        _infer_category(chunk_text, pdf_path.name),
                        "guideline_version":  _extract_version(pdf_path.name),
                        "extractor":          "openkb",
                    },
                })
            logger.info(f"[openkb] {pdf_path.name} → {len(chunks)} chunks via converter")
            return chunks

        logger.warning(f"[openkb] convert_document returned no source_path for {pdf_path.name}")
        return _extract_via_pymupdf(pdf_path)

    except Exception as e:
        logger.warning(f"[openkb] converter failed ({e}) — falling back to PyMuPDF")
        return _extract_via_pymupdf(pdf_path)


# ── PyMuPDF fallback ───────────────────────────────────────────

def _extract_via_pymupdf(pdf_path: Path) -> list[dict]:
    """Page-by-page text extraction with PyMuPDF."""
    doc = fitz.open(str(pdf_path))
    chunks = []
    for page_num, page in enumerate(doc, start=1):
        text = page.get_text("text").strip()
        if not text:
            continue
        for chunk_text in _chunk_text(text):
            chunks.append({
                "text": chunk_text,
                "metadata": {
                    "source":            pdf_path.name,
                    "page":              page_num,
                    "lang":              _detect_lang(chunk_text),
                    "disease_cat":       _infer_category(chunk_text, pdf_path.name),
                    "guideline_version": _extract_version(pdf_path.name),
                    "extractor":         "pymupdf",
                },
            })
    doc.close()
    logger.info(f"[pymupdf] {pdf_path.name} → {len(chunks)} chunks")
    return chunks


# ── Qdrant helpers ─────────────────────────────────────────────

def _ensure_collection(client: QdrantClient, name: str) -> None:
    existing = {c.name for c in client.get_collections().collections}
    if name not in existing:
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
        )
        logger.info(f"[qdrant] Collection '{name}' created")
    else:
        logger.info(f"[qdrant] Collection '{name}' already exists")


def _upsert(client: QdrantClient, collection: str,
            chunks: list[dict], model: SentenceTransformer) -> None:
    texts = [c["text"] for c in chunks]
    points = []

    for i in range(0, len(texts), BATCH_SIZE):
        batch_texts  = texts[i: i + BATCH_SIZE]
        batch_chunks = chunks[i: i + BATCH_SIZE]

        vecs = model.encode(
            batch_texts,
            batch_size=BATCH_SIZE,
            normalize_embeddings=True,
            show_progress_bar=False,
        )

        for vec, chunk in zip(vecs, batch_chunks):
            points.append(PointStruct(
                id=str(uuid.uuid4()),
                vector=vec.tolist(),
                payload={"text": chunk["text"], **chunk["metadata"]},
            ))
        logger.debug(f"  embedded batch {i}–{i + len(batch_texts)}")

    client.upsert(collection_name=collection, points=points)
    logger.info(f"[qdrant] upserted {len(points)} vectors → '{collection}'")


# ── main pipeline ──────────────────────────────────────────────

def run_ingest(pdf_dir: str | Path | None = None) -> None:
    """
    Ingest all PDFs in pdf_dir into Qdrant.
    Uses openkb.converter as primary extractor, PyMuPDF as fallback.

    CLI:  python -m api.knowledge.ingest --dir data/guidelines/
    """
    cfg     = get_settings()
    pdf_dir = Path(pdf_dir or "data/guidelines")
    kb_dir  = Path(".openkb_cache")          # openkb working dir

    pdfs = list(pdf_dir.glob("*.pdf"))
    if not pdfs:
        logger.error(f"No PDFs found in {pdf_dir.resolve()}")
        return

    logger.info(f"Found {len(pdfs)} PDF(s): {[p.name for p in pdfs]}")

    # load model
    logger.info(f"Loading embedding model: {cfg.embedding_model}")
    model  = SentenceTransformer(cfg.embedding_model, device=cfg.embedding_device)

    # connect Qdrant
    qdrant = QdrantClient(url=cfg.qdrant_url)
    _ensure_collection(qdrant, cfg.qdrant_collection)

    total = 0
    for pdf in pdfs:
        logger.info(f"Processing: {pdf.name}")
        chunks = _extract_via_openkb(pdf, kb_dir)
        if not chunks:
            logger.warning(f"No chunks for {pdf.name} — skipped")
            continue
        _upsert(qdrant, cfg.qdrant_collection, chunks, model)
        total += len(chunks)

    logger.info(f"Ingestion complete — {total} total chunks indexed")


# ── CLI ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="PharmBot PDF ingestion")
    parser.add_argument("--dir", default="data/guidelines",
                        help="Directory containing PDF files")
    args = parser.parse_args()
    run_ingest(pdf_dir=args.dir)