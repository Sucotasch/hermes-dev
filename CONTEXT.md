# Hermes custom tools — development context

## Goal
Maintain a stable, recoverable deep-research integration for Hermes custom DDG tools, resilient to Hermes updates/context resets.

## Source of truth repo
Path: `D:\Arx\Software Downloads\Hermes copy\hermes-dev`

- This repo stores custom files that must be restored into `C:\Users\sucot\.hermes\` after any breakage.
- Keep changes granular: one logical fix per commit with factual background.
- Before editing, git status and inspect. Commit only after verified state change.

## Backed-up files (paths inside repo)
- `hermes-agent/tools/ddg_search_tool.py`
- `hermes-agent/tools/browser_dialog_tool.py`
- `plugins/web-tools/ddg/ddg_search.py`
- `plugins/web-tools/ddg/visit_website_enhanced.py`

Additional discovered artifacts:
- `plugins/web-tools/ddg/visit_website_enhanced.py` — antidetection fetcher; public entrypoint: `visit_website(url, max_chars, find_terms, max_links, max_images)`
- `plugins/web-tools/ddg/compose.py` — removed as separate compose path; compose now handled in wrapper build stage if needed

## Current toolchain state
- Native `web_search` is untouched and must stay as fallback.
- Custom tools registered by wrapper (`hermes-agent/tools/ddg_search_tool.py`) via AST-discovered `registry.register(...)` calls.
- `image_search` is now served from `plugins.web_tools.ddg.ddg_search.image_search` (no separate `plugins/web-tools/ddg/image_search.py`).
- Wrapper binding for visit tool: `visit_website` (not `visit_website_enhanced`) from `visit_website_enhanced.py`.
- `compose` parameter removed from tool schema; compose-like markdown synthesis now owned by wrapper-side `_build_markdown_answer` if needed.
- Classifier `_classify_by_content` is keyword-based (`_CATEGORY_KEYWORDS`); authoritative-domain bonuses block removed.

## Registry/tool visibility
- `web tools: ['image_search', 'visit_website_tool', 'web_extract', 'web_search', 'web_search_deep']`
- Expected `check_fn` and actual class/function sources after fix:
	- `web_search_deep`: handler → `ddg_search.search_deep`, source module: `plugins.web_tools.ddg.ddg_search`
	- `visit_website_tool`: handler → wrapper mapping to `visit_website_enhanced.visit_website`, source module: `plugins.web_tools.ddg.visit_website_enhanced`
	- `image_search`: handler → wrapper mapping to `ddg_search.image_search`, source module: `plugins.web_tools.ddg.ddg_search`

## Recent fixes / decisions
1. Restored `ddg_search.py` IndentationError and replaced `proxies` parameter with `proxy` for httpx 0.28.1.
2. Reinstalled `lxml` 6.1.1 to unblock `from lxml import etree`.
3. Reinstalled `ddgs` package to restore coverage backend used in `search_deep`.
4. Replaced `browser_dialog_tool.py` with a stub to eliminate import noise (`websockets.asyncio` failure) in discovery logs.
5. Rewrote wrapper so:
	- `image_search` comes from `ddg_search.py` (module present)
	- `visit_website_enhanced` bound to `visit_website`
	- removed `compose` parameter from schema to avoid a missing-compose-module failure path
6. Removed authoritative-domain bonus set from classifier; classifier now uses `_CATEGORY_KEYWORDS` only.

## Known issues / TODO
- `ddg_search.py` remains large; classifier block replacement was done by surgical removal — verify boundary conditions after any future full-rewrite. Prefer isolating classifier/relevance/image extraction into smaller modules later.
- `compose` functionality should be implemented explicitly as a wrapper-stage function to avoid depending on a missing module.
- Add fallback from custom tools to native `web_search` when DDG backend is unavailable.
- Consider adding restore automation and test hook (smoke test dispatch and `check_fn`) to detect breakage.

## Workflows
- Development: edit files in repo, verify with compile + local dispatch tests, `git add/commit`. Then run `restore.ps1` to apply changes to Hermes install.
- Recovery: run `restore.ps1` after Hermes update or context wipe.

## Restore procedure (from `D:\Arx\Software Downloads\Hermes copy\hermes-dev\restore.ps1`)
See `restore.ps1` in repo root.

## Backup copies
This repo is the canonical backup. Additional Hermes backups (if created) are logged by restore script in `~/.hermes/.restore-log/`.
