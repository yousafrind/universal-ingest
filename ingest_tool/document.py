"""Core data model shared by every conversion tier. Convert used to hand back a flat
markdown string per document, which is why page-scoped OCR fallback and per-page
quality checks always felt like a hack bolted onto the side. Making pages first-class
here means kreuzberg (tier 1), MinerU (tier 2, page-scoped), and the VLM (tier 3,
page-scoped) all read and write the same shape, and splicing one tier's output into
another's is just a list assignment.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Page:
    index: int  # 0-based, stable across tiers for a given source file
    text: str
    source: str  # which tier produced this page's text: "kreuzberg" | "mineru" | "vlm"


@dataclass
class Document:
    pages: list[Page] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)  # extra HTML tables kreuzberg found, appended after page text
    assets: list[tuple[str, bytes]] = field(default_factory=list)  # (filename, bytes) images to write alongside the .md

    def low_content_pages(self, min_chars: int = 30) -> list[int]:
        """Pages whose extracted text is suspiciously short — the signal that a page
        is actually scanned/image content that tier 1 (text-layer extraction) missed."""
        return [p.index for p in self.pages if len(p.text.strip()) < min_chars]

    def to_markdown(self) -> str:
        body = "\n\n".join(p.text.strip() for p in self.pages if p.text.strip())
        if self.tables:
            body += "\n\n" + "\n\n".join(self.tables)
        return body
