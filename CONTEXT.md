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
    ddg_search_tool.py        <- wrapper: 3x registry.register()
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
- Wrapper loads additional backend `compose.py` and registers it as `sys.modules["compose"]` so backend imports succeed.
- `visit_website` in wrapper binds to `visit_website_enhanced.visit_website`.
- `image_search` in wrapper binds to `ddg_search.image_search`.
- `web_search_deep` in wrapper binds to `ddg_search.search_deep`.
- All three tools must have `check_fn=True` after restore; treat `False` as breakage.
- Classifier `_classify_by_content` is keyword-based (`_CATEGORY_KEYWORDS`) only; `_AUTHORITATIVE_DOMAINS` must stay removed.
- Wrapper must use `spec_from_file_location` absolute path for modules; otherwise `ModuleNotFoundError: No module named 'plugins.web_tools'`.

## Registry/tool visibility (expected)
- `web` toolset: `['image_search', 'visit_website_tool', 'web_extract', 'web_search', 'web_search_deep']`
- Expected binding:
  - `web_search_deep` → wrapper handler `compose_markdown` / `_safe_search_deep`
  - `visit_website_tool` → wrapper handler `visit_website_enhanced`
  - `image_search` → wrapper handler `image_search`

## Quick diagnostics
```powershell
# Check registry and tool health from Hermes python
python -c "import sys; sys.path.insert(0, r'C:\Users\sucot\.hermes'); from tools.registry import registry; names=sorted([n for n,e in registry._tools.items() if e.toolset=='web']); print('web tools:', names); bad=[n for n in names if not registry.get_entry(n).check_fn()]; print('bad check_fn:', bad)"

# Verify wrapper loads cleanly
python -c "import sys, importlib.util, pathlib; p=pathlib.Path(r'C:\Users\sucot\.hermes\hermes-agent\tools\ddg_search_tool.py'); spec=importlib.util.spec_from_file_location('ddg_search_tool', p); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); print('loaded:', mod.__name__)"

# Compile backend modules
python -m py_compile C:\Users\sucot\.hermes\plugins\web-tools\ddg\ddg_search.py
python -m py_compile C:\Users\sucot\.hermes\plugins\web-tools\ddg\visit_website_enhanced.py
python -m py_compile C:\Users\sucot\.hermes\hermes-agent\tools\ddg_search_tool.py

# Smoke test full compose markdown pipeline
python -c "import sys, importlib.util; sys.path.insert(0, r'C:\Users\sucot\.hermes'); reg = importlib.util.spec_from_file_location('tools.registry', r'C:\Users\sucot\.hermes\hermes-agent\tools\registry.py'); reg_mod=importlib.util.module_from_spec(reg); reg.loader.exec_module(reg_mod); sys.modules['tools.registry']=reg_mod; wrap = importlib.util.spec_from_file_location('ddg_search_tool.smoke', r'C:\Users\sucot\.hermes\hermes-agent\tools\ddg_search_tool.py'); wrap_mod=importlib.util.module_from_spec(wrap); wrap.loader.exec_module(wrap_mod); out=reg_mod.registry.get_entry('web_search_deep').handler({'query':'llama.cpp GGUF', 'validate':True, 'classify':True, 'max_validate':10, 'compose':True}); print(type(out).__name__); print(str(out)[:160])"
```

## Known blockers and workarounds
1. `compose` module missing before wrapper load → resolved by wrapper-side conditional load of `compose.py`; backend continues to fall back if composition unavailable.
2. Legacy backend path passed `compose=True` through to backend, causing import failure → wrapper now forces `compose=False` for backend call and performs markdown composition itself.
3. No fallback to native `web_search` on degraded/empty DDG results → wrapper-side fallback implemented as `_safe_native_fallback`.
4. `browser_dialog_tool.py` is a stub; do not rely on it.
5. `image_search` requires DDG image/Jina pipeline; if degraded, explicit `image_search` tool remains available.

## Gotchas and pitfalls
- Hermes tool registration depends on AST-visible `registry.register(...)` at top-level of wrapper module. Do not move calls inside conditionals or functions.
- `visit_website_tool` name is hard-coded in wrapper register; changing the name requires matching agent prompts and skill references.
- `ddg_search.py` was patched in-place; if Hermes update overwrites it, classifier may regress to domain-weighted mode. Verify after every DDG backend update.
- If backend code later introduces its own top-level `compose` import, wrapper-side preload ensures it doesn’t fail immediately. Remove the conditional preload once backend no longer imports compose.
- `httpx` proxy workaround is version-sensitive; upgrades may change exception types.
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
6. Smoketest `web_search_deep` with `compose=True` via `registry.get_entry(...).handler(...)`.

## Risks and countermeasures
- Loss of custom tools after Hermes update → mitigate: `restore.ps1`, `skills/restore-context/SKILL.md`, this `CONTEXT.md`.
- Breakage from AST-scan registration requirements → countermeasure: keep `registry.register(...)` at module top-level in wrappers.
- Breakage from proxy/httpx changes → keep wrapper path resolution stable; avoid changing backend module names/imports.
- Breakage from schema drift → regenerate `check_fn` after any signature change in backend functions.

## Open plans
1. Refine hot-path branch in `web_search_deep`: replace broad `from tools.web_search import web_search` fallback with explicit fallback contract that matches wrapper schema.
2. Integrate `image_search` into `web_search_deep` output for structured research reports.
3. Replace `browser_dialog_tool.py` stub with full implementation or disable its discovery.
4. Test end-to-end deep-research flow and refine category mapping based on real results.

## Decision log
- Classifier fix: domain bonuses removed because authoritative-list maintenance is unbounded and breaks deep-research generality. Kept only keyword-baseline to avoid query-dependent regressions.
- Wrapper design: single-file binding chosen over multi-file plugin to minimize breakage surface during Hermes updates. Direct `spec_from_file_location` chosen because relative/package-style imports fail after Hermes update reorders module paths.
- `compose` handling: moved from backend-side import to wrapper-owned composition, with conditional fallback to existing `compose.py` when available. This preserves old backend behaviour without requiring `compose` at module-import time.
