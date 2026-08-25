# AGENTS.md — Hermes Custom Tools Dev Repo

## What this repo is

This is **not** the main Hermes application. It's a deep research pipeline for network search, plus a standalone CLI variant, plus the DeepSeek Harness bridge (the agent's own web tooling). Files here get restored into `~/.hermes/` via `restore.ps1`. Hermes updates regularly overwrite the live copy — this repo is the durable source of truth.

## Critical workflow: edit here, apply there

1. Edit files in this repo
2. `git add` + `git commit`
3. Run restore: `powershell.exe -File restore.ps1`
4. Dry-run first: add `-DryRun -SkipBackup -NoStopHermes`
5. Verify after restore: `python -m py_compile` on key files under `~/.hermes/`
6. On Linux/macOS (no PowerShell): copy files manually:
   ```bash
   cp hermes-agent/tools/ddg_search_tool.py ~/.hermes/hermes-agent/tools/
   cp hermes-agent/tools/browser_dialog_tool.py ~/.hermes/hermes-agent/tools/
   cp plugins/web-tools/ddg/*.py ~/.hermes/plugins/web-tools/ddg/
   cp skills/web-deep-search/SKILL.md ~/.hermes/skills/web-deep-search/
   cp skills/restore-context/SKILL.md ~/.hermes/skills/restore-context/
   cp CONTEXT.md ~/.hermes/
   ```

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

No test runner config (no `pyproject.toml`, no `Makefile`). Tests are standalone pytest files. No linting or formatting config.

## Restore script details

`restore.ps1` copies 7 files (not just the plugins), backs up skill files before overwriting, and runs a compose smoke probe after restore. The probe tests `web_deep_research` with `compose=True` to verify the tool chain works end-to-end.

## Architecture

### Three execution modes

1. **Hermes plugin mode** — wrapper registers tools with Hermes `tools.registry`. Main entry for interactive use.
2. **Standalone CLI** — `standalone/deep_research.py` drives the same plugin code directly via `orchestrator.py`, with a local LLM (llama.cpp) for synthesis. Reuses `plugins/web-tools/ddg/` unchanged.
3. **DeepSeek Harness bridge** — `webtools_bridge.py` loads the Hermes wrapper with a no-op `tools.registry` stub (no Hermes framework needed) and exposes `search`/`read`/`image`/`expand`/`render` as CLI primitives. This is the agent's own web tooling; companion skill `hermes-web-tools` lives in the Harness skill store (`~/.dsh/skills/`), NOT in this repo. Uses a vendored Deno engine (`deno/`, gitignored) for inline-JS rendering.

### Wrapper → Backend pattern

- `hermes-agent/tools/ddg_search_tool.py` — the wrapper. Registers tools with Hermes `tools.registry` via `registry.register(...)` at **module top level** (not inside functions). Uses `spec_from_file_location` to load plugins by absolute path.
- `plugins/web-tools/ddg/ddg_search.py` — the backend. Search strategies, URL validation, classification, bot-challenge tagging. Policy-free (no topic branching).
- `plugins/web-tools/ddg/visit_website_enhanced.py` — enhanced fetcher. curl_cffi + httpx fallback + Jina. Handles Cloudflare, age gates, cookie consent.
- `plugins/web-tools/ddg/query_variants.py` — intent-aware query variant generator. Frequently missing after restore; backend degrades gracefully.
- `plugins/web-tools/ddg/compose.py` — markdown formatter (compose mode).
- `standalone/orchestrator.py` — standalone pipeline. Imports `ddg_search` + `visit_website_enhanced` directly, uses `llm_client.py` for synthesis.
- `webtools_bridge.py` — harness bridge (repo root). Stub-registry loader + CLI subcommands + disk cache + Wayback fallback + proxy/impersonation flags + Deno `render`.
- `js_engine/render_worker.js` — Deno happy-dom worker used by the bridge's `render` (engine binaries in gitignored `deno/`).

### Registered tools (web toolset)

`web_search_deep`, `web_expand_and_fetch`, `visit_website_tool`, `image_search`, `web_deep_research`, `web_extract`, `web_search`

### Standalone CLI

```bash
python standalone/deep_research.py "your query" --server http://localhost:8888
```

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
- Platform domains (blogspot, livejournal) use path-based dedup, not base domain.

### Bridge gotchas

- `webtools_bridge.py` runs standalone — needs `curl_cffi`, `ddgs`, `httpx`, `bs4` in the invoking Python. It imports the wrapper with a stub `tools.registry`; if the real Hermes package is installed in the same interpreter, the stub is still installed first (idempotent).
- Bridge stdout is ASCII-only status lines; all payload goes to the `--out` JSON file (UTF-8). Non-ASCII on stdout breaks on cp1251 consoles.
- Cache dir is `%TEMP%\hermes_web_cache\`; `read`/`readweak` key split keeps failed reads from being cached 6 h (weak = 5 min).
- Wayback: availability API rate-limits (429) — bridge retries once with backoff; don't hammer it in loops.
- Deno `render` is fail-open: weak output (<300 chars) falls through to the plain read ladder. Deno binary+cache live in gitignored `deno/`; `--node-modules-dir=auto` recreates `node_modules/` (gitignored) on first call from the vendored cache.

## Git operations in the Harness sandbox

The sandbox blocks Git Bash sh.exe (`couldn't create signal pipe, Win32 error 5`), which breaks
credential helpers that spawn prompt scripts (GCM `manager`, askpass). Symptom: `fatal: could not
read Username/Password`. Also the Windows cert store is blocked (`schannel SEC_E_NO_CREDENTIALS`).

**Working solution (already applied to this repo's origin):**
- The `origin` remote URL carries a token: `https://x-access-token:TOKEN@github.com/...`
  (token obtained via `gh auth token`; `gh` CLI is authenticated with `repo` scope).
- TLS works because the token URL bypasses the credential helpers entirely; the
  `sh signal pipe` stderr noise is harmless — judge success by git's own output
  (`Everything up-to-date`, `-> master`, etc.), not by the stderr noise.
- If the token remote ever stops working: `git remote set-url origin "https://x-access-token:$(gh auth token)@github.com/Sucotasch/hermes-dev.git"` (run inside a pwsh tool call).
- Do NOT store plaintext tokens in tracked files; `git remote -v` shows the token — don't paste
  remote output into commits/issues.

## Deep research pipeline rules

After Level 1 (`web_search_deep`):
- If relevant_alive < 15 → run Level 2 via `web_expand_and_fetch`
- If `query_type == "visual"` → call `image_search`

Fetcher fallback: `visit_website_tool` first → proxy retry if <500 chars or challenge markers → `web_extract` as last resort.

## Dependencies

Python 3.11.11 (venv at `hermes-agent/venv`). Key packages: `httpx` 0.28.1 (`proxy=` not `proxies=`), `curl_cffi` 0.14.0, `ddgs` 9.14.4, `bs4` 4.13.4, `lxml` 6.0.2.
