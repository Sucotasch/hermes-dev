# Hermes custom tools — development context

## Goal
Maintain a stable, recoverable deep-research integration for Hermes custom DDG tools, resilient to Hermes updates/context resets.

## Source of truth repo
Path: `D:\\Arx\\Software Downloads\\Hermes copy\\hermes-dev`

- This repo stores custom files that must be restored into `C:\\Users\\sucot\\.hermes\\` after any breakage.
- Keep changes granular: one logical fix per commit with factual background.
- Before editing, git status and inspect. Commit only after verified state change.
- Update this file after every confirmed fix or plan change.

## Backed-up files (paths inside repo)
- `hermes-agent/tools/ddg_search_tool.py`
- `hermes-agent/tools/browser_dialog_tool.py`
- `plugins/web-tools/ddg/ddg_search.py`
- `plugins/web-tools/ddg/visit_website_enhanced.py`
- `plugins/web-tools/ddg/image_search.py` (not required — `image_search` is served from `ddg_search.py`; keep filename here only if recreated later)

## Verified states and invariants
- Native `web_search` is untouched and must stay as fallback.
- Custom tools are registered by wrapper (`hermes-agent/tools/ddg_search_tool.py`) via AST-discovered `registry.register(...)` calls at module top-level.
- `visit_website` in wrapper binds to `visit_website_enhanced.visit_website`.
- `image_search` in wrapper binds to `ddg_search.image_search` directly.
- `web_search_deep` in wrapper binds to `ddg_search.search_deep`.
- All three tools must have `check_fn=True` after restore; treat `False` as breakage.
- Classifier `_classify_by_content` is keyword-based (`_CATEGORY_KEYWORDS`) only; keep `_AUTHORITATIVE_DOMAINS` removed.

## Registry/tool visibility (expected)
- `web` toolset: `['image_search', 'visit_website_tool', 'web_extract', 'web_search', 'web_search_deep']`

## Known blockers and workarounds
1. `compose` module missing → causes exception when `compose=True` is passed
   - Workaround: wrapper exposes synthesis function directly; avoid passing `compose=True` until wrapper synthesis is available or module is provided.
2. `image_search` not yet integrated into deep-research markdown output
   - Workaround: call `image_search` explicitly from wrapper/pipeline when images are required.
3. No fallback to native `web_search` on degraded/empty DDG results
   - Workaround: monitor empty payload in wrapper and route to `web_search` manually.
4. `browser_dialog_tool.py` is a stub; do not rely on it for browser automation.
5. `_probe_gallery_urls` may be slow/unreliable; prefer trusted `image_results` when available.

## Dependencies and versions
- Python: 3.11.11 (venv under `hermes-agent/venv`)
- `httpx`/`curl_cffi` and `websockets.asyncio` are used by backend modules
- `ddgs` package is assumed installed in Hermes venv
- `compose` module is missing — either install/restore it or remove `compose=True` from schemas

## Recovery and restore workflow
1. Stop Hermes.
2. Backup current `~/.hermes` to timestamped folder.
3. Copy files from this repo into:
   - `~/.hermes/hermes-agent/tools/`
   - `~/.hermes/plugins/web-tools/ddg/`
4. Run `python -m py_compile` on each changed `.py`.
5. Restore `~/.hermes/CONTEXT.md` and `SKILL.md` if needed.
6. Verify registry and `check_fn=True` for all three tools.
7. See `restore.ps1` for scripted version.

## Risks and countermeasures
- Loss of custom tools after Hermes update → mitigations: `restore.ps1`, `skills/restore-context/SKILL.md`, this `CONTEXT.md`.
- Breakage from AST-scan registration requirements → countermeasure: keep `registry.register(...)` at module top-level in wrappers.
- Breakage from proxy/httpx changes in DDG backend → keep wrapper path resolution stable; avoid changing backend module names/imports.
- Breakage from schema drift → regenerate `check_fn` after any signature change in backend functions.

## Open plans
1. Implement wrapper-side markdown synthesis for `compose` behavior, or restore `compose` module.
2. Integrate `image_search` into `web_search_deep` output for structured research reports.
3. Add automatic fallback to native `web_search` when DDG returns empty/degraded results.
4. Replace `browser_dialog_tool.py` stub with full implementation or disable its discovery.
5. Test end-to-end deep-research flow and refine category mapping based on real results.
