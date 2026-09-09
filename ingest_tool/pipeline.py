"""Orchestration. Four entry points (ingest_directory / ingest_web / ingest_wiki /
ingest_papers), all converging on the same classify -> convert -> normalize spine and
the same output contract: mirrored tree of <name>.md (+ <name>_assets/ if there are
images) + index.md. ingest_wiki additionally folds every crawled site into one
consolidated per-site markdown file instead of one file per page.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import acquire_papers, acquire_web, convert, normalize
from .classify import route, should_skip_dir
from .config import Config


def ingest_directory(source: Path, output: Path, config: Config, ocr_images: bool = False, dry_run: bool = False) -> list[str]:
    entries: list[tuple[str, Path]] = []
    failures: list[str] = []

    # Path.rglob("*") on a file (not a directory) or a nonexistent path silently
    # yields nothing — no exception. Without this check, pointing `source` at a
    # single PDF/doc, or a typo'd directory name, produces a quietly empty index
    # instead of either processing the file or failing loudly.
    if not source.exists():
        return [f"source path does not exist: {source}"]
    if source.is_file():
        files = [source]
    else:
        files = sorted(p for p in source.rglob("*") if not p.is_dir())

    for src in files:
        if should_skip_dir(src):
            continue

        rel = src.relative_to(source) if source.is_dir() else Path(src.name)
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
        elif kind == "document":
            assets_dir = output / rel.parent / f"{src.stem}_assets"
            doc, err = convert.convert_document(src, output / rel.parent, config, assets_dir.name)
            if doc is None:
                failures.append(f"{rel}: {err}")
                continue
            normalize.write_document_assets(assets_dir, doc.assets)
            body = normalize.extract_inline_images(doc.to_markdown(), assets_dir)
            normalize.write_markdown(out_md, body)
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


def ingest_wiki(urls: list[str], output: Path, config: Config) -> list[str]:
    """Batch mode: one seed URL -> one crawl -> one consolidated markdown "wiki" file
    per site, folding in every linked document (PDF/DOCX/etc) it can find, converted
    through the same tiered convert.convert_document() as `ingest dir`."""
    failures: list[str] = []
    entries: list[tuple[str, Path]] = []
    output.mkdir(parents=True, exist_ok=True)

    for url in urls:
        pages, err = acquire_web.crawl_site(url, config.crawl.max_depth, config.crawl.max_pages)
        if err:
            failures.append(f"{url}: {err}")
            continue
        if not pages:
            failures.append(f"{url}: crawl returned no pages")
            continue

        # Use the full slug, not just the domain - two seed URLs on the same host
        # (two GitHub repos, two arXiv papers, two posts on one blog) must not collapse
        # onto the same output file and silently overwrite each other.
        site_slug = (pages[0]["slug"] or "site").replace("/", "_")
        out_md = output / f"{site_slug}.md"
        assets_dir = output / f"{site_slug}_assets"

        sections = [f"# {url}\n\n*LLM wiki generated from {len(pages)} crawled page(s).*\n"]
        sections.append("## Pages\n\n" + "\n".join(f"- [{p['url']}](#{_anchor(p['url'])})" for p in pages))

        download_urls: list[str] = []
        seen_downloads: set[str] = set()
        for page in pages:
            for d in page.get("downloads", []):
                if d not in seen_downloads:
                    seen_downloads.add(d)
                    download_urls.append(d)

        for page in pages:
            body = normalize.extract_inline_images(page["markdown"], assets_dir)
            sections.append(f"\n---\n\n## {page['url']}\n\n{body}")

        if download_urls:
            sections.append("\n---\n\n## Downloaded documents\n")
        staging = output / f"{site_slug}_downloads"
        for durl in download_urls:
            path, derr = acquire_web.download_file(durl, staging)
            if path is None:
                failures.append(f"{durl}: {derr}")
                continue
            doc, cerr = convert.convert_document(path, output / f"{site_slug}_work", config, assets_dir.name)
            if doc is None:
                failures.append(f"{durl}: {cerr}")
                continue
            normalize.write_document_assets(assets_dir, doc.assets)
            body = normalize.extract_inline_images(doc.to_markdown(), assets_dir)
            sections.append(f"\n### {durl}\n\n{body}")

        normalize.write_markdown(out_md, "\n".join(sections), metadata={"source_url": url})
        entries.append((url, out_md))

    normalize.build_index(output, entries)
    normalize.write_failures(output, failures)
    return failures


def _anchor(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


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

        assets_dir = output / f"{slug}_assets"
        doc, derr2 = convert.convert_document(pdf_path, output / f"{slug}_work", config, assets_dir.name)
        if doc is None:
            failures.append(f"{paper['title']}: {derr2}")
            continue
        normalize.write_document_assets(assets_dir, doc.assets)
        body = normalize.extract_inline_images(doc.to_markdown(), assets_dir)
        normalize.write_markdown(out_md, body, metadata=metadata)

        entries.append((paper.get("title", slug), out_md))

    output.mkdir(parents=True, exist_ok=True)
    normalize.build_index(output, entries)
    normalize.write_failures(output, failures)
    return failures
