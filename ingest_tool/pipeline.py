"""Orchestration. Three entry points (ingest_directory / ingest_web / ingest_papers),
all converging on the same classify -> convert -> normalize spine and the same output
contract: mirrored tree of <name>.md (+ <name>_assets/ if there are images) + index.md.
"""

from __future__ import annotations

from pathlib import Path

from . import acquire_papers, acquire_web, convert, normalize
from .classify import route, should_skip_dir
from .config import Config


def ingest_directory(source: Path, output: Path, config: Config, ocr_images: bool = False, dry_run: bool = False) -> list[str]:
    entries: list[tuple[str, Path]] = []
    failures: list[str] = []

    for src in sorted(source.rglob("*")):
        if src.is_dir() or should_skip_dir(src):
            continue

        rel = src.relative_to(source)
        kind = route(src)

        if dry_run:
            print(f"[dry-run] {rel} -> {kind}")
            continue

        out_md = output / rel.parent / f"{src.stem}.md"

        if kind == "passthrough":
            normalize.write_markdown(out_md, convert.convert_passthrough(src))
        elif kind == "image":
            if not ocr_images:
                continue
            assets_dir = output / rel.parent / f"{src.stem}_assets"
            assets_dir.mkdir(parents=True, exist_ok=True)
            import shutil as _shutil
            _shutil.copy2(src, assets_dir / src.name)
            normalize.write_markdown(out_md, convert.convert_image_stub(src, f"{assets_dir.name}/{src.name}"))
        elif kind == "anydoc":
            text, err = convert.convert_anydoc(src, out_md)
            if text is not None:
                assets_dir = output / rel.parent / f"{src.stem}_assets"
                normalized = normalize.extract_inline_images(text, assets_dir)
                normalize.write_markdown(out_md, normalized)
            elif err == "scanned":
                mineru_dir = output / rel.parent / f"{src.stem}_mineru"
                mineru_md, mineru_err = convert.convert_mineru(src, mineru_dir)
                if mineru_md is None:
                    failures.append(f"{rel}: {mineru_err}")
                    continue
                out_md = mineru_md  # keep mineru's own layout (and its images/ folder) as-is
            else:
                failures.append(f"{rel}: {err}")
                continue
        else:
            continue

        if out_md.exists():
            entries.append((rel.as_posix(), out_md))

    if not dry_run:
        output.mkdir(parents=True, exist_ok=True)
        normalize.build_index(output, entries)
        normalize.write_failures(output, failures)

    return failures


def ingest_web(url: str, output: Path, config: Config) -> list[str]:
    pages, err = acquire_web.crawl_site(url, config.crawl.max_depth, config.crawl.max_pages)
    if err:
        return [err]

    # Crawl4AI can return both "example.com" and "example.com/" as distinct results
    # for the same page; slugify collapses them to the same filename, so dedupe here
    # rather than silently overwriting one with the other.
    seen_slugs: set[str] = set()
    deduped = []
    for page in pages:
        if page["slug"] in seen_slugs:
            continue
        seen_slugs.add(page["slug"])
        deduped.append(page)
    pages = deduped

    entries: list[tuple[str, Path]] = []
    for page in pages:
        out_md = output / f"{page['slug']}.md"
        assets_dir = output / f"{page['slug']}_assets"
        body = normalize.extract_inline_images(page["markdown"], assets_dir)
        normalize.write_markdown(out_md, body, metadata={"source_url": page["url"]})
        entries.append((page["url"], out_md))

    output.mkdir(parents=True, exist_ok=True)
    normalize.build_index(output, entries)
    return []


def ingest_papers(query: str, output: Path, config: Config, sources: list[str] | None = None, limit: int | None = None) -> list[str]:
    sources = sources or config.papers.default_sources
    limit = limit or config.papers.default_limit

    papers, err = acquire_papers.search_papers(query, sources=sources, max_results=limit)
    if err:
        return [err]

    entries: list[tuple[str, Path]] = []
    failures: list[str] = []
    staging = output / "_downloads"

    for paper in papers:
        pdf_path, derr = acquire_papers.download_paper(paper["source"], paper["paper_id"], staging)
        if pdf_path is None:
            failures.append(f"{paper.get('title', paper['paper_id'])}: {derr}")
            continue

        slug = paper["paper_id"].replace("/", "_")
        out_md = output / f"{slug}.md"
        metadata = {
            "title": paper.get("title", ""),
            "authors": paper.get("authors", ""),
            "source": paper.get("source", ""),
            "paper_id": paper.get("paper_id", ""),
            "published_date": paper.get("published_date", ""),
            "url": paper.get("url", ""),
        }

        text, cerr = convert.convert_anydoc(pdf_path, out_md)
        if text is not None:
            assets_dir = output / f"{slug}_assets"
            normalized = normalize.extract_inline_images(text, assets_dir)
            normalize.write_markdown(out_md, normalized, metadata=metadata)
        elif cerr == "scanned":
            mineru_dir = output / f"{slug}_mineru"
            mineru_md, merr = convert.convert_mineru(pdf_path, mineru_dir)
            if mineru_md is None:
                failures.append(f"{paper['title']}: {merr}")
                continue
            existing = mineru_md.read_text(encoding="utf-8", errors="replace")
            normalize.write_markdown(mineru_md, existing, metadata=metadata)
            out_md = mineru_md
        else:
            failures.append(f"{paper['title']}: {cerr}")
            continue

        entries.append((paper.get("title", slug), out_md))

    output.mkdir(parents=True, exist_ok=True)
    normalize.build_index(output, entries)
    normalize.write_failures(output, failures)
    return failures
