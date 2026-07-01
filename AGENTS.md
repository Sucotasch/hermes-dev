# AGENTS.md — Hermes Custom Tools Dev Repo

## What this repo is

This is **not** the main Hermes application. It's a deep research pipeline for network search, plus a standalone CLI variant. Files here get restored into `~/.hermes/` via `restore.ps1`. Hermes updates regularly overwrite the live copy — this repo is the durable source of truth.

## Critical workflow: edit here, apply there

1. Edit files in this repo
2. `git add` + `git commit`
3. Run restore: `powershell.exe -File restore.ps1`
4. Dry-run first: add `-DryRun -SkipBackup -NoStopHermes`
5. Verify after restore: `python -m py_compile` on key files under `~/.hermes/`

**Never edit `~/.hermes\` custom files directly without mirroring back to this repo.**

## Two directories, one truth

| Path | Role |
|---|---|
| This repo (wherever cloned) | Edit here. Version-controlled. |
| `~/.hermes/` | Live runtime. Gets overwritten by Hermes updates. |

## Running tests

```bash
# query_variants unit tests (from plugins/web-tools/ddg/):
python -m pytest plugins/web-tools/ddg/test_query_variants.py

# coverage gate test (from hermes-agent/):
python -m pytest hermes-agent/test_coverage_gate.py

# compile check (from ~/.hermes/ after restore):
python -m py_compile plugins\web-tools\ddg\ddg_search.py
python -m py_compile plugins\web-tools\ddg\visit_website_enhanced.py
python -m py_compile hermes-agent\tools\ddg_search_tool.py
```

No test runner config (no `pyproject.toml`, no `Makefile`). Tests are standalone pytest files.

## Architecture

### Two execution modes

1. **Hermes plugin mode** — wrapper registers tools with Hermes `tools.registry`. Main entry for interactive use.
2. **Standalone CLI** — `standalone/deep_research.py` drives the same plugin code directly via `orchestrator.py`, with a local LLM (llama.cpp) for synthesis. Reuses `plugins/web-tools/ddg/` unchanged.

### Wrapper → Backend pattern

- `hermes-agent/tools/ddg_search_tool.py` — the wrapper. Registers tools with Hermes `tools.registry` via `registry.register(...)` at **module top level** (not inside functions). Uses `spec_from_file_location` to load plugins by absolute path.
- `plugins/web-tools/ddg/ddg_search.py` — the backend. Search strategies, URL validation, classification, bot-challenge tagging. Policy-free (no topic branching).
- `plugins/web-tools/ddg/visit_website_enhanced.py` — enhanced fetcher. curl_cffi + httpx fallback + Jina. Handles Cloudflare, age gates, cookie consent.
- `plugins/web-tools/ddg/query_variants.py` — intent-aware query variant generator. Frequently missing after restore; backend degrades gracefully.
- `plugins/web-tools/ddg/compose.py` — markdown formatter (compose mode).
- `standalone/orchestrator.py` — standalone pipeline. Imports `ddg_search` + `visit_website_enhanced` directly, uses `llm_client.py` for synthesis.

### Registered tools (web toolset)

`web_search_deep`, `web_expand_and_fetch`, `visit_website_tool`, `image_search`, `web_deep_research`, `web_extract`, `web_search`

## Invariants — do not break these

- `registry.register(...)` calls must stay at **top level** of `ddg_search_tool.py`. Moving them inside conditionals or functions breaks Hermes tool discovery.
- `visit_website_tool` name is hard-coded in the wrapper. Changing it requires updating agent prompts and skill files.
- `query_type` is the single intent policy mechanism. Backend code must remain monotone — no topic/visual/coverage keyword branching in `ddg_search.py`.
- Native `web_search` is untouched and serves as fallback. Don't modify it.
- `USE_PROXY=False` is the default. Proxy is optional (NECOBOX).

## Gotchas

- `query_variants` module is frequently missing after restore. Backend prints a warning and continues with static fallback — this is expected, not a bug.
- `web_extract` (native Hermes tool) often returns 0 chars on alive pages. Use `visit_website_tool` as primary fetcher.
- `curl_cffi` session caches one session per proxy+impersonation setting. If proxy env changes at runtime, restart Hermes.
- `browser_dialog_tool.py` is a stub — don't rely on it.
- `ddg_search.py` was patched in-place. If Hermes overwrites it, verify behavior after each backend update.
- `restore.ps1` expects PowerShell. On Git Bash / MSYS2: `powershell.exe -File ...`
- httpx 0.28.1 removed `proxies=` keyword — must use `proxy=` (single URL, not dict).
- 40-46% of URLs are blocked (HTTP 403). Proxy retry helps ~5%. Further improvement needs headless browser.
- IMDB, Wikipedia, Reddit blocked by Cloudflare/JS. JS-block detection flags these correctly.
- `content_relevance_score` is keyword-based; can't disambiguate "Sara James" from "Sara St James" without explicit logic.

## Deep research pipeline rules

After Level 1 (`web_search_deep`):
- If relevant_alive < 15 → run Level 2 via `web_expand_and_fetch`
- If `query_type == "visual"` → call `image_search`

Fetcher fallback: `visit_website_tool` first → proxy retry if <500 chars or challenge markers → `web_extract` as last resort.

## Dependencies

Python 3.11.11 (venv at `hermes-agent/venv`). Key packages: `httpx` 0.28.1 (`proxy=` not `proxies=`), `curl_cffi` 0.14.0, `ddgs` 9.14.4, `bs4` 4.13.4, `lxml` 6.0.2.
