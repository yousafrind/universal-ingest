# Universal Ingest — Implementation Memo

Last updated: 2026-09-10

## 1. What it does

Universal Ingest turns four kinds of input into a mirrored tree of Markdown
files, viewable in any Markdown reader and ready to feed to an LLM/RAG
pipeline:

| Input | Command | Output |
|---|---|---|
| A local directory or single file (PDF, DOCX, PPTX, XLSX, LaTeX, code, images, ...) | `ingest dir <source> <output>` | One `.md` per source file, mirroring the source tree, + `index.md` |
| A website | `ingest web <url> <output>` | One `.md` per crawled page + `index.md` |
| A list of seed URLs ("LLM wiki" mode) | `ingest wiki <urls.txt> <output>` | One consolidated `.md` per seed URL/site (all its crawled pages + any linked documents it could download and convert, folded into a single file) + `index.md` |
| A topic/query, searched across academic sources | `ingest papers "<query>" <output>` | One `.md` per downloaded paper + `index.md` |

Every `.md` file is self-contained: embedded/extracted images live in a
sibling `<name>_assets/` folder and are referenced by relative path, so the
whole output tree is portable (copy it anywhere, no absolute paths, no
external database).

## 2. Design principles

- **Delegate to specialist tools, don't reimplement parsers.** The project
  never parses a PDF or DOCX byte-for-byte itself; it orchestrates existing
  libraries/CLIs (kreuzberg, MinerU) and normalizes their output.
- **Tiered, cost-gated fallback.** Cheapest/fastest tool tried first; slower
  or heavier tools only invoked for the specific pages that actually need
  them — never the whole document, just because *some* part of it is hard.
- **Config-driven provider swap for anything that calls an LLM.** One
  `config.toml`, one `provider/model` string (via `litellm`) — switching
  between a cloud API, a self-hosted server, or a local `llama.cpp` process
  is an edit to a gitignored config file, never a code change.
- **Never silently drop content.** Every fallback tier exists because the
  tier above it was found, empirically, to drop something silently (see
  §5, "Why the tiers exist" — this list is the project's real debugging
  history, not a hypothetical design doc).
- **Isolated environments only.** Dependencies live in the project's own
  `.venv`; nothing is installed into a shared/global environment. See
  `resolve_tool()` in `convert.py` for the runtime safeguard (prefers the
  venv's own copy of a tool over a bare `PATH` lookup).

## 3. How the document pipeline works (`ingest dir` / `ingest papers` / downloaded docs inside `ingest wiki`)

Every document — regardless of entry point — goes through the same function:
`convert.convert_document(src, out_dir, config, assets_relname)`. It returns
a `Document` (see `document.py`): a list of `Page(index, text, source)`
objects plus any extra HTML tables and binary assets (images), never a flat
string. That page-indexed model is what makes the tiered fallback possible:
each tier can inspect, and selectively overwrite, individual pages.

```
PDF / DOCX / PPTX / XLSX / LaTeX / ... (any format kreuzberg understands)
        │
        ▼
┌─────────────────────┐
│ Tier 1 — kreuzberg    │  Fast, MIT-licensed, no ML weight for native-text
│ (convert_kreuzberg)   │  documents. Extracts text (page-indexed) + real
└─────────┬────────────┘  HTML tables. ~2-3 seconds for a 678-page book.
          │
          ▼
   Flag pages that are either:
     - low-content (< 30 chars — kreuzberg silently returns near-empty
       text for scanned/image-only pages instead of erroring), OR
     - "caption" pages (text matches /Figure N|Fig\. N|Table N/ — has
       plenty of text, but the figure/table/equation itself needs a real
       layout model, not plain text extraction)
          │
          ▼ (only the flagged pages, in batches of 3)
┌─────────────────────┐
│ Tier 2 — MinerU       │  Real layout model: HTML tables (with rowspan),
│ (convert_mineru_pages)│  LaTeX equations, cropped figure images — not a
└─────────┬────────────┘  photo of the whole page. Page-scoped: only the
          │                flagged pages are extracted to a temp PDF and
          │                handed to MinerU, never the whole document.
          │                Batched 3 pages/call to cap peak memory (loading
          │                layout+table+formula models at once for many
          │                pages can exceed available pagefile/RAM).
          ▼ (pages a MinerU batch still couldn't fix, or MinerU unavailable)
┌─────────────────────┐
│ Tier 3 — local VLM    │  Config-swappable (config.toml): DeepSeek/OpenAI/
│ (vlm.describe_page)   │  Anthropic/OpenRouter cloud API, or a fully local
└─────────┬────────────┘  server (llama.cpp / Ollama / vLLM / NVIDIA NIM) —
          │                same litellm client either way. Renders the page
          │                to an image (kreuzberg's render_pdf_page) and
          │                asks the model to transcribe it to Markdown.
          ▼ (pages still on tier 1 after tiers 2+3 - MinerU/VLM unavailable)
┌─────────────────────┐
│ Fallback — snapshot   │  Last resort: attach a full-page PNG snapshot so
│ (_attach_figure_      │  nothing is silently dropped, even if no OCR/VLM
│  snapshots)           │  tool is available at all.
└─────────┬────────────┘
          ▼
  Document.to_markdown() → normalize.write_markdown() → <name>.md
  Document.assets        → normalize.write_document_assets() → <name>_assets/
```

## 4. How web/wiki ingestion works

- **`ingest web`** (`pipeline.ingest_web`): `acquire_web.crawl_site()` runs a
  BFS crawl via Crawl4AI (`max_depth`/`max_pages` from `config.toml`, or CLI
  flags). Each crawled page becomes its own `<slug>.md`.
- **`ingest wiki`** (`pipeline.ingest_wiki`): takes a list of seed URLs (one
  per line in a text file). For each seed URL: crawl it the same way, but
  instead of one file per page, concatenate every crawled page into **one**
  Markdown file per seed URL/site, with a table of contents. It also scans
  every crawled page's links for anything `classify.py` recognizes as a
  document format (PDF/DOCX/etc — see `acquire_web._document_links()`),
  downloads those, and runs them through the *same* `convert_document()`
  tiered pipeline as `ingest dir`, folding the result into the same
  consolidated file under a "Downloaded documents" section.
  Output filenames are keyed by the **full** slug of the seed URL (not just
  the domain) — a real bug found via live testing: two different GitHub
  repos, or two different arXiv papers, both slugify to the same *domain*
  and would otherwise silently overwrite each other's output file.

## 5. Why the tiers exist (debugging history, condensed)

This is not a hypothetical design — every fallback tier below exists because
the tier above it was proven, empirically, to fail in a specific way:

1. **anydoc → kreuzberg.** The original tier-1 tool (`@firecrawl/anydoc`, a
   Node CLI) had no layout model at all and no LaTeX/BibTeX/Jupyter/email
   support. Replaced with kreuzberg (MIT, Rust core, real table-structure
   models, much broader format coverage).
2. **Whole-document MinerU → page-scoped MinerU.** kreuzberg doesn't error
   on a page it can't read — it just returns near-empty text. The original
   fallback logic responded to "this document has *a* scanned page" by
   OCR'ing the *entire* document with MinerU. Verified live: a 678-page
   book took ~1h15m to OCR in full for the sake of ~39 actually-scanned
   pages. Fixed by flagging specific pages and extracting only those to a
   temp PDF for MinerU.
3. **Single large MinerU batch → batches of 3.** Routing all flagged pages
   (including caption/figure/table/equation pages, not just low-content
   ones) through MinerU in one call means loading its layout + table +
   formula models simultaneously. On a real 14-page batch this crashed with
   `OSError: The paging file is too small` on a machine with only 30GB free
   disk (not willing to consume 16-32GB of that for a pagefile). Fixed by
   processing flagged pages in small batches (3 at a time), capping peak
   memory while keeping MinerU's per-page throughput (~7-8s/page).
4. **VLM as the default tier-2 replacement → VLM as tier-3 only.** Briefly
   tried replacing MinerU's caption-page handling with a local VLM
   (GLM-OCR via `llama-server`) entirely, to sidestep the memory problem.
   Per-page CPU inference proved far slower in practice (~2+ min/page) than
   MinerU's batched throughput, so VLM was kept as the fallback for when a
   MinerU batch itself fails, not the primary path.
5. **Figure/table snapshot fallback.** kreuzberg's image extraction only
   pulls embedded raster images (JPEG/PNG blobs) — a vector-drawn diagram
   (TikZ/matplotlib PDF output, common in papers) leaves *no* image at all,
   even on an otherwise text-rich page, so the low-content heuristic never
   catches it. Verified live: a real paper's Figure 2 (a workflow diagram)
   came back as disconnected text fragments with zero image. Any
   kreuzberg-sourced page whose text mentions "Figure N"/"Table N" now gets
   routed to MinerU/VLM for real extraction, or — if neither is
   available — a full-page snapshot is attached so nothing is silently lost.

## 6. Configuration (`config.toml`)

Copy `config.example.toml` → `config.toml` (gitignored — safe to put real
API keys in it). Nothing in the codebase changes when you switch providers;
only this file does.

```toml
[vlm.default]
model = "deepseek/deepseek-v4-flash-vision-exp"   # or openai/gpt-4o, anthropic/claude-3-5-sonnet, ...
api_key = "sk-..."                                 # inline, OR:
api_key_env = "DEEPSEEK_API_KEY"                   # read from an env var instead
# api_base = "http://127.0.0.1:8080/v1"            # fully local (llama.cpp/Ollama/vLLM/NVIDIA NIM) - no key needed

[vlm.fallback]        # optional second attempt if [vlm.default] fails
model = "openrouter/anthropic/claude-3.5-sonnet"
api_key_env = "OPENROUTER_API_KEY"

[crawl]
max_depth = 2
max_pages = 200
respect_robots_txt = true

[papers]
default_sources = ["arxiv", "semantic", "openalex"]
default_limit = 20
```

If `config.toml` doesn't exist, `config.py` falls back to a working DeepSeek
default automatically (see `load_config()`).

## 7. External tool requirements

| Tool | Required for | Install |
|---|---|---|
| `kreuzberg` (Python package) | tier 1, always | `pip install kreuzberg` |
| `pypdf` (Python package) | splitting flagged pages before tier 2 | `pip install pypdf` |
| `mineru` CLI | tier 2 (optional — pipeline degrades to tier 3/snapshot without it) | `pip install "mineru[pipeline]"` (Python 3.10-3.12 on Windows) |
| `crawl4ai` (Python package) | `ingest web` / `ingest wiki` | `pip install -U crawl4ai && crawl4ai-setup` |
| `paper-search-mcp` CLI | `ingest papers` | `pip install paper-search-mcp` |
| `litellm` (Python package) | tier 3 (VLM), any config.toml-driven model call | `pip install litellm` |

None of these are hard-linked dependencies of the core orchestration code —
`tool_available()`/`resolve_tool()` checks are used throughout so a missing
tool degrades the pipeline (skips that tier) rather than crashing it.

## 8. Project structure

```
universal-ingest/
├── ingest.py                    Thin CLI entrypoint — delegates to ingest_tool.cli.main()
├── config.toml                  Real config, gitignored (holds API keys if inline)
├── config.example.toml          Template — copy to config.toml and edit
├── requirements.txt             Pip dependencies, annotated by which tier needs what
├── .gitignore                   Excludes .venv/, config.toml, .cache/, output dirs, etc.
├── LICENSE                      MIT + third-party attribution
├── README.md                    User-facing install/usage guide
├── PLAN.md                      Original architecture/design doc (Phase 1-5 planning)
├── docs/
│   └── IMPLEMENTATION.md        This file
├── ingest_tool/                 The actual package
│   ├── __init__.py              One-line package docstring
│   ├── cli.py                   argparse subcommands (dir/web/wiki/papers) + --selftest
│   ├── pipeline.py              Orchestration: ingest_directory / ingest_web / ingest_wiki / ingest_papers
│   ├── classify.py              Pure routing: extension -> passthrough|image|document|skip
│   ├── convert.py               Tiered conversion: kreuzberg -> MinerU (batched) -> VLM -> snapshot
│   ├── document.py              Page-indexed Document/Page data model shared across all tiers
│   ├── normalize.py             Unifies backend output: inline-image extraction, frontmatter, index.md, FAILURES.md
│   ├── config.py                Loads config.toml into typed Config/VLMRole/CrawlConfig/PapersConfig
│   ├── vlm.py                   litellm-backed VLM client (tier 3) + on-disk response cache
│   ├── acquire_web.py           Crawl4AI adapter + downloadable-link discovery + file download
│   └── acquire_papers.py        paper-search-mcp CLI adapter (search + download)
└── .cache/vlm/                  On-disk cache of VLM responses, keyed by (image bytes, role, prompt) hash
```

### Module responsibilities, one line each

- **`cli.py`** — argument parsing only; the `--selftest` flag runs a set of
  pure-logic assertions (routing, the `Document` page model, image
  extraction, frontmatter rendering, config fallback) with no network or
  external-tool dependency, so it can be run in any environment as a smoke
  test.
- **`pipeline.py`** — the only module that knows about *all four* entry
  points and the shared output contract (`<name>.md` + `<name>_assets/` +
  `index.md` + `FAILURES.md`).
- **`classify.py`** — one pure function, `route(path) -> "passthrough" |
  "image" | "document" | "skip"`, based purely on file extension. No I/O.
- **`convert.py`** — everything in §3 above: the three conversion tiers,
  the snapshot fallback, and the `resolve_tool()`/`tool_available()`
  helpers used to detect external CLIs safely (own venv first, then PATH).
- **`document.py`** — `Page` (index, text, source) and `Document` (pages,
  tables, assets) dataclasses, plus `Document.low_content_pages()` and
  `Document.to_markdown()`.
- **`normalize.py`** — backend-agnostic output shaping: pulls inline
  base64 images out of any backend's markdown into real files, renders
  YAML frontmatter, writes the final `.md`, and builds `index.md` /
  `FAILURES.md` for a whole run.
- **`config.py`** — one `load_config(path) -> Config` function; falls back
  to a working DeepSeek default if `config.toml` is missing.
- **`vlm.py`** — `describe_page(image_bytes, config, role="default") ->
  (markdown_text | None, error)`, trying `role` then `"fallback"`, with a
  SHA-256-keyed on-disk cache so repeat runs over the same page/prompt
  don't re-bill an API.
- **`acquire_web.py`** — `crawl_site()` (Crawl4AI BFS wrapper),
  `_document_links()` (finds downloadable document URLs on a crawled
  page), `download_file()` (plain `httpx` GET to a local path).
- **`acquire_papers.py`** — thin subprocess wrapper around the
  `paper-search-mcp` CLI's `search`/`download` subcommands.

## 9. Output contract

Every entry point produces the same shape under its output directory:

```
<output>/
├── index.md                     Links to every successfully converted item
├── FAILURES.md                  Present only if something failed (path/URL + reason)
├── <name>.md                    One per source file/page/site/paper
├── <name>_assets/                Images (extracted, cropped, or full-page snapshots)
│   ├── img1.png                  Inline base64 images pulled out by normalize.py
│   ├── mineru_<hash>.jpg         Real cropped figures/charts from MinerU (tier 2)
│   └── page<N>_snapshot.png      Last-resort full-page snapshot (see §5.5)
└── <name>_work/                  Scratch dir convert_document() uses for MinerU batches (temp, per-call)
```

`ingest wiki` additionally produces `<site-slug>_downloads/` (staged raw
downloads of linked documents before conversion).

## 10. Known limitations / open items

- **MinerU is optional but recommended.** Without it, scanned pages and
  figure/table/equation pages fall straight to VLM (if configured) or the
  full-page snapshot — functional, but lower fidelity.
- **VLM tier needs either a paid API key or a running local server.** If
  neither is configured/reachable, tier 3 is silently skipped (falls
  through to snapshot) — this is intentional degradation, not a crash, but
  it does mean a misconfigured/offline `llama-server` produces noisy
  `litellm` stderr output for each attempted page (cosmetic, harmless).
- **`MINERU_TASK_RESULT_TIMEOUT_SECONDS`** (not `MINERU_API_TASK_POLLING_
  TIMEOUT`, which this version of MinerU silently ignores) is set to 10800s
  (3 hours) in `convert_mineru_pages()` — verified as the actual env var
  this version's CLI client reads via source inspection, not docs (the
  public docs reference the older/wrong name).
- **The `MINERU_BATCH_SIZE = 3` constant in `convert.py`** is an empirical
  choice (small enough to avoid the pagefile OOM seen on a 30GB-free
  machine, large enough to amortize MinerU's per-call model-load overhead).
  Machines with more RAM/pagefile headroom could likely raise it.
