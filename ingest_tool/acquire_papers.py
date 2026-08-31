"""paper-search-mcp adapter. CLI shape and JSON schema below were verified live against
the real tool on 2026-08-26 (see PLAN.md) — this is not guesswork.

    $ paper-search search "<query>" -n <max_results> -s <sources>
      -> {"query":..., "papers": [{"paper_id", "title", "authors", "abstract",
                                    "pdf_url", "url", "source", "published_date", ...}]}
    $ paper-search download <source> <paper_id> -o <save_dir>
      -> {"status": "ok", "path": "<save_dir>/<paper_id>.pdf"}
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def tool_available() -> bool:
    return shutil.which("paper-search") is not None


def search_papers(query: str, sources: list[str] | None = None, max_results: int = 5, year: str | None = None) -> tuple[list[dict], str]:
    if not tool_available():
        return [], "paper-search not installed (pip install paper-search-mcp)"

    args = ["paper-search", "search", query, "-n", str(max_results)]
    if sources:
        args += ["-s", ",".join(sources)]
    if year:
        args += ["-y", year]

    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    if result.returncode != 0:
        return [], f"paper-search search failed: {result.stderr.strip()[:300]}"

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        # Defensive: if a diagnostic line ever leaks onto stdout, salvage from the first "{".
        start = result.stdout.find("{")
        if start == -1:
            return [], "paper-search returned no parseable JSON"
        payload = json.loads(result.stdout[start:])

    return payload.get("papers", []), ""


def download_paper(source: str, paper_id: str, save_dir: Path) -> tuple[Path | None, str]:
    if not tool_available():
        return None, "paper-search not installed"

    save_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["paper-search", "download", source, paper_id, "-o", str(save_dir)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    if result.returncode != 0:
        return None, f"paper-search download failed: {result.stderr.strip()[:300]}"

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        start = result.stdout.find("{")
        payload = json.loads(result.stdout[start:]) if start != -1 else {}

    if payload.get("status") != "ok" or "path" not in payload:
        return None, f"unexpected download response: {result.stdout[:300]}"

    return Path(payload["path"]), ""
