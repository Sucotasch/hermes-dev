# Hermes custom tools — development context

## Goal
Maintain a stable, recoverable deep-research integration for Hermes custom DDG tools, resilient to Hermes updates/context resets. Intent policy is now LLM-driven via `query_type`; code is monotone and does not branch on topic/visual/coverage keywords.

## Source of truth repo
Path: `D:\Arx\Software Downloads\Hermes copy\hermes-dev`

| Rule | Rationale |
|---|---|
| This repo stores custom files that must be restored into `C:\Users\sucot\.hermes\` after any breakage. | Hermes updates/context resets regularly destroy progress. |
| Keep changes granular: one logical fix per commit with factual background. | Easier revert/audit. |
| Before editing, git status and inspect. Commit only after verified state change. | Prevents phantom changes. |
| Update this file after every confirmed fix or plan change. | CONTEXT.md is the durable memory. |

## File map
```
repo:
  CONTEXT.md                  <- this file
  RESTORE.md                  <- cheat sheet for manual restore
  restore.ps1                 <- scripted restore (powershell)
  skills/restore-context/SKILL.md  <- agent-facing restore skill
  hermes-agent/tools/
    ddg_search_tool.py        <- wrapper: minimal binding + native fallback; exposes query_type schema
  plugins/web-tools/ddg/
    ddg_search.py             <- backend (fixed classifier, httpx proxy, bot-challenge tagger, per-engine UA)
    visit_website_enhanced.py <- enhanced fetcher (curl_cffi + websockets.asyncio)
    query_variants.py         <- intent-aware variant generator
    compose.py                <- restored/compatible formatter (loaded conditionally)
```

## Verified states and invariants
- Native `web_search` is untouched and must stay as fallback.
- Custom tools are expanded by wrapper (`hermes-agent/tools/ddg_search_tool.py`).
- Wrapper must use `spec_from_file_location` absolute path for deep-level tool registration.
- `query_type` is explicitly typed in both `_schema_search_deep()` and `_schema_deep_research()`, enum: `visual/technical/news/historical/comparison/general`.
- Backend `search_deep` accepts `query_type` but remains monotone: no topic branching, no coverage overrides, no image gating code-side.
- Tags produced by backend: `bot_challenge`. Backend does not drop pages; synthesis side excludes them.

## Included tool contracts
- `web_search_deep` → raw validated pages, classify=False, no compose. Accepts `query_type` (optional).
- `web_expand_and_fetch` → second-level expansion + fetch: accepts `query` + `source_urls`, calls `visit_website_enhanced` across each candidate, returns fetched Level-2 pages for synthesis.
- `web_deep_research` → composite tool: multi-query Level 1 → auto Level 2 if coverage insufficient → image search only when `query_type == "visual"` → unified evidence pack.
- `visit_website_tool` → unchanged enhanced fetcher contract.
- `image_search` → unchanged ddg backend contract; enabled by wrapper only for `visual`, not auto by page content keywords.

## Intent policy (2026-06-11)
- Agent classifies intent before tool call and passes `query_type` explicitly.
- All topic/coverage/image keywords are removed from code; any future override must be LLM-side via `query_type`.
- Without explicit `query_type`, wrapper uses `general` and keeps default behavior.

## Registry/tool visibility (expected)
- `web` toolset: `['image_search', 'visit_website_tool', 'web_expand_and_fetch', 'web_extract', 'web_search', 'web_search_deep', 'web_deep_research']`
- By default `USE_PROXY=False` to avoid depending on local tunneller.
- Proxy rotation is still managed by NECOBOX when enabled; if no NECOBOX, direct connection is used.
- `curl_cffi` session path caches one session per proxy setting. If proxy env changes at runtime, restart Hermes.
- Backoff rule (empirical): only try proxy if blocked ratio ≥ 35% or if `visit_website_tool` returned content-empty or Cloudflare challenge page.

## Hand-off pipeline rule (empirical, 2026-06-06)
- `web_extract` returned **0 chars** on several otherwise alive pages in 2026-06-06 run. Do **not** use it as primary fetcher.
- Preferred fetcher: `visit_website_tool`. If it returns <500 chars or obvious challenge text (e.g. "Checking your browser..."), fallback to proxy-enabled retry.
- After first-level search, **auto-trigger conditional second level** and **images for visual topics**:
  1. If `alive < 15` → call `web_expand_and_fetch(query, source_urls=top_alive_urls)`.
  2. If `query_type == "visual"` → call `image_search(query)` after deep search.
- Multi-query required for deep topics: 4–6 `query_variants` give 50–150 raw URLs vs 10–30 from single query.
- `query_variants` backend module is frequently missing after restore; backend prints warning and continues — acceptable degradation, do not block on it.

## Quick diagnostics
```python
import tools.registry as r
from tools.registry import registry
from tools.registry import discover_builtin_tools
discover_builtin_tools()
web_tools = sorted([n for n, e in registry._tools.items() if getattr(e, 'toolset', None) == 'web'])
print('web tools:', web_tools)
bad = [n for n in web_tools if not registry.get_entry(n).check_fn()]
print('bad check_fn:', bad)
```

## Known blockers and workarounds
1. `query_variants` module missing in many restored states → backend prints a warning and continues with collected results.
2. `browser_dialog_tool.py` is a stub; do not rely on it.
3. `image_search` requires DDG image/Jina pipeline; if degraded, explicit `image_search` tool remains available.
4. `web_expand_and_fetch` is the supported Level 2 path; `web_expand` link-only path remains but is no longer primary.
5. `web_extract` frequently returns empty content; treat it as best-effort, not primary.

## Gotchas and pitfalls
- Hermes tool registration depends on valid AST-visible `registry.register(...)` at top-level of wrapper module. Do not move calls inside conditionals or functions.
- `visit_website_tool` and new `web_expand` maintain the op token mapping; any changes to structure production, instance methods, or session lifetime method use must maintain backwards compatibility.
- `visit_website_tool` name is hard-coded in wrapper register; changing the name requires matching agent prompts and skill references.
- `ddg_search.py` was patched in-place; if Hermes update overwrites it, some paths may regress. Verify after each backend update.
- `httpx`/`curl_cffi` proxy workaround is version-sensitive; upgrades may change exception types.
- `restore.ps1` expects PowerShell. On Git Bash / MSYS2, run explicitly via `powershell.exe -File ...`.

## Dependencies and versions
- Python: 3.11.11 (venv under `hermes-agent/venv`)
- `httpx`/`curl_cffi` and `websockets.asyncio` used by backend modules
- `ddgs` package assumed installed in Hermes venv

## Pre-flight checklist before each deep-research run
- [ ] Confirm wrapper: `registry.get_tool_names_for_toolset('web')` includes `web_search_deep`, `web_expand_and_fetch`, `visit_website_tool`, `image_search`, `web_extract`, `web_search`.
- [ ] Confirm `check_fn=True` for all custom tools.
- [ ] Determine `query_type` before tool call and pass it explicitly.
- [ ] Use multi-query (4–6 variants) unless the query is unambiguous and tight.
- [ ] Default `max_validate` to 200.
- [ ] Preferred fetcher: `visit_website_tool`. `web_extract` is best-effort only.

## Adaptive two-level rule (must follow automatically)
After Level 1 (`web_search_deep`):
1. If `alive < 15` → run **Level 2** `web_expand_and_fetch` on top alive URLs.
2. If `query_type == "visual"` → call **`image_search`** before writing the answer.

## Fetcher fallback policy (must follow)
- Call `visit_website_tool` first.
- If returned text < 500 chars, or page contains challenge markers (`Checking your browser`, `captcha`, `cloudflare`, `Access is denied`) → retry same URL with proxy enabled if available.
- Only after exhaustive retries, consider `web_extract` as best-effort fallback.

## Open plans / current state
1. `web_deep_research` composite tool implemented and registered as the primary routine deep research entrypoint.
2. `web_expand_and_fetch` retained and preferred for Level 2.
3. Evidence selection and synthesis pipeline verified on pinup-art topic; curated evidence and rendered report saved under `~/.hermes_research/`.
4. Post-retrieval Jaccard dedup + per-source URL quotas added to `web_deep_research` path from TinySearch chunk pool selection.
5. Next optional step: migrate coverage rules into Python scoring helper to further reduce manual filtering.

## Decision log
- Classification + on-tool markdown synthesis removed from wrapper-side `web_search_deep` to avoid AI-style template answers like unsatisfactory football-history output.
- New contract: tool returns raw validated pages with extracted text; agent performs thematic synthesis itself using full page content.
- `max_validate` default raised to 200 so the tool explores all collected URLs when total_raw is small.
- Wrapper design remains single-file binding with `spec_from_file_location` to minimize breakage surface during Hermes updates.
- Added `web_expand_and_fetch` as second-level expansion tool following extracted links from fetched sources.
- Made proxy optional in `visit_website_enhanced.py` and aligned the active copy under `~/.hermes`, keeping NECOBOX as an explicit optional local value (`PROXY_URL`, `USE_PROXY`).
- Introduced `query_type` as the single source of intent policy, defined by the LLM before tool call; backend is policy-free.
- Bot-challenge detection retained as metadata-only tagger (`bot_challenge`); synthesis side excludes these pages from final answer.

## Empirical run data (2026-06-06)
- Query: "famous pinup artists, modern trends, modern vs classic pinup art, free pinup art galleries"
- Raw URLs collected: 225
- Unique after dedup: 225
- Alive pages: 95
- First-level time: ~164s
- Pages fetched with useful text (>2KB): 10
- Content-empty / failed fetches: web_extract возвращает 0 chars на otherwise alive URL; visit_website_tool справляется
- Second-level (`web_expand`): вызван, 40 кандидатов за ~27 сек. **Не добавлен в deep-read фазу** — архитектурный gap
- `image_search`: вызван, 10 результатов. **Не добавлены в финальный markdown** — нет автоматической интеграции иллюстраций в отчёт
- Cloudflare challenge: markovart.wordpress.com — "Checking your browser..." (6.85s). Прокси не включён автоматически
- `query_variants` backend module missing → backend raises a usable fallback; pipeline continues.
- Final deliverable after fixes returns raw validated pages including bot-challenge metadata; synthesis must ignore bot-challenge pages.

## Lessons learned from run
- `web_extract` is unreliable as primary fetcher; prefer `visit_website_tool` and fallback to proxy-only retry when blocked or content-empty.
- Need explicit post-search action rules for multi-facet topics: always follow up with `web_expand` if alive count is modest (<15).
- Need auto-image call for artist/visual topics: without `image_search`, final answer lacks visual material, breaking skill expectation.
- Must not rely on `web_extract` if it returns empty; failover must be immediate, not after several retries.
- `web_expand_and_fetch` closes the Level 2 evidence gap.
- Agent-side `query_type` replaces keyword-based branching in code for all visual/technical/news/historical/comparison decisions.
