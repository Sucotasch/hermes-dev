# Project knowledge

This file gives Freebuff context about your project: goals, commands, conventions, and gotchas.

## What this is

Hermes Deep Research Pipeline — deep web research tooling (multi-query search, URL validation, anti-bot bypass, image extraction) with three execution modes sharing the same backend:

1. **Hermes plugin mode** — `hermes-agent/tools/ddg_search_tool.py` registers tools into Hermes `tools.registry` (web toolset: `web_search_deep`, `web_expand_and_fetch`, `visit_website_tool`, `image_search`, `web_deep_research`, `web_extract`, `web_search`).
2. **Standalone CLI/GUI** — `standalone/deep_research.py` + `standalone/gui.py` (PyQt5), driven by `standalone/orchestrator.py` with a local OpenAI-compatible LLM (llama.cpp/Ollama) for synthesis.
3. **DeepSeek Harness bridge** (v2+) — `webtools_bridge.py` loads the Hermes wrapper with a no-op registry stub and exposes `search`/`read`/`image`/`expand`/`render` as plain Python CLI primitives. Used by the Harness skill `hermes-web-tools` (at `~/.dsh/skills/`). Vendored Deno 2.7.7 + happy-dom in `deno/` provides inline-JS execution without a headless browser.

This repo is the **durable source of truth**: files are restored into the live Hermes install at `~/.hermes/` via `restore.ps1` because Hermes updates overwrite the live copies.

> ⚠️ **Version skew: the standalone pipeline is the newer, authoritative implementation.**
> The Hermes wrapper version (`hermes-agent/tools/ddg_search_tool.py`) is **not kept in sync** and still
> contains problems that were already fixed in standalone (last sync ~2026-07-05). Treat the standalone
> pipeline as the reference for correct behavior; see "Standalone vs Hermes" below.

## Key locations

| Path | Role |
|------|------|
| `hermes-agent/tools/ddg_search_tool.py` | Wrapper: tool registration, `query_type` schemas, post-retrieval filter |
| `plugins/web-tools/ddg/ddg_search.py` | Backend: search strategies, validation, blocklist, relevance scoring, images |
| `plugins/web-tools/ddg/visit_website_enhanced.py` | Fetcher: curl_cffi impersonation, httpx, Jina, overlay stripping |
| `plugins/web-tools/ddg/query_variants.py` | Intent-aware query variant generator |
| `plugins/web-tools/ddg/compose.py` | Markdown formatter (compose mode) |
| `standalone/orchestrator.py` | Standalone pipeline (reuses `plugins/web-tools/ddg/` unchanged) |
| `standalone/deep_research.py`, `standalone/gui.py`, `standalone/llm_client.py` | CLI / PyQt5 GUI / LLM client |
| `webtools_bridge.py` | Harness bridge: registry-stub loader, 5 subcommands, disk cache, Wayback fallback, proxy/impersonation flags |
| `js_engine/render_worker.js` | Deno happy-dom worker (inline-JS execution); engine binaries in gitignored `deno/` |
| `skills/web-deep-search/SKILL.md`, `skills/restore-context/SKILL.md` | Skills copied into `~/.hermes` |
| `restore.ps1` | PowerShell restore script (7 files + backup + compose smoke probe) |
| `CONTEXT.md` / `PROJECT_CONTEXT.md` / `README.md` | Durable session context, detailed docs (read for depth) |

## Standalone vs Hermes: status as of 2026-08-10

The wrapper was updated (Audit batches B/C): `query_type` is forwarded to `search_deep`; visual image URLs use `thumbnail`/`page_url`; `visit_website_tool` caps at 8000 chars; evidence keeps only alive pages; per-domain source quota; dedup inversion fixed; `video` added to schema enums, classifier, and suffix tables (backend `query_variants.py`); real domain quarantine and deferred 503 handling now work in both pipelines; `_check_url_live` retry paths active; tests 22/22 green.

Still standalone-only by design (orchestrator deep-read phase): platform-aware dedup (blogspot/livejournal), mirror domains (bunkr), query-string dedup, `img_bonus` gallery detection, GettyImages filter for person, sort-by-text-length before deep-read, gallery links section, `_filter_images_for_report`.

Remaining gaps in the wrapper: no LLM enrichment (aliases for person queries — standalone `enrich_query`), schema enum still lacks `person/fact/science/education/art`, visual images still come from `image_search` rather than page-HTML extraction.

When changing pipeline behavior: implement/verify in standalone first, then port to the wrapper deliberately while preserving wrapper invariants (top-level `registry.register()`, policy-free backend).

## Commands

```bash
# Install deps (no package.json/requirements.txt — install manually)
pip install httpx curl_cffi ddgs beautifulsoup4 lxml PyQt5 Pillow

# Run GUI
python standalone/gui.py          # or double-click gui_launcher.bat

# Standalone CLI (needs a local LLM server)
python standalone/deep_research.py "query" --server http://localhost:8888

# Tests (no test config; standalone pytest files)
python -m pytest plugins/web-tools/ddg/test_query_variants.py
python -m pytest hermes-agent/test_coverage_gate.py
python test_quick.py               # manual liveness smoke test
python test_pipeline.py            # end-to-end run (needs LLM server on 8888)

# Compile checks
python -m py_compile plugins/web-tools/ddg/ddg_search.py
python -m py_compile plugins/web-tools/ddg/visit_website_enhanced.py
python -m py_compile hermes-agent/tools/ddg_search_tool.py

# Deploy to Hermes (dry-run first; PowerShell required)
powershell.exe -File restore.ps1 -DryRun -SkipBackup -NoStopHermes
powershell.exe -File restore.ps1
```

No linting/formatting/build config exists.

## Conventions & invariants (do not break)

- **Edit here, apply there**: edit in this repo → commit → restore. Never edit `~/.hermes/` directly without mirroring back.
- `registry.register(...)` calls must stay at **top level** of `ddg_search_tool.py` (moving them into conditionals/functions breaks Hermes tool discovery).
- `query_type` is the **sole intent mechanism** — backend (`ddg_search.py`) is policy-free, no topic/visual/coverage keyword branching. `_is_visual_topic()` was removed.
- `visit_website_tool` name is hard-coded in the wrapper; renaming requires updating prompts + skill files.
- Native `web_search` and `web_extract` are untouched fallbacks. `web_extract` often returns 0 chars — `visit_website_tool` is the primary fetcher.
- httpx 0.28.1: use `proxy=` (single URL), **not** `proxies=` dict.
- Main sessions always direct; proxy is a retry mechanism only (`USE_PROXY=False` default, NECOBOX at `127.0.0.1:2080`).

## Gotchas

- 40–46% of URLs are blocked (403/Cloudflare); proxy retry helps ~5–10%. IMDB/Wikipedia/Reddit are effectively unreachable.
- `query_variants` and `_coverage` modules are now copied by `restore.ps1` (was a known post-restore gap); if missing, backend/wrapper degrade gracefully with fallbacks.
- `browser_dialog_tool.py` is a stub — don't rely on it.
- `curl_cffi` caches one session per proxy+impersonation combo; restart Hermes after changing proxy env at runtime.
- `content_relevance_score` is keyword-based — can't disambiguate namesakes like "Sara James" vs "Sara St James" without the entity-phrase gate.
- Platform domains (blogspot, livejournal) use **path-based** dedup, not base-domain dedup.
- Level 2 expansion triggers when `relevant_alive < 15`; image search only when `query_type == "visual"`.
