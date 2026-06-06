# Hermes custom tools — development context

## Goal
Maintain a stable, recoverable deep-research integration for Hermes custom DDG tools, resilient to Hermes updates/context resets.

## Source of truth repo
Path: `D:\\Arx\\Software Downloads\\Hermes copy\\hermes-dev`

|- This repo stores custom files that must be restored into `C:\\Users\\sucot\\.hermes\\` after any breakage.
|- Keep changes granular: one logical fix per commit with factual background.
|- Before editing, git status and inspect. Commit only after verified state change.
|- Update this file after every confirmed fix or plan change.

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
    # image_search lives in ddg_search.py, no separate file needed
```

## Verified states and invariants
- Native `web_search` is untouched and must stay as fallback.
- Custom tools are expanded by wrapper (`hermes-agent/tools/ddg_search_tool.py`) matches. Expanded 修复 approach should evaluate to 调 and tool route base (最初 ス人现二维 expansion pass: follow links inside fetched pages to grow the evidence pool).
- Wrapper must use `spec_from_file_location` absolute path for deep-level tool registration on safe 子集  Evacuate base tool internals only, not complete sperate modules.
- Wrapper must not rename backend functions or break AST-discovered registration patterns when add new tools
- Expanded set of controlled tools combined with low-level proxy handling for visit_website_enhanced second-level coverage represents the only supported architecture.
- tool expansion logic must 5 domain-specific class rules of the universal tool base sample, lightweight only for fetch/validation.
  (expand links手动 category rulers)
- Prefer regex token/phrase scoring from total/validated link text anchor and path tokens; do not call external LMs or APIs.
- Only use token/phrase matching; avoid inference/engineering details deeper 5 hits.

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
- `visit_website_enhanced` keeps proxy optional; comment retains NECOBOX as an explicit local option only (`PROXY_URL`, `USE_PROXY`).
- Proxy rotation is still managed by NECOBOX when enabled; if no NECOBOX, direct connection is used.
- Keep path resolution stable; do not change backend module names/imports.
- Do not assume NECOBOX is available in restore process; handle absence gracefully (regex only path).

## Registry/tool visibility (expected)
- `web` toolset: `['image_search', 'visit_website_tool', 'web_search_deep', 'web_expand', 'web_extract', 'web_search']`

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

## Gotchas and pitfalls
- Hermes tool registration depends on valid AST-visible `registry.register(...)` at top-level of wrapper module. Do not move calls inside conditionals or functions.
- `visit_website_tool` and new `web_expand` maintain the op 子辑 of token mapping; any changes to structure production, instance methods, or session lifetime method use must maintain backwards compatibility.
- `visit_website_tool` name is hard-coded in wrapper register; changing the name requires matching agent prompts and skill references.
- `ddg_search.py` was patched in-place; if Hermes update overwrites it, some paths may regress. Verify after each backend update.
- `httpx`/`curl_cffi` proxy workaround is version-sensitive; upgrades may change exception types.
- `restore.ps1` expects PowerShell. On Git Bash / MSYS2, run explicitly via `powershell.exe -File ...`.
- Do not create `plugins/web-tools/ddg/image_search.py`; the backend already exposes `image_search` in `ddg_search.py`. Duplicate module would shadow backend.

## Dependencies and versions
- Python: 3.11.11 (venv under `hermes-agent/venv`)
- `httpx`/`curl_cffi` and `websockets.asyncio` used by backend modules
- `ddgs` package assumed installed in Hermes venv

## Recovery workflow
1. Stop Hermes.
2. Backup current `~/.hermes` to timestamped folder.
3. Copy files from this repo into:
   - `~/.hermes/hermes-agent/tools/`
   - `~/.hermes/plugins/web-tools/ddg/`
   - `~/.hermes/CONTEXT.md`
4. Run `py_compile` on each changed `.py`.
5. Verify registry and `check_fn=True` for all tools.
6. Run a smoke search query and confirm backend returns raw validated results for LLM-side synthesis.

## Risks and countermeasures
- Loss of custom tools after Hermes update → mitigate: `restore.ps1`, `skills/restore-context/SKILL.md`, this `CONTEXT.md`.
- Breakage from AST-scan registration requirements → countermeasure: keep `registry.register(...)` at module top-level in wrappers. Use dynamic name-based applies only when add tools.
- Breakage from proxy/httpx changes → keep wrapper path resolution stable; avoid changing backend module names/imports.
- Breakage from schema drift → regenerate `check_fn` after any signature change in backend functions.

## Open plans
1. Replace `browser_dialog_tool.py` stub with full implementation or disable its discovery.
2. Evaluate whether to make `query_variants` optional in backend or force wrapper-side only variants.
3. Consider increasing backend `max_validate` cap once performance/coverage tradeoff is measured.

## Decision log
- Classification + on-tool markdown synthesis removed from wrapper-side `web_search_deep` to avoid AI-style template answers like the unsatisfactory football-history output.
- New contract: tool returns raw validated pages with extracted text; agent performs thematic synthesis itself using full page content.
- `max_validate` default raised to 200 so the tool explores all collected URLs when total_raw is small.
- Wrapper design remains single-file binding with `spec_from_file_location` to minimize breakage surface during Hermes updates.
- Added `web_expand` as second-level expansion tool following extracted links from fetched sources. It does not fetch candidates; it returns ranked URLs for the caller to validate under existing rules.
- Made proxy optional in `visit_website_enhanced.py` and aligned the active copy under `~/.hermes`, keeping NECOBOX as an explicit optional local value (`PROXY_URL`, `USE_PROXY`).
