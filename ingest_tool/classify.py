"""Pure routing decisions. No I/O, no subprocess — easy to unit-test (see cli.py --selftest)."""

from __future__ import annotations

from pathlib import Path

PASSTHROUGH_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".c", ".cpp",
    ".h", ".hpp", ".sh", ".ps1", ".rb", ".php", ".sql", ".yaml", ".yml",
    ".json", ".toml", ".ini", ".cfg", ".txt", ".md", ".markdown", ".rst",
}
LANG_BY_EXT = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "tsx",
    ".jsx": "jsx", ".go": "go", ".rs": "rust", ".java": "java", ".c": "c",
    ".cpp": "cpp", ".h": "c", ".hpp": "cpp", ".sh": "bash", ".ps1": "powershell",
    ".rb": "ruby", ".php": "php", ".sql": "sql", ".yaml": "yaml", ".yml": "yaml",
    ".json": "json", ".toml": "toml", ".ini": "ini", ".cfg": "ini",
}

# Everything anydoc's real --help lists as a supported input (verified 2026-08-26).
ANYDOC_EXTS = {
    ".doc", ".docx", ".docm", ".odt", ".rtf", ".epub", ".pdf",
    ".ppt", ".pps", ".pot", ".pptx", ".pptm", ".ppsx", ".ppsm", ".odp",
    ".xls", ".xlsx", ".xlsm", ".xlsb", ".ods", ".csv",
}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"}

SKIP_DIR_NAMES = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".cache"}

# anydoc's documented behavior: scanned/image-only PDFs are not silently mangled,
# they exit 1 with a message naming OCR/scanning as the reason. We key off that
# instead of a text-length heuristic, since anydoc itself already knows.
ANYDOC_SCANNED_MARKERS = ("ocr", "scan")


def route(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in PASSTHROUGH_EXTS:
        return "passthrough"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in ANYDOC_EXTS:
        return "anydoc"
    return "skip"


def is_scanned_pdf_error(stderr: str) -> bool:
    lowered = stderr.lower()
    return any(marker in lowered for marker in ANYDOC_SCANNED_MARKERS)


def should_skip_dir(path: Path) -> bool:
    return any(part in SKIP_DIR_NAMES for part in path.parts)
