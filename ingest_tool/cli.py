from __future__ import annotations

import argparse
from pathlib import Path

from . import pipeline
from .classify import is_scanned_pdf_error, route
from .config import load_config
from .normalize import extract_inline_images, render_frontmatter


def selftest() -> None:
    # routing (pure logic, no I/O)
    assert route(Path("main.py")) == "passthrough"
    assert route(Path("readme.MD")) == "passthrough"
    assert route(Path("report.docx")) == "anydoc"
    assert route(Path("report.PDF")) == "anydoc"
    assert route(Path("photo.png")) == "image"
    assert route(Path("archive.zip")) == "skip"

    # anydoc's real "scanned PDF" error signal (verified string from live --help output)
    assert is_scanned_pdf_error("Scanned or image-only PDFs need OCR, which anydoc does not do") is True
    assert is_scanned_pdf_error("usage error: unknown option") is False

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

    print("selftest: OK (routing, anydoc-error-detection, image-extraction, frontmatter, config-fallback)")


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
        parser.error("a subcommand is required unless --selftest is passed (dir | web | papers)")

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
    elif args.command == "papers":
        sources = args.sources.split(",") if args.sources else None
        failures = pipeline.ingest_papers(args.query, args.output, config, sources, args.limit)
        _report(args.output, failures)


def _report(output: Path, failures: list[str]) -> None:
    print(f"Done. Index: {output / 'index.md'}")
    if failures:
        print(f"{len(failures)} failure(s) — see {output / 'FAILURES.md'}")


if __name__ == "__main__":
    main()
