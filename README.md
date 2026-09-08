# Universal Ingest

Turn a local directory, a website, or an academic-paper search into a mirrored tree of
clean Markdown — viewable in any Markdown viewer (Obsidian, VS Code preview, etc.) and
ready to feed into a RAG pipeline.

Three acquisition modes, one output shape:

```bash
python ingest.py dir <source_dir> <output_dir>
python ingest.py web <url> <output_dir> [--depth N] [--max-pages N]
python ingest.py papers "<query>" <output_dir> [--sources arxiv,semantic] [--limit N]
```

```
output/
├── index.md              ← entry point, links every converted file
├── some-doc.md
├── some-doc_assets/       ← images extracted from some-doc, if any
│   └── img1.png
└── ...
```

## Status

**Early, honestly.** Built and tested end-to-end for the three core paths described
below, by one person, in a short build session. No automated CI, no pytest suite beyond
a self-check of the pure routing/config/normalize logic (`--selftest`). Treat it as a
working prototype, not a hardened production tool — read the verification table below
before relying on any specific tier.

| Path | Verified how |
|---|---|
| `dir` — passthrough (code/text/`.md`) | Live test |
| `dir` — Office docs / text-layer PDFs (anydoc) | Live test, real `.docx` |
| `dir` — scanned PDF OCR fallback (MinerU) | Live test, real scanned PDF, correct OCR transcription confirmed |
| `dir` — VLM fallback (litellm → DeepSeek/OpenRouter/etc.) | Request plumbing verified only — **never run against a live API key** |
| `web` — site crawl (Crawl4AI) | Live test, real multi-page deep crawl |
| `papers` — search + download (paper-search-mcp) | Live test, real arXiv search → download → convert → frontmatter |

## Why this exists

Built while working through document-ingestion design for a RAG pipeline. Rather than
reimplement OCR, crawling, or paper search, it orchestrates existing, well-regarded
tools behind one consistent interface and output contract — see `PLAN.md` for the full
architecture reasoning and why each dependency was chosen over its alternatives.

## Install

This project pins **Python 3.10** because MinerU's Windows wheel requires 3.10–3.12
(Linux/macOS support 3.10–3.13). Always use an isolated environment — never install
into your system/global Python.

```bash
uv venv --python 3.10 .venv
.venv\Scripts\activate          # Linux/macOS: source .venv/bin/activate
uv pip install -r requirements.txt

crawl4ai-setup                  # one-time browser install, needed for `ingest web`
pip install "mineru[pipeline]"  # optional: CPU-only OCR fallback, ~1-2GB of models
```

Node.js 20+ is required for `ingest dir` (anydoc runs via `npx`, no separate install
beyond having Node on PATH).

## Configure the LLM/VLM layer

Copy `config.example.toml` to `config.toml` and set an API key env var. Ships with
DeepSeek as the default — every other provider is a copy-paste block swap, no code
changes required:

```toml
[vlm.default]
model = "deepseek/deepseek-v4-flash-vision-exp"
api_key_env = "DEEPSEEK_API_KEY"
```

Swap targets are documented inline in `config.example.toml`: OpenRouter, OpenAI,
Anthropic, or a fully local/self-hosted model (Ollama, vLLM, NVIDIA NIM) via a custom
`api_base` — no cloud dependency at all in that last case. This tier is only used when
a scanned PDF needs OCR and MinerU isn't installed, or when you explicitly want
VLM-based transcription instead.

**Never commit `config.toml`** once it has a real key in it — `.gitignore` already
excludes it; only `config.example.toml` (no secrets) is meant to be tracked.

## Usage

```bash
python ingest.py dir <source_dir> <output_dir> [--ocr-images] [--dry-run]
python ingest.py web <url> <output_dir> [--depth N] [--max-pages N]
python ingest.py papers "<query>" <output_dir> [--sources arxiv,semantic] [--limit N]
python ingest.py --selftest     # verify routing/config/normalize logic, no network needed
```

## How conversion actually works

```
ACQUIRE (filesystem / Crawl4AI / paper-search-mcp)
   → CLASSIFY (extension + anydoc's own "needs OCR" signal)
      → CONVERT (tiered, cheapest-capable-tool-first)
         → NORMALIZE (unify to <name>.md + <name>_assets/ + YAML frontmatter)
```

| Tier | Tool | Handles |
|---|---|---|
| 1 | built-in | code/text/existing `.md` — passthrough |
| 2 | [anydoc](https://github.com/firecrawl/anydoc) (MIT) | Office docs, text-layer PDFs |
| 3 | [MinerU](https://github.com/opendatalab/mineru) `[pipeline]` (Apache-2.0-based) | Scanned/complex-layout PDFs — fallback when anydoc reports OCR is needed |
| 4 | [litellm](https://github.com/BerriAI/litellm) (MIT) → any VLM | Last-resort transcription, cost-gated, response-cached |

`web` uses [Crawl4AI](https://github.com/unclecode/crawl4ai) (Apache-2.0) for deep,
depth-limited crawling with clean Markdown extraction built in.

`papers` uses [paper-search-mcp](https://github.com/openags/paper-search-mcp) (MIT) —
20+ sources including arXiv, PubMed, bioRxiv, Semantic Scholar, OpenAlex, Crossref.

## Known gaps

- The VLM fallback tier has never been exercised against a live provider — the
  litellm call plumbing is correct per its docs, but that's not the same as verified.
- MinerU's real (undocumented) output layout — `<out_dir>/<stem>/auto/<stem>.md` plus
  several debug artifacts (`_layout.pdf`, `_content_list.json`, etc.) — was found
  empirically during testing, not from official docs, and could change between MinerU
  versions. `convert.py` searches for the markdown file recursively rather than
  hardcoding the path, so it should tolerate minor layout changes.
- No automated test suite beyond `--selftest` (pure logic only — routing, anydoc's
  scanned-PDF error detection, inline-image extraction, frontmatter rendering, config
  fallback). No integration tests run in CI.

## License

MIT (see `LICENSE`) for this project's own code. It shells out to the third-party
tools above as external processes — none of their source is vendored or redistributed
here — so their own licenses govern your use of them independently.
