"""
knowledge/ingest.py
-------------------
PDF ingestion pipeline — Multimodal 4-tier edition.

Pipeline per page:
  ┌─────────────────────────────────────────────────────────┐
  │  For each PDF page:                                     │
  │                                                         │
  │  1. Classify page content (text / chart / mixed)        │
  │     via PyMuPDF heuristics (fast, no LLM cost)          │
  │                                                         │
  │  2a. Text page → Docling (semantic chunking)            │
  │      If Docling yield too low → PyMuPDF text fallback   │
  │                                                         │
  │  2b. Chart / low-confidence page                        │
  │      → Render as image → Gemini Vision                  │
  │      → Structured Markdown description                  │
  │      → chunk & embed as normal text                     │
  │                                                         │
  │  3. Semantic chunk (heading-aware, not fixed-size)      │
  │                                                         │
  │  4. Embed (BGE-M3) + upsert Qdrant                      │
  │     with rich metadata: source, page, type, disease_cat │
  └─────────────────────────────────────────────────────────┘

CLI:
    python -m api.knowledge.ingest --dir data/guidelines/
    python -m api.knowledge.ingest --dir data/guidelines/ --reset
    python -m api.knowledge.ingest --dir data/guidelines/ --reset --ocr
    python -m api.knowledge.ingest --dir data/guidelines/ --reset --ocr --batch-pages 10
    python -m api.knowledge.ingest --dir data/guidelines/ --reset --no-vision
"""

from __future__ import annotations

import argparse
import base64
import gc
import hashlib
import io
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Literal

from loguru import logger
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from sentence_transformers import SentenceTransformer

from api.config import get_settings

# ─────────────────────────────────────────────────────────────
#  Memory management helpers
# ─────────────────────────────────────────────────────────────

def _flush_memory(label: str = "") -> None:
    """
    Force Python GC + CUDA cache clear.
    Call after every docling batch to prevent VRAM leak accumulation.
    torch is imported lazily — no hard dependency at module level.
    """
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
    except ImportError:
        pass  # CPU-only environment — gc.collect() is enough
    if label:
        logger.debug(f"[memory] flushed after {label}")


def _free_docling_cache() -> None:
    """
    Drop cached docling converters from memory.
    Call before starting a new large file to reclaim VRAM from previous run.
    """
    global _docling_cache
    _docling_cache.clear()
    _flush_memory("docling_cache_drop")
    logger.debug("[memory] docling converter cache cleared")


# ─────────────────────────────────────────────────────────────
#  Data types
# ─────────────────────────────────────────────────────────────

PageType = Literal["text", "chart", "mixed", "empty"]


@dataclass
class PageAnalysis:
    page_num: int            # 1-indexed
    page_type: PageType
    text: str                # raw extracted text (may be empty for chart pages)
    text_confidence: float   # 0.0–1.0 (ratio of meaningful text chars)
    has_image: bool
    has_table: bool


@dataclass
class IngestChunk:
    text: str
    source: str              # filename
    page: int | None
    chunk_type: str          # "text" | "vision_chart" | "vision_mixed"
    lang: str
    disease_cat: str
    guideline_version: str
    extractor: str           # "docling" | "pymupdf" | "vision"
    file_hash: str
    extra_meta: dict = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────
#  Docling converter (lazy, cached by config key)
# ─────────────────────────────────────────────────────────────

_docling_cache: dict[str, object] = {}


def _get_docling(use_ocr: bool, use_table: bool):
    key = f"{use_ocr}_{use_table}"
    if key not in _docling_cache:
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.pipeline_options import PdfPipelineOptions

        cfg  = get_settings()
        opts = PdfPipelineOptions(
            do_ocr=use_ocr,
            do_table_structure=use_table,
            generate_page_images=False,
        )
        _docling_cache[key] = DocumentConverter(
            format_options={"pdf": PdfFormatOption(pipeline_options=opts)}
        )
        logger.info(f"[docling] converter ready | ocr={use_ocr} table={use_table} device={cfg.docling_device}")
    return _docling_cache[key]


# ─────────────────────────────────────────────────────────────
#  Text utilities
# ─────────────────────────────────────────────────────────────

def _chunk_semantic(text: str, size: int, overlap: int) -> Iterator[str]:
    """
    Structure-aware chunker — splits on document structure, not word count.

    Split boundaries (in priority order):
    1. Markdown headings (## / ###) — from docling output
    2. Thai clinical section keywords (บทนำา, สาเหตุ, การรักษา, etc.)

    Deliberately excludes numbered-list splitting because numbered items
    inside flowcharts (1. โหนด, 2. โหนด) must stay together — splitting
    them destroys the decision logic needed for clinical Q&A.

    For sections exceeding `size` words, sliding window is applied with
    the section heading prepended to every continuation chunk so each
    vector is fully self-contained for retrieval.

    Guarantees:
    - Drug dosage table + its heading → always 1 chunk
    - Flowchart/algorithm nodes → always 1 chunk (if ≤ size)
    - Long treatment sections → split with heading prefix on sub-chunks
    """
    BOUNDARY = re.compile(
        r"(?m)(?="
        r"^#{1,3} "
        r"|^(?:บทนำ[าา]?|คำ[าา]นำ[าา]?|สาเหตุ|ระบาดวิทยา|ลักษณะอาการ|"
        r"อาการ(?:ทางคลินิก)?|การวินิจฉัย|การตรวจ|การรักษา|"
        r"ภาวะแทรกซ้อน|การป้องกัน|เอกสารอ้างอิง|ข้อแนะนำ[าา]?|"
        r"คำ[าา]จำ[าา]กัดความ|การจำ[าา]แนก|ปัจจัยเสี่ยง|"
        r"สรุป|บทสรุป|ผลการรักษา)"
        r")"
    )

    def _extract_heading(s: str) -> str:
        first = s.strip().splitlines()[0] if s.strip() else ""
        return first[:120]

    def _split_section(section: str, heading: str) -> Iterator[str]:
        words = section.split()
        if not words:
            return
        if len(words) <= size:
            yield section.strip()
            return
        heading_words = heading.split() if heading else []
        step = max(1, size - overlap)
        start = 0
        while start < len(words):
            if start == 0:
                chunk_words = words[start: start + size]
            else:
                prefix      = heading_words + ["[...จากหัวข้อด้านบน]"]
                available   = size - len(prefix)
                chunk_words = prefix + words[start: start + max(1, available)]
            chunk = " ".join(chunk_words).strip()
            if chunk:
                yield chunk
            start += step

    # Merge tiny stub sections (< 8 words) into next section
    # to avoid floating one-line heading chunks in retrieval
    raw = BOUNDARY.split(text)
    merged: list[str] = []
    i = 0
    while i < len(raw):
        sec = raw[i].strip()
        if not sec:
            i += 1
            continue
        if len(sec.split()) < 8 and (i + 1) < len(raw):
            nxt = raw[i + 1].strip()
            if nxt:
                merged.append(sec + "\n\n" + nxt)
                i += 2
                continue
        merged.append(sec)
        i += 1

    for section in merged:
        heading = _extract_heading(section)
        yield from _split_section(section, heading)


def _detect_lang(text: str) -> str:
    thai = sum(1 for c in text if "\u0e00" <= c <= "\u0e7f")
    return "th" if thai / max(len(text), 1) > 0.08 else "en"


def _is_cid_proxy(c: str) -> bool:
    """True if character is in the CID proxy ranges used by broken Thai fonts."""
    cp = ord(c)
    return 0x00C0 <= cp <= 0x00FF or 0x0100 <= cp <= 0x024F


def _has_any_cid(text: str) -> bool:
    """
    Zero-tolerance CID detector — used at PAGE level where Vision can rescue.

    Returns True if the text contains ANY evidence of Thai CID font encoding.
    Uses high-precision signals to avoid false positives from real Latin text
    (drug names like amoxicillin, author names like Shaikh, journal names).

    Thai CID font signature:
      CID proxy chars (U+00C0-024F) appear ADJACENT to Thai vowel/tone marks
      (U+0E30-0E4F). This pattern is physically impossible in real Latin text
      because Latin scripts don't use Thai vowel marks.

      Examples:
        þĆึ = CID_proxy + CID_proxy + Thai_vowel(ึ)  → Thai CID font
        Ăาวะ = CID_proxy + Thai_vowel(า) + Thai       → Thai CID font
        amoxicillin = all ASCII                         → real Latin ✅
        Shaikh N.   = all ASCII                         → real Latin ✅

    Two checks, either triggers a positive:
      1. CID chars adjacent to Thai vowel/tone marks (definitive Thai CID signal)
      2. High-density CID ratio (> 20%) covering non-reference text
    """
    if not text or len(text) < 10:
        return False

    # ── Check 1: CID adjacent to Thai vowel/tone mark (zero false positives) ──
    # Thai vowel/tone marks: U+0E30–0E4F (า ิ ี ึ ื ุ ู  ่ ้ ๊ ๋ ็ etc.)
    # If a CID proxy char appears next to one of these, it's definitively CID.
    cid_adj_thai = re.findall(
        r"[À-ÿĀ-ɏ][ะ-๏]"   # CID before Thai vowel
        r"|[ะ-๏][À-ÿĀ-ɏ]",  # Thai vowel before CID
        text
    )
    if cid_adj_thai:
        return True

    # ── Check 2: High CID density fallback (catches CID-only pages) ──
    # Only applies when Thai vowel adjacency wasn't found.
    # Threshold 20% is deliberately high to avoid flagging reference pages
    # (which may have author initials like "A.", "B." but no actual CID).
    cid_count       = sum(1 for c in text if _is_cid_proxy(c))
    total_non_space = max(len(re.sub(r"\s+", "", text)), 1)
    if cid_count / total_non_space > 0.20:
        return True

    return False


def _is_cid_garbage(text: str) -> bool:
    """
    Threshold-based CID detector — used at CHUNK level as final safety net.

    Less aggressive than _has_any_cid (used at page level) because at chunk
    level we don't have a Vision fallback for individual sub-chunks from the
    same page. Used only to prevent garbage from entering Qdrant if it somehow
    slipped past the page-level filter.

    Uses the same CID proxy definition but with relaxed thresholds.
    """
    if not text or len(text) < 20:
        return False

    # Reuse the adjacency check — still zero false positive
    cid_adj_thai = re.findall(
        r"[\u00c0-\u00ff\u0100-\u024f][\u0e30-\u0e4f]"
        r"|[\u0e30-\u0e4f][\u00c0-\u00ff\u0100-\u024f]",
        text
    )
    if cid_adj_thai:
        return True

    cid_chars       = sum(1 for c in text if _is_cid_proxy(c))
    total_non_space = max(len(re.sub(r"\s+", "", text)), 1)
    cid_ratio       = cid_chars / total_non_space

    # Chunk-level: 12% threshold (same as before — reasonable for sub-chunks)
    if cid_ratio > 0.12:
        return True

    clusters = re.findall(r"[\u00c0-\u00ff\u0100-\u024f]{3,}", text)
    if clusters:
        cluster_chars = sum(len(c) for c in clusters)
        if cluster_chars / total_non_space > 0.06:
            return True
        if len(clusters) > 8:
            return True

    isolated = re.findall(r"(?<!\S)[\u00c0-\u00ff\u0100-\u024f](?!\S)", text)
    if len(isolated) > 12 and len(isolated) / total_non_space > 0.07:
        return True

    return False


def _infer_category(text: str, filename: str) -> str:
    kw: dict[str, list[str]] = {
        "respiratory": ["ไอ", "cough", "asthma", "rhinitis", "copd", "หอบ", "หืด",
                        "bronchitis", "uri", "upper respiratory", "acute respiratory"],
        "allergy":     ["allerg", "แพ้", "antihistamine", "urticaria", "ลมพิษ"],
        "pain":        ["ปวด", "pain", "nsaid", "analgesic", "fever", "ไข้",
                        "paracetamol", "ibuprofen"],
        "gi":          ["ท้อง", "gastric", "antacid", "diarrhea", "ท้องเสีย",
                        "nausea", "คลื่นไส้"],
        "dermatology": ["ผิวหนัง", "skin", "rash", "eczema", "แผล", "dermatitis"],
        "ent":         ["หู", "คอ", "จมูก", "ear", "throat", "nose", "sinusitis",
                        "pharyngitis", "tonsil", "otitis", "laryngitis"],
        "pediatric":   ["เด็ก", "children", "pediatric", "child", "infant", "ทารก"],
    }
    combined = (text[:600] + " " + filename).lower()
    for cat, words in kw.items():
        if any(w in combined for w in words):
            return cat
    return "general"


def _extract_version(filename: str) -> str:
    m = re.search(r"(20\d{2})", filename)
    return m.group(1) if m else "unknown"


def _file_hash(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def _page_count(pdf_path: Path) -> int:
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
        n = len(doc)
        doc.close()
        return n
    except Exception:
        return 0


# ─────────────────────────────────────────────────────────────
#  Step 1 — Page Classification (heuristic, no LLM cost)
# ─────────────────────────────────────────────────────────────

def _has_raster_content(page) -> bool:
    """
    Detect if a page is visually content-rich even when
    PyMuPDF reports no embedded image objects.
    Handles Form XObjects (common in scanned/image PDFs like AAFP)
    by sampling pixel brightness variance on a low-res render.
    """
    try:
        import fitz
        # Render at low DPI for speed (72dpi = 1:1 point)
        mat = fitz.Matrix(0.3, 0.3)   # ~22dpi — tiny, just for variance check
        pix = page.get_pixmap(matrix=mat, alpha=False, colorspace=fitz.csGRAY)
        samples = pix.samples         # raw bytes
        if len(samples) < 100:
            return False
        # Compute mean brightness — pure white page = 255, content page < ~240
        mean_brightness = sum(samples) / len(samples)
        return mean_brightness < 240  # non-white → has visual content
    except Exception:
        return False


def _classify_pages(pdf_path: Path) -> list[PageAnalysis]:
    """
    Analyse each page using PyMuPDF heuristics:
      - Count meaningful text characters → confidence score
      - Detect embedded images (standard XObjects)
      - Detect raster content via pixel variance (Form XObjects, scanned PDFs)
      - Detect table-like structures (many horizontal lines)

    Page types:
      text   → mostly text, high confidence
      chart  → image/diagram dominant, very low text
      mixed  → has both text and image content
      empty  → blank or near-blank page
    """
    import fitz

    cfg = get_settings()
    doc = fitz.open(str(pdf_path))
    analyses: list[PageAnalysis] = []

    for i, page in enumerate(doc):
        text             = page.get_text("text").strip()
        page_area        = page.rect.width * page.rect.height
        meaningful_chars = len(re.sub(r"\s+", "", text))
        confidence       = min(1.0, meaningful_chars / max(page_area * 0.003, 1))

        # ── Image / visual detection — 3 methods ─────────────────
        # Method 1: Standard embedded image XObjects (JPEG, PNG in PDF)
        image_list    = page.get_images(full=True)
        has_std_image = len(image_list) > 0

        # Method 2: Raster Form XObjects (scanned PDFs, AAFP-original style)
        has_raster = _has_raster_content(page) if not has_std_image else True

        # Method 3: Vector-text bypass — "Print to PDF" renders fonts as paths
        # Symptom: text=0, image=0, BUT drawings count >> 500
        # Threshold 500: normal diagrams have ~10-100 paths; vector-text has 3000+
        paths      = page.get_drawings()
        n_paths    = len(paths)
        has_vector_text = (meaningful_chars == 0 and n_paths > 500)

        has_image = has_std_image or has_raster or has_vector_text

        # Table heuristic: many short horizontal lines
        h_lines   = sum(
            1 for p in paths
            if abs(p["rect"].height) < 3 and p["rect"].width > 30
        )
        has_table = h_lines >= 4

        # ── Classify ──────────────────────────────────────────────
        if meaningful_chars < 30 and has_image:
            # No readable text but has visual content → send to Vision
            page_type: PageType = "chart"
        elif confidence < cfg.ingest_vision_confidence_threshold and has_image:
            # Some text but image-heavy → Vision for the visual parts
            page_type = "mixed"
        elif meaningful_chars < 10 and not has_image:
            # Truly blank
            page_type = "empty"
        elif meaningful_chars < 10 and has_image:
            # Text-less but has something visual
            page_type = "chart"
        else:
            page_type = "text"

        if has_vector_text:
            logger.debug(
                f"[classify] p{i+1}: vector-text PDF detected "
                f"(paths={n_paths}) → forcing Vision"
            )

        analyses.append(PageAnalysis(
            page_num=i + 1,
            page_type=page_type,
            text=text,
            text_confidence=round(confidence, 3),
            has_image=has_image,
            has_table=has_table,
        ))

    doc.close()
    logger.debug(
        f"[classify] {pdf_path.name}: "
        f"text={sum(1 for a in analyses if a.page_type=='text')} "
        f"chart={sum(1 for a in analyses if a.page_type=='chart')} "
        f"mixed={sum(1 for a in analyses if a.page_type=='mixed')} "
        f"empty={sum(1 for a in analyses if a.page_type=='empty')}"
    )
    return analyses


# ─────────────────────────────────────────────────────────────
#  Step 2b — Vision bypass for charts/diagrams
# ─────────────────────────────────────────────────────────────

_VISION_PROMPT = """\
You are a medical document analyst. This image is a page from a Thai/English clinical guideline PDF.

Your output will be stored in a vector database for retrieval. It is critical that:
1. Chart/diagram titles are preserved exactly as headings so retrieval can find them by name.
2. Hierarchy and flow relationships are preserved so clinical decisions can be reconstructed.
3. All text visible in the image is captured.

## Output Rules

### ALWAYS start with a title heading:
- If you see a label like "แผนภูมิที่ 1", "ภาพที่ 2", "Figure 3", "ตารางที่ 1" — use it EXACTLY as the ## heading.
- If there is a descriptive title (e.g. "แนวทางการประเมินผู้ป่วยเด็กที่มีน้ำมูก") — include it after the label.
- If no label is visible, infer a short descriptive title from the content.
- Format: `## แผนภูมิที่ N: [ชื่อแผนภูมิ]` or `## ตารางที่ N: [ชื่อตาราง]`

### For flowcharts / decision trees / treatment algorithms:
- Preserve the EXACT hierarchy: root node → branches → leaf nodes.
- Use indentation to show depth level.
- Use `→` for flow direction.
- Use `[ถ้าใช่]` / `[ถ้าไม่]` or `[YES]` / `[NO]` for decision branches.
- Number each decision node sequentially.
- Capture ALL boxes/diamonds/labels — do not skip any node.
- Format:
  ```
  ## แผนภูมิที่ N: [ชื่อ]
  **จุดเริ่มต้น**: [เงื่อนไขเริ่มต้น]
  1. [โหนดแรก]
     - [ถ้าใช่] → [ขั้นตอนถัดไป หรือการวินิจฉัย]
     - [ถ้าไม่] → [ทางเลือกอื่น]
  2. [โหนดถัดไป]
     - ...
  **ผลลัพธ์**: [การวินิจฉัยสุดท้าย / การรักษา]
  ```

### For tables:
- Reproduce as full Markdown table with all rows and columns.
- Use `## ตารางที่ N: [ชื่อตาราง]` as heading.
- Do not skip any row, even if it looks like a sub-row.

### For graphs / clinical charts:
- Describe: title, axes labels, units, key data points, and clinical conclusion.

### For pages mixing text and visuals:
- First transcribe all body text verbatim.
- Then describe each visual element with its title heading.

Language: respond in Thai if the document is Thai, English if English. Match the document language exactly.
Be exhaustive — this description is the ONLY representation of this page in the system.
"""


def _render_page_image(pdf_path: Path, page_num: int, dpi: int) -> bytes | None:
    """Render a single PDF page to PNG bytes using PyMuPDF."""
    try:
        import fitz
        doc  = fitz.open(str(pdf_path))
        page = doc[page_num - 1]   # 0-indexed
        mat  = fitz.Matrix(dpi / 72, dpi / 72)
        pix  = page.get_pixmap(matrix=mat, alpha=False)
        img_bytes = pix.tobytes("png")
        doc.close()
        return img_bytes
    except Exception as exc:
        logger.warning(f"[vision] render failed page {page_num}: {exc}")
        return None


def _vision_describe_page(
    pdf_path: Path,
    page_num: int,
    page_type: PageType,
) -> str | None:
    """
    Send page image to Gemini Vision.
    Returns markdown description string, or None on failure.
    """
    cfg       = get_settings()
    img_bytes = _render_page_image(pdf_path, page_num, cfg.ingest_vision_dpi)
    if img_bytes is None:
        return None

    try:
        from google import genai
        from google.genai import types as genai_types

        client = genai.Client(api_key=cfg.gemini_api_key)

        img_b64    = base64.b64encode(img_bytes).decode()
        image_part = genai_types.Part.from_bytes(
            data=base64.b64decode(img_b64),
            mime_type="image/png",
        )

        # Build context note — specific to page type for better model guidance
        if page_type == "chart":
            context_note = (
                f"Page {page_num}: This page contains a flowchart, decision tree, "
                f"or treatment algorithm. Extract ALL nodes and flow relationships. "
                f"Look for any แผนภูมิที่/ภาพที่/Figure label and use it as the title."
            )
        elif page_type == "mixed":
            context_note = (
                f"Page {page_num}: This page contains both body text and visual "
                f"elements (tables, diagrams, or charts). "
                f"Transcribe all text first, then describe each visual with its label/title."
            )
        else:
            # CID pages or unknown — full render, ask to read everything
            context_note = (
                f"Page {page_num}: This page contains Thai text and/or visual content. "
                f"Please read and transcribe ALL visible text carefully "
                f"(the automated text extraction failed for this page). "
                f"Also describe any diagrams, tables, or flowcharts present."
            )
        prompt_text = f"{context_note}\n\n{_VISION_PROMPT}"

        # Safety settings: allow medical content
        # Without this, Gemini may block drug/disease terminology in images
        safety_settings = [
            genai_types.SafetySetting(
                category=genai_types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                threshold=genai_types.HarmBlockThreshold.BLOCK_NONE,
            ),
            genai_types.SafetySetting(
                category=genai_types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                threshold=genai_types.HarmBlockThreshold.BLOCK_NONE,
            ),
            genai_types.SafetySetting(
                category=genai_types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                threshold=genai_types.HarmBlockThreshold.BLOCK_NONE,
            ),
            genai_types.SafetySetting(
                category=genai_types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                threshold=genai_types.HarmBlockThreshold.BLOCK_NONE,
            ),
        ]

        response = client.models.generate_content(
            model=cfg.ingest_vision_model,
            contents=[prompt_text, image_part],
            config=genai_types.GenerateContentConfig(
                max_output_tokens=cfg.ingest_vision_max_tokens,
                temperature=0.1,
                safety_settings=safety_settings,
            ),
        )

        # ── Robust text extraction ────────────────────────────────
        # response.text  : shortcut, can be None even with BLOCK_NONE
        # response.candidates : can be None (not just empty list) when
        #   Gemini hard-blocks at API level regardless of safety settings
        # Strategy: try 3 paths, degrade gracefully, never crash
        result = None

        # Path 1: .text shortcut (fastest)
        try:
            if response.text is not None:
                result = response.text.strip() or None
        except Exception:
            pass

        # Path 2: walk candidates[0].content.parts
        if result is None:
            try:
                candidates = response.candidates or []
                if candidates and candidates[0].content and candidates[0].content.parts:
                    text_parts = [
                        p.text for p in candidates[0].content.parts
                        if hasattr(p, "text") and p.text
                    ]
                    result = "\n".join(text_parts).strip() or None
            except Exception:
                pass

        # Path 3: nothing worked — log details and skip
        if result is None:
            finish = "UNKNOWN"
            try:
                candidates = response.candidates or []
                if candidates:
                    finish = str(candidates[0].finish_reason)
            except Exception:
                pass
            logger.warning(
                f"[vision] page {page_num} ({page_type}): "
                f"no text returned (finish_reason={finish}) — skipping"
            )
            return None

        logger.debug(f"[vision] page {page_num} ({page_type}): {len(result)} chars")
        return result

    except Exception as exc:
        logger.warning(f"[vision] Gemini call failed for page {page_num}: {exc}")
        return None


# ─────────────────────────────────────────────────────────────
#  Step 2a — Docling full-file (text-layer, semantic)
# ─────────────────────────────────────────────────────────────

def _docling_full_markdown(pdf_path: Path) -> str:
    """
    Run docling on the full PDF (text-layer only, no OCR/layout model).
    Fast + low memory. Only called for small files (< INGEST_TIER1_MAX_PAGES).
    Flushes memory after completion regardless of success/failure.
    """
    md = ""
    try:
        converter = _get_docling(use_ocr=False, use_table=True)
        result    = converter.convert(str(pdf_path))
        md        = result.document.export_to_markdown() or ""
        del result   # release docling result object immediately
    except Exception as exc:
        logger.warning(f"[docling:full] {pdf_path.name} failed: {exc}")
    finally:
        _flush_memory("docling_full")
    return md


def _docling_batch_markdown(pdf_path: Path, batch_pages: int) -> dict[tuple[int, int], str]:
    """
    Batch OCR: split into temp PDFs of batch_pages, run docling on each.

    Returns:
        {(start_page, end_page): markdown_text}  — 1-indexed page ranges
        Callers can use the page range to route CID-garbage batches to Vision.

    Memory safety:
      - Each batch PDF is deleted immediately after processing (not at end)
      - gc.collect() + cuda.empty_cache() called after EVERY batch
      - DL frameworks (PyTorch/ONNX) hold VRAM until explicitly released;
        without this flush, VRAM accumulates and OOMs on later batches.
    """
    import fitz

    n_pages  = _page_count(pdf_path)
    tmp_dir  = Path(tempfile.mkdtemp(prefix="pharmbot_batch_"))
    results: dict[tuple[int, int], str] = {}
    n_ok = 0
    n_fail = 0

    try:
        converter = _get_docling(use_ocr=True, use_table=True)

        for start in range(0, n_pages, batch_pages):
            end        = min(start + batch_pages, n_pages)
            # Convert to 1-indexed for page_range key
            page_range = (start + 1, end)
            batch_path = tmp_dir / f"batch_{start:04d}.pdf"

            # ── Split: extract page range into a small temp PDF ───
            src = out = None
            try:
                src = fitz.open(str(pdf_path))
                out = fitz.open()
                out.insert_pdf(src, from_page=start, to_page=end - 1)
                out.save(str(batch_path))
            except Exception as exc:
                logger.warning(f"[docling:batch] split p{start+1}–{end} failed: {exc}")
                n_fail += 1
                continue
            finally:
                if out: out.close()
                if src: src.close()

            # ── Convert batch with docling ─────────────────────────
            md = ""
            try:
                result = converter.convert(str(batch_path))
                md     = result.document.export_to_markdown() or ""
                if md.strip():
                    results[page_range] = md
                    n_ok += 1
                logger.debug(f"[docling:batch] p{start+1}–{end}: {len(md)} chars")
            except Exception as exc:
                logger.warning(f"[docling:batch] p{start+1}–{end} failed: {exc}")
                n_fail += 1
            finally:
                try:
                    del result
                except NameError:
                    pass
                _flush_memory(f"batch_p{start+1}_{end}")
            # Delete temp PDF immediately after use
            batch_path.unlink(missing_ok=True)

        _flush_memory("batch_loop_end")

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    total_chars = sum(len(v) for v in results.values())
    logger.info(
        f"[docling:batch] {pdf_path.name}: "
        f"{n_ok}/{n_ok+n_fail} batches OK, {total_chars} total chars"
    )
    return results


# ─────────────────────────────────────────────────────────────
#  Step 2a fallback — PyMuPDF raw text
# ─────────────────────────────────────────────────────────────

def _pymupdf_text(pdf_path: Path) -> tuple[dict[int, str], set[int]]:
    """
    Extract text per page via PyMuPDF.

    Zero-tolerance CID policy: uses _has_any_cid() (not threshold-based)
    so ANY page containing CID chars adjacent to Thai vowels → Vision.
    This eliminates mixed pages like references pages where most text is
    clean but a few body lines are CID-corrupted.

    Returns:
      clean_pages : {page_num: text}  — confirmed 100% clean pages
      cid_pages   : {page_num}        — any CID detected → Vision rescue
    """
    import fitz
    doc        = fitz.open(str(pdf_path))
    clean: dict[int, str] = {}
    cid:   set[int]       = set()

    for i, page in enumerate(doc):
        text     = page.get_text("text").strip()
        page_num = i + 1

        if not text or len(text) < 20:
            continue

        # Zero-tolerance: _has_any_cid catches even a single CID+Thai vowel pair
        if _has_any_cid(text):
            cid.add(page_num)
            logger.debug(f"[pymupdf] p{page_num}: CID detected → Vision rescue")
        else:
            clean[page_num] = text

    doc.close()
    if cid:
        logger.info(
            f"[pymupdf] {pdf_path.name}: "
            f"{len(clean)} clean pages, {len(cid)} CID pages {sorted(cid)[:10]}{'...' if len(cid)>10 else ''}"
        )
    return clean, cid


# ─────────────────────────────────────────────────────────────
#  Chunk builder
# ─────────────────────────────────────────────────────────────

def _build_chunks(
    text: str,
    filename: str,
    fhash: str,
    extractor: str,
    chunk_type: str,
    page: int | None = None,
    extra_meta: dict | None = None,
) -> tuple[list[IngestChunk], bool]:
    """
    Build IngestChunk objects from text.

    Returns:
        (chunks, was_cid_garbage)
        - chunks         : clean IngestChunk list (never contains CID garbage)
        - was_cid_garbage: True if ALL sub-chunks were dropped as CID garbage
                           → caller should re-route this page to Vision

    CID garbage gate: every sub-chunk is checked with _is_cid_garbage().
    Garbage sub-chunks are dropped and logged. If every sub-chunk of a
    page is garbage, the page is flagged for Vision re-processing so no
    data is silently lost — it is rescued by Gemini Vision instead.
    """
    cfg       = get_settings()
    chunks    = []
    n_total   = 0
    n_dropped = 0

    for t in _chunk_semantic(text, cfg.chunk_size, cfg.chunk_overlap):
        if not t.strip():
            continue
        n_total += 1

        # ── CID garbage gate ─────────────────────────────────
        if _is_cid_garbage(t):
            n_dropped += 1
            logger.debug(
                f"[chunk_gate] CID garbage dropped "
                f"({filename} p{page}): '{t[:60]}...'"
            )
            continue

        chunks.append(IngestChunk(
            text=t,
            source=filename,
            page=page,
            chunk_type=chunk_type,
            lang=_detect_lang(t),
            disease_cat=_infer_category(t, filename),
            guideline_version=_extract_version(filename),
            extractor=extractor,
            file_hash=fhash,
            extra_meta=extra_meta or {},
        ))

    # Flag page as fully-garbage if every sub-chunk was dropped
    all_garbage = (n_total > 0 and n_dropped == n_total)

    if n_dropped:
        level = "warning" if all_garbage else "debug"
        getattr(logger, level)(
            f"[chunk_gate] {filename} p{page}: "
            f"dropped {n_dropped}/{n_total} CID sub-chunk(s) "
            f"extractor='{extractor}'"
            + (" → page flagged for Vision re-route" if all_garbage else "")
        )

    return chunks, all_garbage


# ─────────────────────────────────────────────────────────────
#  Main extractor orchestrator
# ─────────────────────────────────────────────────────────────

def _extract(
    pdf_path: Path,
    use_ocr: bool = False,
    batch_pages: int = 15,
    use_vision: bool = True,
) -> list[IngestChunk]:
    """
    Full 4-tier extraction:

    Tier 1: Docling text-layer on full file
            → if yield OK → done
            → if yield low AND use_ocr → Tier 2
            → if yield low AND not use_ocr → Tier 3

    Tier 2: Docling batched OCR (anti-OOM)
            → supplements with Tier 3 for still-missing content

    Tier 3: PyMuPDF raw text
            → reliable baseline

    Vision: For pages classified as chart/mixed (parallel to above)
            → Vision LLM describes visual content as Markdown
    """
    cfg      = get_settings()
    n_pages  = _page_count(pdf_path)
    fhash    = _file_hash(pdf_path)
    filename = pdf_path.name
    min_ok   = max(3, int(n_pages * cfg.ingest_min_chunks_ratio))

    logger.info(f"[extract] {filename} | {n_pages} pages | ocr={use_ocr} vision={use_vision}")

    # ── Step 1: Classify all pages ────────────────────────────
    page_analyses = _classify_pages(pdf_path)
    text_pages   = [a for a in page_analyses if a.page_type in ("text", "mixed")]
    # Include "empty" pages that have images — these are image-only pages
    # (e.g. scanned PDFs like AAFP where every page is a rasterised image)
    visual_pages = [
        a for a in page_analyses
        if a.page_type in ("chart", "mixed")
        or (a.page_type == "empty" and a.has_image)
    ]

    all_chunks: list[IngestChunk] = []

    # ── Step 2a: Docling text extraction ──────────────────────
    # Skip Tier 1 for large files to avoid std::bad_alloc
    # Threshold from config: INGEST_TIER1_MAX_PAGES (default 20)
    tier1_skip = n_pages > cfg.ingest_tier1_max_pages
    tier1_oom  = False
    md_full    = ""

    if tier1_skip:
        logger.info(
            f"[extract] Tier 1: SKIPPED ({n_pages} pages > "
            f"tier1_max={cfg.ingest_tier1_max_pages}) → straight to Tier 2+3"
        )
    else:
        logger.info(f"[extract] Tier 1: docling text-layer ({n_pages} pages)")
        try:
            md_full = _docling_full_markdown(pdf_path)
        except (MemoryError, Exception) as exc:
            if "bad_alloc" in str(exc).lower() or isinstance(exc, MemoryError):
                logger.warning(f"[extract] Tier 1 OOM on {filename} — escalating")
                tier1_oom = True
            else:
                logger.warning(f"[extract] Tier 1 error: {exc}")

    # Pages discovered to contain CID garbage — accumulated across all tiers
    # so Vision can rescue them at the end (one pass, no duplicates)
    cid_rescue_pages: set[int] = set()

    # Helper: make PageAnalysis for a CID page to send to Vision
    existing_vision_page_nums = {a.page_num for a in visual_pages}
    def _queue_cid_for_vision(page_num: int) -> None:
        if page_num not in existing_vision_page_nums and page_num not in cid_rescue_pages:
            cid_rescue_pages.add(page_num)

    # ── Tier 1: Docling text-layer ────────────────────────────
    docling_chunks: list[IngestChunk] = []
    if md_full.strip():
        raw_chunks, all_garbage = _build_chunks(
            md_full, filename, fhash, extractor="docling_text", chunk_type="text"
        )
        docling_chunks = raw_chunks
        # Tier 1 processes the full file as one blob — if everything is garbage,
        # individual page numbers aren't available; we'll catch them in Tier 3

    logger.info(
        f"[extract] Tier 1 → {len(docling_chunks)} chunks "
        f"(min_ok={min_ok} skip={tier1_skip} oom={tier1_oom})"
    )

    if len(docling_chunks) >= min_ok and not tier1_oom and not tier1_skip:
        all_chunks.extend(docling_chunks)
    else:
        if use_ocr:
            # ── Tier 2: batched docling OCR ───────────────────
            # Returns {(start_page, end_page): markdown} per batch.
            # Each batch is checked for CID garbage individually so we know
            # WHICH page range to send to Vision if OCR output is garbage.
            logger.info(f"[extract] Tier 2: docling batched OCR (batch={batch_pages})")
            batch_results = _docling_batch_markdown(pdf_path, batch_pages)
            batch_chunks: list[IngestChunk] = []

            for (p_start, p_end), md in batch_results.items():
                if not md.strip():
                    continue
                b_chunks, all_garbage = _build_chunks(
                    md, filename, fhash,
                    extractor="docling_ocr", chunk_type="text",
                    page=p_start,   # tag with first page of batch
                )
                if all_garbage:
                    # Entire batch is CID garbage → queue every page for Vision
                    for p in range(p_start, p_end + 1):
                        _queue_cid_for_vision(p)
                    logger.info(
                        f"[extract] Tier 2 batch p{p_start}–{p_end}: "
                        f"all CID garbage → queued for Vision rescue"
                    )
                else:
                    batch_chunks.extend(b_chunks)

            logger.info(f"[extract] Tier 2 → {len(batch_chunks)} chunks")
            all_chunks.extend(batch_chunks)

        # ── Tier 3: PyMuPDF page-by-page ─────────────────────
        # Processes per-page so CID pages can be individually routed to Vision.
        logger.info(f"[extract] Tier 3: PyMuPDF full sweep (with CID detection)")
        pymupdf_clean, pymupdf_cid = _pymupdf_text(pdf_path)
        pymupdf_chunks: list[IngestChunk] = []

        for page_num, text in pymupdf_clean.items():
            page_chunks, page_all_garbage = _build_chunks(
                text, filename, fhash,
                extractor="pymupdf", chunk_type="text", page=page_num,
            )
            if page_all_garbage:
                # PyMuPDF reported "clean" but _build_chunks gate caught garbage
                # (partial CID page that passed page-level threshold)
                _queue_cid_for_vision(page_num)
                logger.info(
                    f"[extract] Tier 3 p{page_num}: partially-CID page "
                    f"→ queued for Vision rescue"
                )
            else:
                pymupdf_chunks.extend(page_chunks)

        # PyMuPDF CID pages (detected at page level)
        for p in sorted(pymupdf_cid):
            _queue_cid_for_vision(p)

        logger.info(
            f"[extract] Tier 3 → {len(pymupdf_chunks)} chunks from "
            f"{len(pymupdf_clean)} clean pages "
            f"({len(pymupdf_cid)} page-level CID + "
            f"{len(cid_rescue_pages) - len(pymupdf_cid)} chunk-level CID → Vision)"
        )
        all_chunks.extend(pymupdf_chunks)

        # Add all CID pages to Vision queue (deduped against existing visual_pages)
        if cid_rescue_pages:
            cid_analyses = [
                PageAnalysis(
                    page_num=p,
                    page_type="mixed",
                    text="",
                    text_confidence=0.0,
                    has_image=True,
                    has_table=False,
                )
                for p in sorted(cid_rescue_pages)
            ]
            visual_pages = visual_pages + cid_analyses
            logger.info(
                f"[extract] {len(cid_analyses)} CID pages queued for Vision rescue"
            )

    # ── Vision: chart/mixed/CID pages ────────────────────────
    if use_vision and visual_pages:
        logger.info(
            f"[extract] Vision bypass: {len(visual_pages)} pages "
            f"({[a.page_num for a in visual_pages]})"
        )
        vision_chunks: list[IngestChunk] = []

        for analysis in visual_pages:
            description = _vision_describe_page(
                pdf_path, analysis.page_num, analysis.page_type
            )
            if description:
                c_type = "vision_chart" if analysis.page_type == "chart" else "vision_mixed"
                raw_v, _ = _build_chunks(
                    description, filename, fhash,
                    extractor="vision", chunk_type=c_type,
                    page=analysis.page_num,
                    extra_meta={"original_page_type": analysis.page_type},
                )
                vision_chunks.extend(raw_v)

        logger.info(f"[extract] Vision → {len(vision_chunks)} chunks")
        all_chunks.extend(vision_chunks)
    elif visual_pages and not use_vision:
        logger.warning(
            f"[extract] {len(visual_pages)} chart/mixed pages skipped (vision disabled)"
        )

    logger.info(
        f"[extract] {filename}: {len(all_chunks)} total chunks | "
        f"breakdown: "
        f"docling={sum(1 for c in all_chunks if 'docling' in c.extractor)} "
        f"pymupdf={sum(1 for c in all_chunks if c.extractor=='pymupdf')} "
        f"vision={sum(1 for c in all_chunks if c.extractor=='vision')}"
    )
    return all_chunks


# ─────────────────────────────────────────────────────────────
#  Qdrant helpers
# ─────────────────────────────────────────────────────────────

def _ensure_collection(client: QdrantClient, name: str, dim: int) -> None:
    existing = {c.name for c in client.get_collections().collections}
    if name not in existing:
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )
        logger.info(f"[qdrant] collection '{name}' created (dim={dim})")
    else:
        logger.info(f"[qdrant] collection '{name}' exists")


def _delete_collection(client: QdrantClient, name: str) -> None:
    if name in {c.name for c in client.get_collections().collections}:
        client.delete_collection(name)
        logger.info(f"[qdrant] collection '{name}' deleted")


def _upsert(
    client: QdrantClient,
    collection: str,
    chunks: list[IngestChunk],
    model: SentenceTransformer,
    batch_size: int,
) -> None:
    texts  = [c.text for c in chunks]
    points: list[PointStruct] = []

    for i in range(0, len(texts), batch_size):
        batch_texts  = texts[i: i + batch_size]
        batch_chunks = chunks[i: i + batch_size]

        vecs = model.encode(
            batch_texts,
            batch_size=batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        for vec, chunk in zip(vecs, batch_chunks):
            payload: dict = {
                "text":              chunk.text,
                "source":            chunk.source,
                "lang":              chunk.lang,
                "disease_cat":       chunk.disease_cat,
                "guideline_version": chunk.guideline_version,
                "extractor":         chunk.extractor,
                "chunk_type":        chunk.chunk_type,
                "file_hash":         chunk.file_hash,
            }
            if chunk.page is not None:
                payload["page"] = chunk.page
            payload.update(chunk.extra_meta)

            points.append(PointStruct(
                id=str(uuid.uuid4()),
                vector=vec.tolist(),
                payload=payload,
            ))
        logger.debug(f"  embedded [{i}:{i + len(batch_texts)}]")

    client.upsert(collection_name=collection, points=points)
    logger.info(f"[qdrant] upserted {len(points)} vectors → '{collection}'")


# ─────────────────────────────────────────────────────────────
#  Main pipeline
# ─────────────────────────────────────────────────────────────

def run_ingest(
    pdf_dir: str | Path | None = None,
    reset: bool = False,
    use_ocr: bool = True,
    batch_pages: int | None = None,
    use_vision: bool = True,
) -> None:
    """
    Ingest all PDFs in pdf_dir into Qdrant.
    All extraction modes ON by default — single command covers everything.

    Args:
        pdf_dir     : directory with PDFs (default: data/guidelines/)
        reset       : wipe collection before indexing
        use_ocr     : batched OCR for Thai CID/scanned PDFs (default True)
        batch_pages : pages per OCR batch (None = INGEST_BATCH_PAGES in .env)
        use_vision  : chart/mixed pages → Gemini Vision (default True)
    """
    cfg         = get_settings()
    pdf_dir     = Path(pdf_dir or "data/guidelines")
    batch_pages = batch_pages or cfg.ingest_batch_pages
    pdfs        = sorted(pdf_dir.glob("*.pdf"))

    if not pdfs:
        logger.error(f"No PDFs in {pdf_dir.resolve()}")
        return

    logger.info(
        f"Ingestion start | files={len(pdfs)} | "
        f"chunk={cfg.chunk_size}/{cfg.chunk_overlap} | "
        f"ocr={use_ocr} batch_pages={batch_pages} | "
        f"vision={use_vision} model={cfg.ingest_vision_model}"
    )

    model  = SentenceTransformer(cfg.embedding_model, device=cfg.embedding_device)
    client = QdrantClient(url=cfg.qdrant_url)

    if reset:
        logger.warning("[ingest] --reset: wiping collection")
        _delete_collection(client, cfg.qdrant_collection)

    _ensure_collection(client, cfg.qdrant_collection, cfg.vector_dim)

    summary: dict[str, dict] = {}
    total = 0

    for idx, pdf in enumerate(pdfs):
        logger.info(f"{'═'*55}")
        logger.info(f"  [{idx+1}/{len(pdfs)}] {pdf.name}  ({_page_count(pdf)} pages)")
        logger.info(f"{'═'*55}")

        # Release VRAM/RAM from previous file before starting next one.
        # Docling caches converter weights; dropping between files prevents
        # memory from one file bleeding into the next (especially after OCR).
        if idx > 0:
            _free_docling_cache()

        chunks = _extract(
            pdf,
            use_ocr=use_ocr,
            batch_pages=batch_pages,
            use_vision=use_vision,
        )

        breakdown = {
            "total":   len(chunks),
            "docling": sum(1 for c in chunks if "docling" in c.extractor),
            "pymupdf": sum(1 for c in chunks if c.extractor == "pymupdf"),
            "vision":  sum(1 for c in chunks if c.extractor == "vision"),
        }
        summary[pdf.name] = breakdown

        if chunks:
            _upsert(client, cfg.qdrant_collection, chunks, model, cfg.ingest_batch_size)
        total += len(chunks)

    # ── Final summary ─────────────────────────────────────────
    logger.info(f"\n{'═'*55}")
    logger.info("  INGESTION SUMMARY")
    logger.info(f"{'═'*55}")
    for name, b in summary.items():
        status = "✓" if b["total"] > 0 else "✗"
        logger.info(
            f"  {status} {name}: {b['total']} chunks "
            f"[docling={b['docling']} pymupdf={b['pymupdf']} vision={b['vision']}]"
        )
    logger.success(f"  Total: {total} chunks across {len(pdfs)} file(s)")

    if any(b["total"] == 0 for b in summary.values()):
        logger.warning("  ⚠ Some files produced 0 chunks — check logs above")

    # Final cleanup — release all GPU memory back to the OS
    _free_docling_cache()
    _flush_memory("run_ingest_end")


# ─────────────────────────────────────────────────────────────
#  CLI
#
#  Normal usage (one command does everything):
#    python -m api.knowledge.ingest --dir data/guidelines/ --reset
#
#  All extraction modes are ON by default:
#    - Docling text-layer  (Tier 1, always)
#    - Docling batched OCR (Tier 2, for Thai CID / scanned pages)
#    - PyMuPDF gap-fill    (Tier 3, always as safety net)
#    - Gemini Vision       (chart/diagram pages via gemini-3.1-pro)
#
#  Tuning via .env only — no need to change flags:
#    INGEST_BATCH_PAGES=15         (lower if OOM: 5 or 10)
#    INGEST_VISION_MODEL=gemini-3.1-pro
#    INGEST_MIN_CHUNKS_RATIO=0.20
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="PharmBot multimodal PDF ingestion",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  First-time setup:
    python -m api.knowledge.ingest --dir data/guidelines/ --reset

  Re-index without wiping:
    python -m api.knowledge.ingest --dir data/guidelines/

  Custom PDF directory:
    python -m api.knowledge.ingest --dir /path/to/pdfs/ --reset
        """
    )
    ap.add_argument(
        "--dir",
        default="data/guidelines/",
        help="Directory containing PDF files (default: data/guidelines/)",
    )
    ap.add_argument(
        "--reset",
        action="store_true",
        help="Wipe existing Qdrant collection before indexing",
    )
    # Advanced overrides — normally set via .env, not CLI
    ap.add_argument(
        "--batch-pages",
        type=int,
        default=None,
        metavar="N",
        help="Override pages per OCR batch (default: INGEST_BATCH_PAGES in .env)",
    )
    args = ap.parse_args()

    run_ingest(
        pdf_dir=args.dir,
        reset=args.reset,
        use_ocr=True,        # always on — handles Thai CID & scanned pages
        batch_pages=args.batch_pages,
        use_vision=True,     # always on — Gemini Vision for charts/diagrams
    )