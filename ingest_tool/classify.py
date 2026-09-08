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

# Everything kreuzberg (tier 1, see convert.py) natively understands — a much wider
# net than the old anydoc-only list, including LaTeX/BibTeX/Jupyter/email, which was
# the actual "jungle of formats" gap anydoc never covered.
DOCUMENT_EXTS = {
    ".doc", ".docx", ".docm", ".odt", ".rtf", ".epub", ".pdf",
    ".ppt", ".pps", ".pot", ".pptx", ".pptm", ".ppsx", ".ppsm", ".odp",
    ".xls", ".xlsx", ".xlsm", ".xlsb", ".ods", ".csv",
    ".tex", ".bib", ".ipynb", ".eml", ".msg",
}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"}

SKIP_DIR_NAMES = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".cache"}


def route(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in PASSTHROUGH_EXTS:
        return "passthrough"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in DOCUMENT_EXTS:
        return "document"
    return "skip"


def should_skip_dir(path: Path) -> bool:
    return any(part in SKIP_DIR_NAMES for part in path.parts)
