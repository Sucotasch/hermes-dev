# Hermes custom tools — development context

## Goal
Maintain a stable, recoverable deep-research integration for Hermes custom DDG tools, resilient to Hermes updates/context resets.

## Source of truth repo
Path: `D:\\Arx\\Software Downloads\\Hermes copy\\hermes-dev`

- This repo stores custom files that must be restored into `C:\\Users\\sucot\\.hermes\\` after any breakage.
- Keep changes granular: one logical fix per commit with factual background.
- Before editing, git status and inspect. Commit only after verified state change.
- Update this file after every confirmed fix or plan change.

## File map
```
repo:
  CONTEXT.md                  <- this file
  RESTORE.md                  <- cheat sheet for manual restore
  restore.ps1                 <- scripted restore (windows powershell)
  skills/restore-context/SKILL.md  <- agent-facing restore skill
  hermes-agent/tools/
    ddg_search_tool.py        <- wrapper: minimal binding + native fallback
    browser_dialog_tool.py    <- stub, not fully implemented
  plugins/web-tools/ddg/
    ddg_search.py             <- backend (fixed classifier, httpx proxy, compose removed from schema)
    visit_website_enhanced.py <- enhanced fetcher (curl_cffi + websockets.asyncio)
    compose.py                <- restored/compatible formatter (loaded conditionally)
    # image_search lives in ddg_search.py, no separate file needed
```

## Verified states and invariants
- Native `web_search` is untouched and must stay as fallback.
- Custom tools are registered by wrapper (`hermes-agent/tools/ddg_search_tool.py`) via AST-discovered `registry.register(...)` calls at module top-level.
- `web_search_deep` handler now returns raw backend JSON only; markdown synthesis and categorization are deferred to the LLM.
- `visit_website_tool` in wrapper binds to `visit_website_enhanced.visit_website`.
- `image_search` in wrapper binds to `ddg_search.image_search`.
- All three tools must have `check_fn=True` after restore; treat `False` as breakage.
- Wrapper must use `spec_from_file_location` absolute path for modules; otherwise `ModuleNotFoundError: No module named 'plugins.web_tools'`.

## Registry/tool visibility (expected)
- `web` toolset: `['image_search', 'visit_website_tool', 'web_extract', 'web_search', 'web_search_deep']`

## Quick diagnostics
```powershell
# Check registry and tool health from Hermes python
python -c "import sys; sys.path.insert(0, r'C:\Users\sucot\.hermes'); from tools.registry import registry; names=sorted([n for n,e in registry._tools.items() if e.toolset=='web']); print('web tools:', names); bad=[n for n in names if not registry.get_entry(n).check_fn()]; print('bad check_fn:', bad)"
```

## Known blockers and workarounds
1. `query_variants` module missing in many restored states → backend prints a warning and continues with collected results.
2. `browser_dialog_tool.py` is a stub; do not rely on it.
3. `image_search` requires DDG image/Jina pipeline; if degraded, explicit `image_search` tool remains available.

## Gotchas and pitfalls
- Hermes tool registration depends on AST-visible `registry.register(...)` at top-level of wrapper module. Do not move calls inside conditionals or functions.
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
5. Verify registry and `check_fn=True` for all three tools (see quick diagnostics).
6. Run a smoke search query and confirm backend returns raw validated results for LLM-side synthesis.

## Risks and countermeasures
- Loss of custom tools after Hermes update → mitigate: `restore.ps1`, `skills/restore-context/SKILL.md`, this `CONTEXT.md`.
- Breakage from AST-scan registration requirements → countermeasure: keep `registry.register(...)` at module top-level in wrappers.
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