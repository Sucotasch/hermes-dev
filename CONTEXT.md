# Hermes custom tools — development context

## Goal
Maintain a stable, recoverable deep-research integration for Hermes custom DDG tools, resilient to Hermes updates/context resets.

## Source of truth repo
Path: `D:\Arx\Software Downloads\Hermes copy\hermes-dev`

- This repo stores custom files that must be restored into `C:\Users\sucot\.hermes\` after any breakage.
- Keep changes granular: one logical fix per commit with factual background.
- Before editing, git status and inspect. Commit only after verified state change.
- Update this file after every confirmed fix or plan change.

## Backed-up files (paths inside repo)
- `hermes-agent/tools/ddg_search_tool.py`
- `hermes-agent/tools/browser_dialog_tool.py`
- `plugins/web-tools/ddg/ddg_search.py`
- `plugins/web-tools/ddg/visit_website_enhanced.py`

## Current toolchain state
- Native `web_search` is untouched and must stay as fallback.
- Custom tools registered by wrapper (`hermes-agent/tools/ddg_search_tool.py`) via AST-discovered `registry.register(...)` calls.
- `image_search` is served from `plugins.web_tools.ddg.ddg_search.image_search` (no separate `plugins/web-tools/ddg/image_search.py`).
- Wrapper binding for visit tool: `visit_website` (not `visit_website_enhanced`) from `visit_website_enhanced.py`.
- `compose` parameter removed from tool schema; compose-like markdown synthesis now owned by wrapper-side `_build_markdown_answer` if needed.
- Classifier `_classify_by_content` is keyword-based (`_CATEGORY_KEYWORDS`); authoritative-domain bonuses block removed to avoid domain-list capture.

## Registry/tool visibility
- `web tools: ['image_search', 'visit_website_tool', 'web_extract', 'web_search', 'web_search_deep']`
- Expected `check_fn` and actual sources after fixes:
	- `web_search_deep`: handler → `ddg_search.search_deep`, source module: `plugins.web_tools.ddg.ddg_search`
	- `visit_website_tool`: handler → wrapper mapping to `visit_website_enhanced.visit_website`, source module: `plugins.web_tools.ddg.visit_website_enhanced`
	- `image_search`: handler → wrapper mapping to `ddg_search.image_search`, source module: `plugins.web_tools.ddg.ddg_search`

## Plans
1. Recover broader DDG backend reliability: validate `ddg_search.py` pipeline boundary and improve image extraction path.
2. Keep `compose` optional via wrapper function, not a missing separate module.
3. Add fallback from custom tools to native `web_search` when DDG backend returns empty/degraded results.
4. Continue recording recovery and verification instructions in this file after every confirmed change.
