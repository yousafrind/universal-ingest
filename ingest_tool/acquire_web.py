"""Crawl4AI adapter. Verified live end-to-end 2026-08-26 (crawl4ai-setup ran, real
deep crawl against docs.crawl4ai.com with max_depth=1 returned 3 pages of real
markdown). `arun()` does return a list when a deep-crawl strategy is set, confirming
the pattern below.
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urlparse


def tool_available() -> bool:
    try:
        import crawl4ai  # noqa: F401
        return True
    except ImportError:
        return False


def _slugify(url: str) -> str:
    parsed = urlparse(url)
    slug = (parsed.netloc + parsed.path).strip("/")
    slug = re.sub(r"[^a-zA-Z0-9/_-]", "_", slug) or "index"
    return slug


def crawl_site(url: str, max_depth: int = 2, max_pages: int = 200) -> tuple[list[dict], str]:
    """Returns (pages, error) where pages is a list of {"url", "markdown"} dicts."""
    if not tool_available():
        return [], "crawl4ai not installed (pip install -U crawl4ai && crawl4ai-setup)"

    try:
        return asyncio.run(_crawl_async(url, max_depth, max_pages)), ""
    except Exception as e:  # noqa: BLE001 - browser/network failures shouldn't crash the whole ingest run
        return [], f"crawl4ai failed: {e}"


async def _crawl_async(url: str, max_depth: int, max_pages: int) -> list[dict]:
    from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
    from crawl4ai.deep_crawling import BFSDeepCrawlStrategy

    strategy = BFSDeepCrawlStrategy(max_depth=max_depth, max_pages=max_pages)
    run_config = CrawlerRunConfig(deep_crawl_strategy=strategy)

    async with AsyncWebCrawler() as crawler:
        results = await crawler.arun(url=url, config=run_config)

    if not isinstance(results, list):
        results = [results]

    pages = []
    for r in results:
        markdown = getattr(r, "markdown", None)
        page_url = getattr(r, "url", url)
        if markdown:
            pages.append({"url": page_url, "markdown": str(markdown), "slug": _slugify(page_url)})
    return pages
