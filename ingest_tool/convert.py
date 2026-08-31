"""Conversion backends. Each function shells out to a real, verified CLI (see PLAN.md
for what was actually confirmed vs. documented-only) and returns (markdown_text_or_None,
error_message). This module never parses PDFs/OCR itself — pure orchestration.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# On Windows, npx/npm-installed CLIs resolve to .cmd wrapper scripts, which
# subprocess.run can't exec directly without shell=True (WinError 2 otherwise).
# Pip-installed console-script CLIs (mineru, paper-search) are real .exe launchers
# on Windows and don't need this, so it's applied narrowly to the npx call only.
_WINDOWS = os.name == "nt"

from .classify import LANG_BY_EXT, is_scanned_pdf_error


def resolve_tool(cmd: str) -> str | None:
    """shutil.which() only sees the parent shell's PATH — if this process is running
    from an unactivated venv's python.exe directly (no `activate` run), pip-installed
    console scripts like `mineru` live in that venv's Scripts/bin and won't be found
    by a bare PATH lookup. Check there too before giving up.
    """
    found = shutil.which(cmd)
    if found:
        return found
    venv_bin = Path(sys.executable).parent
    candidate = venv_bin / (f"{cmd}.exe" if _WINDOWS else cmd)
    return str(candidate) if candidate.exists() else None


def tool_available(cmd: str) -> bool:
    return resolve_tool(cmd) is not None


# --- passthrough / images (no external tool needed) ------------------------

def convert_passthrough(src: Path) -> str:
    text = src.read_text(encoding="utf-8", errors="replace")
    if src.suffix.lower() in {".md", ".markdown"}:
        return text
    lang = LANG_BY_EXT.get(src.suffix.lower(), "")
    return f"# {src.name}\n\n```{lang}\n{text}\n```\n"


def convert_image_stub(src: Path, assets_relpath: str) -> str:
    return f"# {src.name}\n\n![{src.name}]({assets_relpath})\n"


# --- anydoc (verified 2026-08-26: `npx -y @firecrawl/anydoc <file> -o <out.md>`) --

def convert_anydoc(src: Path, out_md: Path) -> tuple[str | None, str]:
    """Returns (markdown_text, "") on success, (None, "scanned") if anydoc reports the
    document needs OCR, or (None, error) for any other failure."""
    npx_bin = resolve_tool("npx")
    if npx_bin is None:
        return None, "npx not found (anydoc requires Node 20+; install Node.js)"

    out_md.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [npx_bin, "-y", "@firecrawl/anydoc", str(src), "-o", str(out_md)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, shell=_WINDOWS,
    )
    if result.returncode == 0 and out_md.exists():
        return out_md.read_text(encoding="utf-8", errors="replace"), ""
    if result.returncode == 1 and is_scanned_pdf_error(result.stderr):
        return None, "scanned"
    return None, f"anydoc exit {result.returncode}: {result.stderr.strip()[:300]}"


# --- mineru (documented CLI, NOT live-verified this session — heavy ML deps) ------
# `mineru -p <input> -o <output> -b pipeline` per opendatalab/mineru README (2026-08).
# Exact nested output layout isn't pinned down in the docs excerpt, so we search for
# it with rglob rather than assuming a fixed path — robust to whatever subfolder
# structure mineru actually uses.

def convert_mineru(src: Path, out_dir: Path) -> tuple[Path | None, str]:
    mineru_bin = resolve_tool("mineru")
    if mineru_bin is None:
        return None, "mineru not installed (pip install \"mineru[pipeline]\")"

    out_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [mineru_bin, "-p", str(src), "-o", str(out_dir), "-b", "pipeline"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600,
    )
    if result.returncode != 0:
        return None, f"mineru exit {result.returncode}: {result.stderr.strip()[:300]}"

    candidates = list(out_dir.rglob(f"{src.stem}.md"))
    if not candidates:
        return None, "mineru produced no markdown file (unexpected output layout — check out_dir manually)"
    return candidates[0], ""
