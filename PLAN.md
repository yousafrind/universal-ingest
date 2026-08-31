# Universal Ingestion Tool — Complete Plan

**Goal**: a standalone, independently-runnable tool that ingests a local directory, a website, or an academic-paper search query, and produces one uniform output — a mirrored directory of Markdown files (+ per-doc asset folders) viewable in any Markdown viewer. Multi-provider LLM/VLM support (DeepSeek default, config-swappable to OpenRouter/OpenAI/Anthropic/self-hosted) as a cost-gated fallback tier, not a hard dependency.

---

## 1. Architecture

```
┌───────────────────────────── ACQUISITION ─────────────────────────────┐
│  produces: (local_file_path, optional_metadata_dict)                   │
│                                                                          │
│  • filesystem walk        — existing, zero deps                        │
│  • Crawl4AI crawl         — url -> local .md/.html files                │
│  • paper-search-mcp fetch — query -> local .pdf + citation metadata     │
└───────────────────────────────────┬────────────────────────────────────┘
                                     ▼
                          ┌─────────────────────┐
                          │      CLASSIFY         │  pure function:
                          │                       │  extension lookup +
                          │                       │  "looks_scanned" heuristic
                          └──────────┬───────────┘
                                     ▼
              ┌──────────────────────────────────────────────┐
              │              CONVERT (tiered, cheapest first)   │
              │                                                  │
              │  1. passthrough   — code/text/existing .md        │
              │  2. anydoc        — office docs, text-layer PDF   │
              │  3. local OCR     — MinerU (scanned/complex/CJK)  │
              │  4. VLM fallback  — LiteLLM, only if tier 3 fails │
              │                     or confidence is low          │
              └──────────────────────┬───────────────────────┘
                                     ▼
                          ┌─────────────────────┐
                          │     NORMALIZE          │  unify to:
                          │                       │  <name>.md
                          │                       │  <name>_assets/*
                          │                       │  + YAML frontmatter
                          │                       │    (source, url, authors,
                          │                       │     date, citation id)
                          └──────────┬───────────┘
                                     ▼
                     output/  (mirrored tree + index.md)
```

**Key invariant carried through the whole design**: every stage produces the same output contract regardless of which acquisition source or conversion tier touched it. The viewer, and any downstream RAG pipeline, never needs to know whether a file came from a local disk, a crawled webpage, or a downloaded paper, or whether it was converted by anydoc, MinerU, or a VLM call.

---

## 2. Dependencies (all existing repos — nothing reimplemented)

| Role | Repo | License | Notes |
|---|---|---|---|
| Office/text-layer docs → md | [firecrawl/anydoc](https://github.com/firecrawl/anydoc) | MIT | Rust, ~4.7ms median, no OCR (text layer only) |
| Scanned/complex-layout OCR | [opendatalab/mineru](https://github.com/opendatalab/mineru) | Apache-2.0-based (MinerU OSS License) | Full pipeline, extracts images to its own folder; free for commercial redistribution below 100M MAU/$20M-mo revenue — attribute in README |
| OCR serving acceleration (optional, later) | [aiptimizer/TurboOCR](https://github.com/aiptimizer/TurboOCR) | MIT | Only needed if MinerU throughput becomes the bottleneck |
| Website crawl → markdown | [unclecode/crawl4ai](https://github.com/unclecode/crawl4ai) | Apache-2.0 | Self-hosted, Playwright-driven, no API key, built for RAG output |
| Academic paper search + download | [openags/paper-search-mcp](https://github.com/openags/paper-search-mcp) | MIT | arXiv, PubMed, bioRxiv, medRxiv, Semantic Scholar, OpenAlex, Crossref, IACR ePrint, Google Scholar |
| Multi-provider LLM/VLM gateway | [BerriAI/litellm](https://github.com/BerriAI/litellm) | MIT | One interface → DeepSeek/OpenAI/Anthropic/OpenRouter/Bedrock/Vertex + self-hosted Ollama/vLLM/NIM/LM Studio |

---

## 3. Config file (the whole point of the exercise)

```toml
# config.toml — shipped default uses DeepSeek; every other provider is a
# copy-paste block away, no code changes required.

[vlm.default]
model = "deepseek/deepseek-vl"
api_key_env = "DEEPSEEK_API_KEY"

# --- swap targets, uncomment as needed ---
# [vlm.default]
# model = "openrouter/anthropic/claude-3.5-sonnet"
# api_key_env = "OPENROUTER_API_KEY"
#
# [vlm.default]
# model = "openai/gpt-4o"
# api_key_env = "OPENAI_API_KEY"
#
# [vlm.default]
# model = "anthropic/claude-3-5-sonnet"
# api_key_env = "ANTHROPIC_API_KEY"
#
# [vlm.default]                          # fully local, no cloud, no key
# model = "openai/llava"                 # NIM/vLLM/Ollama all speak this shape
# api_base = "http://localhost:8000/v1"

[vlm.fallback]                           # optional second attempt if default fails
model = "openrouter/anthropic/claude-3.5-sonnet"
api_key_env = "OPENROUTER_API_KEY"

[crawl]
max_depth = 2
max_pages = 200
respect_robots_txt = true

[papers]
default_sources = ["arxiv", "semantic_scholar", "openalex"]
default_limit = 20
```

---

## 4. CLI surface (target)

```bash
ingest dir <path> [--output DIR] [--ocr-images] [--vlm-role default]
ingest web <url> [--depth N] [--max-pages N] [--output DIR]
ingest papers "<query>" [--sources arxiv,semantic_scholar] [--limit N] [--output DIR]
ingest --selftest        # routing-logic + config validation, no network/backends needed
```

One binary, three acquisition verbs, identical output contract.

---

## 5. Phased build order

**Phase 0 — Scaffolding**
Repo layout, `pyproject.toml`/`requirements.txt`, `config.example.toml`, README with the license table above (attribution requirement noted for MinerU).

**Phase 1 — Local directory ingestion** *(mostly done — `ingest.py` exists)*
- Verify actual `anydoc` and `mineru` CLI flags against real installs (the flags in the current script are best-guess from docs, not yet run against real binaries).
- Confirm MinerU's actual output layout (`<stem>/<stem>.md` + `images/`) matches what the script assumes.

**Phase 2 — VLM fallback tier**
- Add `describe_page(image_bytes, prompt, role="default") -> str` wrapping `litellm.completion(...)`.
- Wire it as tier 4: triggered when MinerU's own confidence/quality signal is low (need to check what MinerU actually exposes — page-level confidence score, or fall back to a cheaper heuristic like "output text density stayed near-zero even after OCR").
- Add response caching keyed by image-content-hash + role, so re-runs don't re-bill.
- Config loader for the `[vlm.*]` blocks above.

**Phase 3 — Web acquisition**
- Thin adapter: `crawl_site(url, max_depth, max_pages) -> list[Path]` calling Crawl4AI's Python API.
- Crawled pages mostly already arrive as Markdown — route straight to Normalize, skipping Convert.
- Politeness config (`respect_robots_txt`, `max_pages`) passed through from `config.toml`.

**Phase 4 — Paper acquisition**
- Thin adapter: `fetch_papers(query, sources, limit) -> list[tuple[Path, metadata_dict]]` calling paper-search-mcp.
- Downloaded PDFs re-enter the normal Convert pipeline at tier 2 (anydoc) — arXiv-style PDFs are text-layer, so tier 3/4 should rarely trigger for these.
- Metadata dict (title/authors/abstract/source-id/URL) flows into Phase 5.

**Phase 5 — Metadata/frontmatter normalization**
- Extend Normalize to accept an optional metadata dict and emit YAML frontmatter on the output `.md` (title, authors, source URL, retrieved-date, source-id).
- This is the only genuinely new logic in the whole plan — everything else is adapter glue around existing tools.

**Phase 6 — Packaging**
- `argparse` subcommands matching the CLI surface above.
- `pip install`-able package (or single-file script + `requirements.txt` — no Docker requirement, so "clone and run" stays true).
- `config.example.toml` → copy to `config.toml`, drop in a key (or point at a local server), done.

**Phase 7 — Validation**
- Real multi-page doc-heavy website crawl.
- Real arXiv query → download → convert → frontmatter check.
- A deliberately scanned/low-quality PDF to confirm the tier-3→tier-4 VLM fallback actually triggers and gets cached.

---

## 6. Open items to confirm before/while building

- Exact `anydoc`/`mineru` CLI invocation (guessed from docs, unverified against real binaries).
- What confidence/quality signal MinerU exposes to gate the VLM fallback decision (vs. a cruder text-density heuristic).
- arXiv/Semantic Scholar rate limits — paper-search-mcp likely handles this internally, but worth confirming before hammering a query with `--limit` set high.
- Whether Crawl4AI's default markdown extraction needs tuning (nav/footer stripping) per-site, or works well enough out of the box for a general-purpose tool.

---

## 7. What stays exactly as already built

`ingestion/ingest.py`'s `route()` / `looks_scanned()` / normalize-to-`<name>.md`-plus-`<name>_assets/` design doesn't change. Everything in this plan is additive — new acquisition sources feeding the same classify→convert→normalize spine, and a new cost-gated conversion tier slotted in above the existing ones.
