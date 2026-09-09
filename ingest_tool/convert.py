"""Conversion backends, tiered and page-scoped.

Tier 1 (kreuzberg): fast text-layer extraction across 100+ formats, MIT-licensed,
no ML weight for native-text documents. Real tables (HTML), native DOCX/PPTX/XLSX/
LaTeX/EPUB support.

Tier 2 (MinerU, page-scoped): kreuzberg silently returns near-empty text for scanned/
image-only pages instead of erroring — verified live against a real 678-page book,
page 39 (a chart-heavy page) came back at 2 chars while neighboring pages had 700-1600.
So we flag pages by content length, extract ONLY those pages to a temp PDF, and hand
that small subset to MinerU. This is the fix for the earlier bug where the whole
678-page book got OCR'd (~1h15m) for the sake of ~39 actually-scanned pages.

Tier 3 (VLM, page-scoped): only reached if MinerU isn't installed, or still leaves a
page short after OCR. Reuses vlm.py's existing config-swappable litellm client and
kreuzberg's own render_pdf_page() to get the page image — no new deps.

Figure/table snapshot fallback: kreuzberg's image extraction only pulls embedded
raster images (JPEG/PNG blobs) — a vector-drawn diagram (TikZ/matplotlib PDF output,
extremely common in papers) leaves no image at all, even on a page with plenty of
text, so the "low content" heuristic never catches it either. Verified live: a real
paper's Figure 2 (a workflow diagram) came back as disconnected label fragments with
zero image. Any kreuzberg-tier page whose text mentions "Figure N"/"Table N" gets a
full-page snapshot attached as a blunt but reliable fallback.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .document import Document, Page

_CAPTION_RE = re.compile(r"\b(?:Figure|Fig\.|Table)\s+\d+\b", re.IGNORECASE)

_WINDOWS = os.name == "nt"

MIN_PAGE_CHARS = 30  # below this, a page is treated as "tier 1 couldn't read this"


def resolve_tool(cmd: str) -> str | None:
    """Prefer the tool installed in *this process's own* venv over whatever a bare
    PATH lookup finds — shutil.which() can silently resolve to an unrelated shared
    environment that happens to appear earlier on PATH (bit us once already)."""
    venv_bin = Path(sys.executable).parent
    candidate = venv_bin / (f"{cmd}.exe" if _WINDOWS else cmd)
    if candidate.exists():
        return str(candidate)
    return shutil.which(cmd)


def tool_available(cmd: str) -> bool:
    return resolve_tool(cmd) is not None


def _tail(text: str, n: int = 500) -> str:
    """A truncated error message from the FRONT is useless for a Python traceback —
    the actual exception type/message is always the last line. Keep the tail instead."""
    text = text.strip()
    return text if len(text) <= n else "…" + text[-n:]


# --- passthrough / images (no external tool needed) ------------------------

PASSTHROUGH_LANG_BY_EXT = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "tsx",
    ".jsx": "jsx", ".go": "go", ".rs": "rust", ".java": "java", ".c": "c",
    ".cpp": "cpp", ".h": "c", ".hpp": "cpp", ".sh": "bash", ".ps1": "powershell",
    ".rb": "ruby", ".php": "php", ".sql": "sql", ".yaml": "yaml", ".yml": "yaml",
    ".json": "json", ".toml": "toml", ".ini": "ini", ".cfg": "ini",
}


def convert_passthrough(src: Path) -> str:
    text = src.read_text(encoding="utf-8", errors="replace")
    if src.suffix.lower() in {".md", ".markdown"}:
        return text
    lang = PASSTHROUGH_LANG_BY_EXT.get(src.suffix.lower(), "")
    return f"# {src.name}\n\n```{lang}\n{text}\n```\n"


def convert_image_stub(src: Path, assets_relpath: str) -> str:
    return f"# {src.name}\n\n![{src.name}]({assets_relpath})\n"


# --- tier 1: kreuzberg -------------------------------------------------------

_PAGE_MARKER = "\x00KREUZBERG_PAGE_BOUNDARY\x00"


def convert_kreuzberg(src: Path) -> tuple[Document | None, str]:
    """Returns (Document, "") on success, (None, error) on failure. Pages are
    1-indexed to match how humans (and MinerU's page_idx + 1) refer to PDF pages."""
    try:
        from kreuzberg import ExtractionConfig, PageConfig, extract_file_sync
    except ImportError:
        return None, "kreuzberg not installed (pip install kreuzberg)"

    cfg = ExtractionConfig(
        pages=PageConfig(extract_pages=True, insert_page_markers=True, marker_format=_PAGE_MARKER)
    )
    try:
        result = extract_file_sync(str(src), config=cfg)
    except Exception as e:  # noqa: BLE001 - any format/parsing failure should surface as a normal error, not crash the run
        return None, f"kreuzberg: {e}"

    parts = result.content.split(_PAGE_MARKER)
    # parts[0] is whatever precedes the first page marker (normally empty); real
    # pages are parts[1:], 1-indexed to match parts[i] == page i.
    pages = [Page(index=i, text=text, source="kreuzberg") for i, text in enumerate(parts) if i > 0]
    if not pages:
        # Not a paginated format (docx/html/etc) - single "page" with everything.
        pages = [Page(index=1, text=result.content, source="kreuzberg")]

    tables = [t.text if hasattr(t, "text") else str(t) for t in (result.tables or [])]
    return Document(pages=pages, tables=tables), ""


# --- tier 2: MinerU, page-scoped ---------------------------------------------

def _render_mineru_block(block: dict, assets_relname: str) -> str:
    kind = block.get("type")
    if kind == "text":
        level = block.get("text_level")
        prefix = ("#" * level + " ") if level else ""
        return prefix + block.get("text", "")
    if kind == "equation":
        return block.get("text", "")
    if kind == "table":
        caption = " ".join(block.get("table_caption") or [])
        body = block.get("table_body", "")
        return (f"**{caption}**\n\n" if caption else "") + body
    if kind in ("image", "chart"):
        caption = " ".join(block.get(f"{kind}_caption") or [])
        img_path = block.get("img_path", "")
        # Must match the filename convert_document actually writes to disk
        # (f"mineru_{img_path.name}" into the doc's assets folder) — these two were
        # out of sync before (bare "images/<hash>.jpg" vs. the real assets path),
        # producing markdown with broken image links.
        ref = f"{assets_relname}/mineru_{Path(img_path).name}" if img_path else ""
        return f"![{caption}]({ref})"
    if kind == "code":
        return "```\n" + block.get("code_body", "") + "\n```"
    if kind == "page_number":
        return ""  # running page numbers carry no document content
    # header/footer/page_footnote: keep as plain text rather than risk losing content
    return block.get("text", "")


def convert_mineru_pages(
    src: Path, page_numbers: list[int], out_dir: Path, assets_relname: str
) -> tuple[dict[int, str] | None, Path | None, str]:
    """OCRs ONLY the given 1-indexed page numbers, not the whole document. Returns
    (page_texts, images_dir, error) where page_texts maps original page number ->
    rendered markdown for that page."""
    mineru_bin = resolve_tool("mineru")
    if mineru_bin is None:
        return None, None, "mineru not installed (pip install \"mineru[pipeline]\")"

    import pypdf

    reader = pypdf.PdfReader(str(src))
    writer = pypdf.PdfWriter()
    ordered_pages = sorted(page_numbers)
    for pnum in ordered_pages:
        writer.add_page(reader.pages[pnum - 1])

    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out_dir) as tmp:
        subset_pdf = Path(tmp) / "subset.pdf"
        with subset_pdf.open("wb") as f:
            writer.write(f)

        subset_out = Path(tmp) / "out"
        env = {**os.environ, "MINERU_TASK_RESULT_TIMEOUT_SECONDS": "10800"}
        result = subprocess.run(
            [mineru_bin, "-p", str(subset_pdf), "-o", str(subset_out), "-b", "pipeline"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10860, env=env,
        )
        if result.returncode != 0:
            return None, None, f"mineru exit {result.returncode}: {_tail(result.stderr)}"

        content_lists = list(subset_out.rglob("*_content_list.json"))
        if not content_lists:
            return None, None, "mineru produced no content_list.json (unexpected output layout)"
        blocks = json.loads(content_lists[0].read_text(encoding="utf-8"))

        by_subset_page: dict[int, list[dict]] = {}
        for block in blocks:
            by_subset_page.setdefault(block["page_idx"], []).append(block)

        page_texts: dict[int, str] = {}
        for subset_idx, original_pnum in enumerate(ordered_pages):
            page_blocks = by_subset_page.get(subset_idx, [])
            rendered = "\n\n".join(
                filter(None, (_render_mineru_block(b, assets_relname) for b in page_blocks))
            )
            page_texts[original_pnum] = rendered

        # Persist the images MinerU extracted so the caller can copy them into the
        # document's own assets dir before this temp directory is cleaned up.
        images_src = content_lists[0].parent / "images"
        images_dst = None
        if images_src.exists():
            images_dst = out_dir / f"_mineru_images_{src.stem}"
            if images_dst.exists():
                shutil.rmtree(images_dst)
            shutil.copytree(images_src, images_dst)

        return page_texts, images_dst, ""


# --- orchestration: tier 1 -> tier 2 -> tier 3 -------------------------------

def convert_document(
    src: Path, out_dir: Path, config, assets_relname: str = "assets"
) -> tuple[Document | None, str]:
    """The single entry point pipeline.py should call for any document format.
    `assets_relname` must be the name of the folder the caller will write this
    document's images into (relative to the .md file) — needed so image references
    rendered here actually resolve once the assets are on disk.
    Returns (Document, "") on success, (None, error) if even tier 1 fails."""
    doc, err = convert_kreuzberg(src)
    if doc is None:
        return None, err

    flagged = doc.low_content_pages(MIN_PAGE_CHARS)

    if flagged and tool_available("mineru"):
        page_texts, images_dir, mineru_err = convert_mineru_pages(src, flagged, out_dir, assets_relname)
        if page_texts is not None:
            for page in doc.pages:
                if page.index in page_texts and page_texts[page.index].strip():
                    page.text = page_texts[page.index]
                    page.source = "mineru"
            if images_dir is not None:
                for img_path in images_dir.iterdir():
                    doc.assets.append((f"mineru_{img_path.name}", img_path.read_bytes()))
                shutil.rmtree(images_dir, ignore_errors=True)
        # If mineru failed outright, fall through to VLM rather than losing the pages.

    still_flagged = doc.low_content_pages(MIN_PAGE_CHARS)
    if still_flagged and "default" in getattr(config, "vlm", {}):
        from . import vlm
        from kreuzberg import render_pdf_page

        for page in doc.pages:
            if page.index not in still_flagged:
                continue
            try:
                image_bytes = render_pdf_page(str(src), page.index - 1)
            except Exception:  # noqa: BLE001 - non-PDF or render failure, leave page as-is
                continue
            text, _ = vlm.describe_page(image_bytes, config)
            if text:
                page.text = text
                page.source = "vlm"

    _attach_figure_snapshots(src, doc, assets_relname)

    return doc, ""


def _attach_figure_snapshots(src: Path, doc: Document, assets_relname: str) -> None:
    """Pages still on tier 1 (kreuzberg) whose text mentions a Figure/Table caption
    get a full-page snapshot attached — tier 1 only pulls embedded raster images, so
    a vector-drawn diagram leaves no image at all even on a text-rich page. Tier 2/3
    pages are skipped: MinerU already extracts figures as proper image blocks, and a
    VLM-transcribed page has no reliable page-image source to re-render from here."""
    candidates = [p for p in doc.pages if p.source == "kreuzberg" and _CAPTION_RE.search(p.text)]
    if not candidates:
        return
    try:
        from kreuzberg import render_pdf_page
    except ImportError:
        return

    for page in candidates:
        try:
            image_bytes = render_pdf_page(str(src), page.index - 1)
        except Exception:  # noqa: BLE001 - not a PDF, or render failure; skip this page's snapshot
            continue
        filename = f"page{page.index}_snapshot.png"
        doc.assets.append((filename, image_bytes))
        page.text += f"\n\n![page {page.index} snapshot (contains a figure/table)]({assets_relname}/{filename})\n"
