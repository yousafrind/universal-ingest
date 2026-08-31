"""Unifies every backend's output into the same shape: <name>.md with any embedded
images pulled out to <name>_assets/ and referenced by relative path, plus optional
YAML frontmatter carrying acquisition metadata (source URL, paper citation, etc).

This is backend-agnostic on purpose: instead of writing per-backend image-handling
code, we scan whatever markdown text comes back for inline base64 images (the one
thing multiple backends might do) and normalize those. Backends that already write
a real images/ folder (mineru) are left untouched — we just place their folder.
"""

from __future__ import annotations

import base64
import binascii
import re
from pathlib import Path

_INLINE_IMAGE_RE = re.compile(
    r"!\[([^\]]*)\]\(data:image/(?P<ext>\w+);base64,(?P<data>[A-Za-z0-9+/=]+)\)"
)


def extract_inline_images(markdown_text: str, assets_dir: Path) -> str:
    """Pulls any data:image/...;base64,... refs out to files in assets_dir, rewrites
    the markdown to reference them by relative path. No-op (fast) if there are none.
    """
    if "base64," not in markdown_text:
        return markdown_text

    counter = {"n": 0}

    def _replace(match: re.Match) -> str:
        alt, ext, data = match.group(1), match.group("ext"), match.group("data")
        try:
            raw = base64.b64decode(data)
        except (binascii.Error, ValueError):
            return match.group(0)  # leave malformed data URIs alone
        counter["n"] += 1
        assets_dir.mkdir(parents=True, exist_ok=True)
        filename = f"img{counter['n']}.{ext}"
        (assets_dir / filename).write_bytes(raw)
        return f"![{alt}]({assets_dir.name}/{filename})"

    return _INLINE_IMAGE_RE.sub(_replace, markdown_text)


def _yaml_escape(value: str) -> str:
    if any(c in value for c in ':#"\n') or value.strip() != value:
        return '"' + value.replace('"', '\\"').replace("\n", " ") + '"'
    return value


def render_frontmatter(metadata: dict[str, str]) -> str:
    if not metadata:
        return ""
    lines = ["---"]
    for key, value in metadata.items():
        if value:
            lines.append(f"{key}: {_yaml_escape(str(value))}")
    lines.append("---\n")
    return "\n".join(lines) + "\n"


def write_markdown(out_md: Path, body: str, metadata: dict[str, str] | None = None) -> None:
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(render_frontmatter(metadata or {}) + body, encoding="utf-8")


def build_index(output_dir: Path, entries: list[tuple[str, Path]]) -> None:
    lines = ["# Ingestion Index\n"]
    for label, md_path in entries:
        link = md_path.relative_to(output_dir).as_posix()
        lines.append(f"- [{label}]({link})")
    (output_dir / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_failures(output_dir: Path, failures: list[str]) -> None:
    if not failures:
        return
    (output_dir / "FAILURES.md").write_text(
        "# Conversion failures\n\n" + "\n".join(f"- {f}" for f in failures) + "\n",
        encoding="utf-8",
    )
