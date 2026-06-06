# Hermes custom tools — development context

## Goal
Maintain a stable, recoverable deep-research integration for Hermes custom DDG tools, resilient to Hermes updates/context resets.

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
    ddg_search_tool.py        <- wrapper: minimal binding + native fallback
    browser_dialog_tool.py    <- stub, not fully implemented
  plugins/web-tools/ddg/
    ddg_search.py             <- backend (fixed classifier, httpx proxy)
    visit_website_enhanced.py <- enhanced fetcher (curl_cffi + websockets.asyncio)
    compose.py                <- restored/compatible formatter (loaded conditionally)
```

## Verified states and invariants
- Native `web_search` is untouched and must stay as fallback.
- Custom tools are expanded by wrapper (`hermes-agent/tools/ddg_search_tool.py`).
- Wrapper must use `spec_from_file_location` absolute path for deep-level tool registration.
- Wrapper must not rename backend functions or break AST-discovered registration patterns when add new tools.
- Expanded set of controlled tools combined with low-level proxy handling for visit_website_enhanced second-level coverage represents the only supported architecture.

## Included tool contracts
- `web_search_deep` → original contract preserved: raw validated pages, classify=False, no compose.
- `web_expand` → second-level expansion pass: accepts `query` + `source_urls`, calls `visit_website_enhanced` across each source, extracts normalized links, dedupes by redirect-normalized URL, ranks by anchor/title/url token overlap, returns ranked candidate list.
- `visit_website_tool` → unchanged enhanced fetcher contract.
- `image_search` → unchanged ddg backend contract.

## Expansion scoring
- Candidate score = token overlap between query tokens (len>2, lowercase) and anchor text/href/path tokens.
- Higher overlap → higher rank.
- No keyword lists, categories, or domain authority adjustments.
- If no new meaningful links found → empty candidates.

## Proxy handling
- `visit_website_enhanced` keeps proxy optional; NECOBOX is an explicit local option only (`PROXY_URL`, `USE_PROXY`).
- By default `USE_PROXY=False` to avoid depending on local tunneller.
- Proxy rotation is still managed by NECOBOX when enabled; if no NECOBOX, direct connection is used.
- Critical: `curl_cffi` session path caches one session per proxy setting. If proxy env changes at runtime, restart Hermes.
- Backoff rule (empirical): only try proxy if blocked ratio ≥ 35% or if `visit_website_tool` returned content-empty or Cloudflare challenge page.

## Hand-off pipeline rule (empirical, 2026-06-06)
- `web_extract` returned **0 chars** on several otherwise alive pages in 2026-06-06 run. Do **not** use it as primary fetcher.
- Preferred fetcher: `visit_website_tool`. If it returns <500 chars or obvious challenge text (e.g. "Checking your browser..."), fallback to proxy-enabled retry.
- After first-level search, **auto-trigger conditional second level** and **images for visual topics**:
  1. If `alive < 15` or coverage looks sparse → call `web_expand(query, source_urls=top_alive_urls)`.
  2. If topic includes people/artists/visuals → call `image_search(query)` after deep search.
- Multi-query required for deep topics: 4–6 `query_variants` give 50–150 raw URLs vs 10–30 from single query.
- `query_variants` backend module is frequently missing after restore; backend prints warning and continues — acceptable degradation, do not block on it.

## Registry/tool visibility (expected)
- `web` toolset: `['image_search', 'visit_website_tool', 'web_expand', 'web_expand_and_fetch', 'web_extract', 'web_search', 'web_search_deep']`

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
4. `web_expand` implementation is link-collection-only; it does not evaluate or fetch candidates. Caller must validate/fetch through existing tools.
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
- [ ] Confirm wrapper: `registry.get_tool_names_for_toolset('web')` includes `web_search_deep`, `web_expand`, `visit_website_tool`, `image_search`, `web_extract`, `web_search`.
- [ ] Confirm `check_fn=True` for all four custom tools.
- [ ] Use multi-query (4–6 variants) unless the query is unambiguous and tight.
- [ ] Default `max_validate` to 200.
- [ ] Preferred fetcher: `visit_website_tool`. `web_extract` is best-effort only.

## Adaptive two-level rule (must follow automatically)
After Level 1 (`web_search_deep`):
1. If `alive < 15` **or** key query facets are absent → run **Level 2** `web_expand` on top alive URLs.
2. If query is about people/art/visual culture → call **`image_search`** before writing the answer.

## Fetcher fallback policy (must follow)
- Call `visit_website_tool` first.
- If returned text < 500 chars, or page contains challenge markers (`Checking your browser`, `captcha`, `cloudflare`, `Access is denied`) → retry same URL with proxy enabled if available.
- Only after exhaustive retries, consider `web_extract` as best-effort fallback.

## Coverage gates (request-specific)
- Artists/visual topics: require ≥8 distinct authoritative/alive sources + ≥1 image_search result set.
- Gallery/tool topics: require ≥5 gallery-like sources from distinct hosts.
- Modern vs classic comparisons: require ≥3 sources each for modern and classic.
- If any gate fails after Level 1, trigger Level 2 and re-check.
- Loss of custom tools after Hermes update → mitigate: `restore.ps1`, `skills/restore-context/SKILL.md`, this `CONTEXT.md`.
- Breakage from AST-scan registration requirements → countermeasure: keep `registry.register(...)` at module top-level in wrappers. Use dynamic name-based applies only when add tools.
- Breakage from proxy/httpx changes → keep wrapper path resolution stable; avoid changing backend module names/imports.
- Breakage from schema drift → regenerate `check_fn` after any signature change in backend functions.

## Open plans
1. Replace `browser_dialog_tool.py` stub with full implementation or disable its discovery.
2. Evaluate whether to make `query_variants` optional in backend or force wrapper-side only variants.
3. Consider increasing backend `max_validate` cap once performance/coverage tradeoff is measured.

## Decision log
- Classification + on-tool markdown synthesis removed from wrapper-side `web_search_deep` to avoid AI-style template answers like unsatisfactory football-history output.
- New contract: tool returns raw validated pages with extracted text; agent performs thematic synthesis itself using full page content.
- `max_validate` default raised to 200 so the tool explores all collected URLs when total_raw is small.
- Wrapper design remains single-file binding with `spec_from_file_location` to minimize breakage surface during Hermes updates.
- Added `web_expand` as second-level expansion tool following extracted links from fetched sources. It does not fetch candidates; it returns ranked URLs for the caller to validate under existing rules.
- Made proxy optional in `visit_website_enhanced.py` and aligned the active copy under `~/.hermes`, keeping NECOBOX as an explicit optional local value (`PROXY_URL`, `USE_PROXY`).

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
- `query_variants` backend module missing → warning, но пайплайн продолжает работу
- Final deliverable (до исправлений): синтезированный markdown без иллюстраций, без ссылок из Level 2

## Lessons learned from run
- `web_extract` is unreliable as primary fetcher; prefer `visit_website_tool` and fallback to proxy-only retry when blocked or content-empty.
- Need explicit post-search action rules for multi-facet topics: always follow up with `web_expand` if alive count is modest (<15) **and** key facets are not represented.
- Need auto-image call for artist/visual topics: without `image_search`, final answer lacks visual material, breaking skill expectation.
- Must not rely on `web_extract` if it returns empty; failover must be immediate, not after several retries.
- `web_expand` работает, но его кандидаты не попадают в evidence pool — нужен helper `expand_and_fetch`.
- `image_search` работает, но нет шага “добавить image URLs в markdown” — нужна автоматизация.
