# Hermes custom tools — development context

## Goal
Maintain a stable, recoverable deep-research integration for Hermes custom DDG tools, resilient to Hermes updates/context resets. Intent policy is now LLM-driven via `query_type`; code is monotone and does not branch on topic/visual/coverage keywords.

## Source of truth repo
Path: this repo (wherever cloned to). Restored via `restore.ps1` which auto-detects its own location.

| Rule | Rationale |
|---|---|
| This repo stores custom files that must be restored into `~/.hermes/` after any breakage. | Hermes updates/context resets regularly destroy progress. |
| Keep changes granular: one logical fix per commit with factual background. | Easier revert/audit. |
| Before editing, git status and inspect. Commit only after verified state change. | Prevents phantom changes. |
| Update this file after every confirmed fix or plan change. | CONTEXT.md is the durable memory. |

## File map
```
repo:
  CONTEXT.md                       <- this file
  AGENTS.md                        <- agent-facing quick reference
  README.md                        <- developer documentation
  RESTORE.md                       <- cheat sheet for manual restore
  restore.ps1                      <- scripted restore (powershell)
  skills/restore-context/SKILL.md  <- agent-facing restore skill
  skills/web-deep-search/SKILL.md  <- deep research skill (cleaned, 706 lines)
  hermes-agent/tools/
    ddg_search_tool.py             <- wrapper: query_type schemas, tool registration
  plugins/web-tools/ddg/
    ddg_search.py                  <- backend: search, validation, blocklist, images, overlay
    visit_website_enhanced.py      <- fetcher: curl_cffi, httpx, Jina, overlay stripping
    query_variants.py              <- intent-aware variant generator
    compose.py                     <- markdown formatter (compose mode)
    test_query_variants.py         <- unit tests for query_variants
  hermes-agent/
    test_coverage_gate.py          <- unit tests for coverage gate
```

## Verified states and invariants
- Native `web_search` is untouched and must stay as fallback.
- Custom tools are expanded by wrapper (`hermes-agent/tools/ddg_search_tool.py`).
- Wrapper must use `spec_from_file_location` absolute path for deep-level tool registration.
- `query_type` is explicitly typed in both `_schema_search_deep()` and `_schema_deep_research()`, enum: `visual/technical/news/historical/comparison/general`.
- Backend `search_deep` accepts `query_type` but remains monotone: no topic branching, no coverage overrides, no image gating code-side.
- Tags produced by backend: `bot_challenge`. Backend does not drop pages; synthesis side excludes them.
- `_is_visual_topic()` has been removed from wrapper. Image search routing uses `query_type == "visual"` only.
- `final_limit` in `_apply_post_retrieval_filter` is 80 (was 40).
- `IMPERSONATE_POOL = ["chrome110", "chrome116", "chrome120", "chrome124"]` — rotates TLS fingerprint per session.

## Included tool contracts
- `web_search_deep` → raw validated pages, classify=False, no compose. Accepts `query_type` (optional).
- `web_expand_and_fetch` → second-level expansion + fetch: accepts `query` + `source_urls`, calls `visit_website_enhanced` across each candidate, returns fetched Level-2 pages for synthesis.
- `web_deep_research` → composite tool: multi-query Level 1 → auto Level 2 if coverage insufficient → image search only when `query_type == "visual"` → unified evidence pack.
- `visit_website_tool` → unchanged enhanced fetcher contract.
- `image_search` → unchanged ddg backend contract; enabled by wrapper only for `visual`, not auto by page content keywords.

## Intent policy (2026-06-11, verified 2026-06-12)
- Agent classifies intent before tool call and passes `query_type` explicitly.
- All topic/coverage/image keywords are removed from code; any future override must be LLM-side via `query_type`.
- Without explicit `query_type`, wrapper uses `general` and keeps default behavior.
- `_is_visual_topic()` was removed 2026-06-12. `query_type == "visual"` is the sole routing mechanism.

## Bugs found and fixed (2026-06-12 session)

### Critical
1. **httpx `proxies=` API broken** — httpx 0.28.1 removed `proxies` keyword, requires `proxy=`. Fixed `_fetch_httpx` in both ddg_search.py and visit_website_enhanced.py.
2. **`query_type` not wired through pipeline** — schemas, handlers, `_safe_deep_research` didn't accept/pass `query_type`. Added to both schemas, both handler lambdas, both wrapper functions.
3. **`_is_visual_topic()` still doing keyword detection** — removed. Replaced with `query_type == "visual"` in `_safe_deep_research`.
4. **`_extract_image_urls` continue bug** — line 1036 had bare `continue` after root-relative URL construction, skipping `urls.append()`. Fixed.
5. **`_get_block_type` cascading `if`** — changed to `elif` chain. Previously last-match won incorrectly.
6. **`image_search` vqd dead code** — removed unused DDG vqd token fetch (saved 1 round-trip per image search).
7. **UA not rotated on retry** — `_fetch` loop reused same UA across 3 attempts. Added `if attempt > 0: rotate UA`.
8. **`_fetch_httpx` no proxy in visit_website_enhanced** — added `proxy=` forwarding.
9. **NEXT_DATA extraction threshold** — `len(raw) < 10` rejected short JSON. Changed to `< 5`.

### Anti-bot improvements
10. **Impersonation rotation** — `IMPERSONATE_POOL` with 4 Chrome versions. Session cache key includes version.
11. **DNS circuit breaker** — `web_search` breaks on `getaddrinfo` error instead of trying all strategies. `_fetch` breaks on DNS failure.
12. **Proxy retry for blocked URLs** — `_check_url_live` retries with proxy when status is 403/429/451/503.
13. **JS-block detection** — added `"javascript is disabled"`, `"enable javascript and then reload"`, `"requires javascript"` to block indicators.
14. **Content relevance scoring** — `content_relevance_score(query, text)` — word-overlap with penalties for short text and bonus for multi-hit.
15. **Domain blocklist** — 80+ domains: analytics, ads, search engines, aggregators, Russian portals, generic platforms.
16. **Image URL upgrade** — `upgrade_to_fullsize(url)` — regex for suffix removal, flickr tokens, CDN subdomains, query params.
17. **Full-size image extraction** — `extract_fullsize_images(html, base_url)` — og:image, srcset, gallery, data-original, JSON-LD, <img> fallback.
18. **JS data extraction** — `extract_js_data(html)` — __NEXT_DATA__, JSON-LD, window.__DATA__.
19. **Overlay bypass expanded** — ID-based, text-based, button removal (Accept all, I agree, Got it).
20. **Tracking pixel filter** — skips images with pixel/track/1x1/spacer in URL or width/height < 50.

## Skill cleanup (2026-06-12)
- `skills/web-deep-search/SKILL.md` copied to dev repo from `~/.hermes`
- Cleaned: 776 → 706 lines (−70 lines of duplication)
- Removed 7 duplicated sections: compose-forwarding, runtime normalization, coverage gates, fetcher contract, chat delivery items
- Fixed ghost references: `_normalize_search_deep_args`, `_effective_query_type` → actual handler forwarding
- Fixed `web_expand` references → `web_expand_and_fetch` (tool not registered)
- Fixed `final_limit=40` → `final_limit=80` in skill
- Fixed Chat Delivery Rule numbering (1-4, 2-6 → 1-8)
- Added back Image integration note

## Registry/tool visibility (expected)
- `web` toolset: `['image_search', 'visit_website_tool', 'web_expand_and_fetch', 'web_extract', 'web_search', 'web_search_deep', 'web_deep_research']`
- By default `USE_PROXY=False` to avoid depending on local tunneller.
- Proxy rotation is still managed by NECOBOX when enabled; if no NECOBOX, direct connection is used.
- `curl_cffi` session caches one session per proxy+impersonation setting. If proxy env changes at runtime, restart Hermes.

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
6. 40-46% of URLs are blocked (HTTP 403/429) — proxy retry helps 5-10% of them. Further improvement requires headless browser or API access.
7. IMDB, AllMovie, Wikipedia blocked — JS-block or Cloudflare. JS-block detection now flags these correctly.

## Gotchas and pitfalls
- Hermes tool registration depends on valid AST-visible `registry.register(...)` at top-level of wrapper module. Do not move calls inside conditionals or functions.
- `visit_website_tool` name is hard-coded in wrapper register; changing the name requires matching agent prompts and skill references.
- `ddg_search.py` was patched in-place; if Hermes update overwrites it, some paths may regress. Verify after each backend update.
- `httpx`/`curl_cffi` proxy workaround is version-sensitive; upgrades may change exception types.
- `restore.ps1` expects PowerShell. On Git Bash / MSYS2, run explicitly via `powershell.exe -File ...`.
- `content_relevance_score` uses person/entity disambiguation: for person queries (all short words), entity phrase must appear as substring. Topic queries use flexible word-overlap. Handles "Sara James" vs "Sara St James" correctly (namesake → 0.0).

## Dependencies and versions
- Python: 3.11.11 (venv under `hermes-agent/venv`)
- `httpx` 0.28.1 (requires `proxy=` not `proxies=`)
- `curl_cffi` 0.14.0
- `ddgs` 9.14.4
- `bs4` (beautifulsoup4) 4.13.4
- `lxml` 6.0.2

## Pre-flight checklist before each deep-research run
- [ ] Confirm wrapper: `registry.get_tool_names_for_toolset('web')` includes `web_search_deep`, `web_expand_and_fetch`, `visit_website_tool`, `image_search`, `web_extract`, `web_search`.
- [ ] Confirm `check_fn=True` for all custom tools.
- [ ] Determine `query_type` before tool call and pass it explicitly.
- [ ] Use multi-query (4–6 variants) unless the query is unambiguous and tight.
- [ ] Default `max_validate` to 200.
- [ ] Preferred fetcher: `visit_website_tool`. `web_extract` is best-effort only.
- [ ] After Level 1: if `relevant_alive < 15` → run Level 2. Check relevance, not just alive count.

## Adaptive two-level rule (must follow automatically)
After Level 1 (`web_search_deep`):
1. If `relevant_alive < 15` → run **Level 2** `web_expand_and_fetch` on top alive URLs.
2. If `query_type == "visual"` → call **`image_search`** before writing the answer.

## Fetcher fallback policy (must follow)
- Call `visit_website_tool` first.
- If returned text < 500 chars, or page contains challenge markers (`Checking your browser`, `captcha`, `cloudflare`, `Access is denied`, `JavaScript is disabled`) → retry same URL with proxy enabled if available.
- Only after exhaustive retries, consider `web_extract` as best-effort fallback.

## Anti-bot architecture (2026-06-12)

### curl_cffi impersonation
- `IMPERSONATE_POOL = ["chrome110", "chrome116", "chrome120", "chrome124"]`
- Sessions keyed by `(PROXY_URL, impersonation_version)` — separate sessions per fingerprint
- UA rotation: random from 18-entry pool, re-selected on retry

### Block detection
- `_detect_blocked(html)` — 19 indicators including JS-block: `"javascript is disabled"`, `"enable javascript and then reload"`, `"requires javascript"`
- `_tag_bot_challenge(items)` — regex-based soft detection on search result snippets
- `_strip_block_overlay(html)` — bs4-based: ID patterns, class patterns, text patterns, button removal

### Domain blocklist
- 80+ domains in `BLOCKED_DOMAINS`: analytics, ads, search engines, aggregators, Russian portals, generic platforms
- `VISUAL_ALLOWLIST`: pinterest, deviantart, artstation, flickr, tumblr, imgur, 500px, unsplash, pixabay — not blocked for visual queries
- `is_blocked_domain(url, query_type)` — checks blocklist, overrides for visual allowlist

### Proxy retry
- `_check_url_live`: when HEAD returns 403/429/451/503, retries with proxy session if `USE_PROXY=True`
- When GET body is blocked, retries with proxy before marking as blocked

### Content relevance scoring
- `content_relevance_score(query, text)` — 0.0-1.0
- Word-overlap: query words in text (title=3x weight, body=1x)
- Penalty for short text (<200 chars): 0.3x multiplier
- Bonus for multi-hit words (appear 3+ times): +0.2

### Full-size image extraction
- `extract_fullsize_images(html, base_url)` — 6 sources + <img> fallback
- `upgrade_to_fullsize(url)` — regex for thumbnail→full conversion
- Sources: og:image, gallery <a><img>, srcset (max), data-original, JSON-LD, <figure><a>

### JS data extraction
- `extract_js_data(html)` — __NEXT_DATA__, window.__DATA__, JSON-LD, data-react-props

## Empirical run data (2026-06-12, Sara St James deep research)

### v2 results (before relevance scoring)
- Raw URLs: 263, Alive: 111 (42%), Blocked: 121 (46%)
- Fetched pages: 9 (all junk — realty.yandex.ru, start.ru, steampowered.com)
- Problem: sorted by text_length, not relevance

### v3 results (with all fixes)
- Raw URLs: 269, Alive: 114 (42%), Blocked: 115 (43%)
- Relevant alive (score ≥ 0.2): 62
- Fetched pages: 8 (all relevant — grokipedia, babepedia, vintage-erotica-forum)
- Images: 26 unique
- Time: 171s
- Key finding: relevance scoring transformed junk results into targeted content

### Blocked sites observed
- IMDB: JS-block ("JavaScript is disabled")
- AllMovie: JS-block
- Wikipedia: 403 Cloudflare
- Reddit, Twitter, Pinterest, Instagram: 403
- FreeOnes, LPW Wiki: 403 adult site protection
- angelfire.com: DNS failure

### Alive/relevant sources found
- grokipedia.com/Jacqueline_Lovell (1.00) — full biography
- moviereelmania.blogspot.com (0.85) — career timeline
- babepedia.com/babe/Sara_St._James (0.88) — 19 nude pics
- vintage-erotica-forum.com/t6325 (0.82) — forum thread
- bluefavorite.blogspot.com (0.80) — DOB, spouse, aliases
- whosdatedwho.com (0.72) — marriage, children

## Decision log
- Classification + on-tool markdown synthesis removed from wrapper-side `web_search_deep` to avoid AI-style template answers.
- New contract: tool returns raw validated pages with extracted text; agent performs thematic synthesis itself using full page content.
- `max_validate` default raised to 200 so the tool explores all collected URLs when total_raw is small.
- Wrapper design remains single-file binding with `spec_from_file_location` to minimize breakage surface during Hermes updates.
- Added `web_expand_and_fetch` as second-level expansion tool following extracted links from fetched sources.
- Made proxy optional in `visit_website_enhanced.py` and aligned the active copy under `~/.hermes`, keeping NECOBOX as an explicit optional local value (`PROXY_URL`, `USE_PROXY`).
- Introduced `query_type` as the single source of intent policy, defined by the LLM before tool call; backend is policy-free.
- Bot-challenge detection retained as metadata-only tagger (`bot_challenge`); synthesis side excludes these pages from final answer.
- **2026-06-12:** Removed `_is_visual_topic()`, replaced with `query_type == "visual"`. Added impersonation rotation, DNS circuit breaker, proxy retry, content relevance scoring, domain blocklist, full-size image extraction, JS data extraction, overlay bypass expansion.

## Session 2026-07-01: Standalone pipeline fixes

### Changes made to ddg_search.py
1. **netporntube.com added to BLOCKED_DOMAINS** — adult aggregator with no useful content
2. **DNS circuit breaker removed** — `break` on getaddrinfo error was too aggressive, skipping valid strategies
3. **Retry logic added to web_search** — 2 attempts per strategy with 2s delay between retries
4. **`_detect_blocked` fix** — "captcha" no longer matches JS config variables (e.g., `wgconfirmeditcaptchaneededforgenericedit` on Wikipedia). Only matches visible captcha forms.
5. **Proxy retry for dead sites** — when site is dead (not blocked) with DNS/timeout errors, try NECOBOX proxy at 127.0.0.1:2080 as last resort

### Changes made to orchestrator.py
1. **25-page limit removed** — `validated[:25]` → `validated` (process all alive pages)
2. **Step 9 content filter** — require `len(text) >= 100` to prevent empty pages in evidence
3. **Sort by text_length** — `validated.sort(key=lambda x: len(x.get("text", "")), reverse=True)` ensures content-rich pages processed first (prevents domain dedup killing better versions like ru.kinorium.com vs en.kinorium.com)
4. **LLM prompt fixed** — now includes actual source content (1500 chars each) instead of just titles/scores. LLM synthesizes facts from sources.

### Key findings
- **DDG engine classes intermittent failure** — Duckduckgo/Yahoo/Yandex/Mojeek sometimes return 0 results due to DNS. DDGS().text() more reliable. Retry helps.
- **`_detect_blocked` false positive** — Wikipedia HTML contains "captcha" in JS config (`wgconfirmeditcaptchaneededforgenericedit`), not in visible content. Fixed by checking for visible captcha forms only.
- **Domain dedup kills better pages** — `ru.kinorium.com` (39K chars) blocked by `en.kinorium.com` (3K chars) due to base domain normalization. Fixed by sorting by text_length before deep-read.
- **LLM synthesis missing source content** — old prompt sent only titles/scores. Fixed to include 1500 chars of actual content per source.
- **NECOBOX proxy available** — `visit_website_enhanced.py` has `USE_PROXY=False`, `PROXY_URL="http://127.0.0.1:2080"`. Added proxy retry for DNS/timeout dead sites only (not JS/captcha).
- **Step 9 evidence filter** — `p.get("deep_text") or p.get("text", "")` allowed empty pages. Fixed: require `len(text) >= 100`.

### DDG search behavior
- DDG results are non-deterministic — same query returns different URLs between runs
- babepedia appears sometimes but not consistently for "Jacqueline Lovell" queries
- Enriched query with "Sara St James" increases chance of finding babepedia
- Engine classes (Duckduckgo, Yahoo, Yandex, Mojeek) intermittently fail with 0 results
- DDGS().text() is more reliable but returns fewer results

### Standalone app files
```
standalone/
  orchestrator.py     <- pipeline: classify → search → validate → L2 → deep-read → synthesize
  deep_research.py    <- CLI entry point
  llm_client.py       <- llama.cpp HTTP client (OpenAI-compatible API)
  README.md
```

### Test reports location
`reports/` directory contains historical test runs
