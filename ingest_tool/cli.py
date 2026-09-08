from __future__ import annotations

import argparse
from pathlib import Path

from . import pipeline
from .classify import route
from .config import load_config
from .document import Document, Page
from .normalize import extract_inline_images, render_frontmatter


def selftest() -> None:
    # routing (pure logic, no I/O)
    assert route(Path("main.py")) == "passthrough"
    assert route(Path("readme.MD")) == "passthrough"
    assert route(Path("report.docx")) == "document"
    assert route(Path("report.PDF")) == "document"
    assert route(Path("notes.tex")) == "document"
    assert route(Path("photo.png")) == "image"
    assert route(Path("archive.zip")) == "skip"

    # Document: the page-scoped data model tier 1/2/3 all read and write.
    doc = Document(pages=[
        Page(index=1, text="real content here", source="kreuzberg"),
        Page(index=2, text="  ", source="kreuzberg"),  # scanned page kreuzberg couldn't read
    ])
    assert doc.low_content_pages(min_chars=5) == [2]
    doc.pages[1].text = "OCR'd content"
    doc.pages[1].source = "mineru"
    assert doc.low_content_pages(min_chars=5) == []
    assert "real content here" in doc.to_markdown()
    assert "OCR'd content" in doc.to_markdown()

    # normalize: inline base64 image extraction
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        assets = Path(tmp) / "assets"
        md = "before ![alt](data:image/png;base64,aGVsbG8=) after"
        out = extract_inline_images(md, assets)
        assert "assets/img1.png" in out
        assert (assets / "img1.png").read_bytes() == b"hello"

    # frontmatter rendering
    fm = render_frontmatter({"title": "A: B", "empty": ""})
    assert fm.startswith("---\n")
    assert 'title: "A: B"' in fm
    assert "empty" not in fm  # empty values are dropped

    # config: missing file falls back to a working DeepSeek default
    config = load_config(Path("/nonexistent/config.toml"))
    assert "default" in config.vlm
    assert config.vlm["default"].model.startswith("deepseek/")

    print("selftest: OK (routing, document page model, image-extraction, frontmatter, config-fallback)")


def main() -> None:
    parser = argparse.ArgumentParser(prog="ingest", description=__doc__)
    parser.add_argument("--config", type=Path, default=None, help="path to config.toml")
    parser.add_argument("--selftest", action="store_true", help="run self-checks and exit (no network/backends needed)")
    sub = parser.add_subparsers(dest="command")

    p_dir = sub.add_parser("dir", help="ingest a local directory")
    p_dir.add_argument("source", type=Path)
    p_dir.add_argument("output", type=Path)
    p_dir.add_argument("--ocr-images", action="store_true")
    p_dir.add_argument("--dry-run", action="store_true")

    p_web = sub.add_parser("web", help="crawl a website and ingest it")
    p_web.add_argument("url")
    p_web.add_argument("output", type=Path)
    p_web.add_argument("--depth", type=int, default=None)
    p_web.add_argument("--max-pages", type=int, default=None)

    p_wiki = sub.add_parser("wiki", help="batch-crawl a list of URLs into one markdown wiki file per site")
    p_wiki.add_argument("urls_file", type=Path, help="text file with one seed URL per line")
    p_wiki.add_argument("output", type=Path)
    p_wiki.add_argument("--depth", type=int, default=None)
    p_wiki.add_argument("--max-pages", type=int, default=None)

    p_papers = sub.add_parser("papers", help="search and download academic papers")
    p_papers.add_argument("query")
    p_papers.add_argument("output", type=Path)
    p_papers.add_argument("--sources", type=str, default=None, help="comma-separated, e.g. arxiv,semantic")
    p_papers.add_argument("--limit", type=int, default=None)

    args = parser.parse_args()

    if args.selftest:
        selftest()
        return

    if not args.command:
        parser.error("a subcommand is required unless --selftest is passed (dir | web | wiki | papers)")

    config = load_config(args.config)

    if args.command == "dir":
        failures = pipeline.ingest_directory(args.source, args.output, config, args.ocr_images, args.dry_run)
        if not args.dry_run:
            _report(args.output, failures)
    elif args.command == "web":
        if args.depth is not None:
            config.crawl.max_depth = args.depth
        if args.max_pages is not None:
            config.crawl.max_pages = args.max_pages
        failures = pipeline.ingest_web(args.url, args.output, config)
        _report(args.output, failures)
    elif args.command == "wiki":
        if args.depth is not None:
            config.crawl.max_depth = args.depth
        if args.max_pages is not None:
            config.crawl.max_pages = args.max_pages
        urls = [line.strip() for line in args.urls_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        failures = pipeline.ingest_wiki(urls, args.output, config)
        _report(args.output, failures)
    elif args.command == "papers":
        sources = args.sources.split(",") if args.sources else None
        failures = pipeline.ingest_papers(args.query, args.output, config, sources, args.limit)
        _report(args.output, failures)


def _report(output: Path, failures: list[str]) -> None:
    index_path = output / "index.md"
    if index_path.exists():
        print(f"Done. Index: {index_path}")
    else:
        print("Nothing was converted — no index.md was written.")
    if failures:
        print(f"{len(failures)} failure(s):")
        for f in failures:
            print(f"  - {f}")


if __name__ == "__main__":
    main()
