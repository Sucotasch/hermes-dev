# Audit.md — Full Engineering Code Review

Date: 2026-08-10 · Scope: entire repo (working tree as-is, branch `fix/pipeline-quality`)
Reviewer: automated principal-engineer review per `ReviewPrompt.txt`

---

## 0. Executive summary

The repo contains two diverged deep-research pipelines sharing one backend:

- **Backend** `plugins/web-tools/ddg/` — search strategies, URL validation, anti-bot, image extraction.
- **Standalone** `standalone/` — the newest, most complete pipeline (CLI + PyQt5 GUI + LLM synthesis). Reference implementation.
- **Hermes wrapper** `hermes-agent/tools/ddg_search_tool.py` — lags behind standalone and contains bugs already fixed there (confirmed: last standalone commits 2026-07-05; wrapper shares none of the image-filtering/gallery fixes).

The code **compiles** (`py_compile` passes on all 11 modules) but has **2 failing tests** (`hermes-agent/test_coverage_gate.py`), several **inert mechanisms that look like features but never execute** (domain quarantine, dead-site proxy retry, visual allowlist, video-type guard), and a large amount of **copy-pasted code that has already drifted** between modules.

Priority ordering: correctness bugs (P0) → consistency/dead code (P1) → duplication/maintainability (P2) → tests (P3) → security/ops (P4).

---

## 1. How the pipeline actually works (verified against code, not docs)

```
Standalone (reference):
  run_deep_research(query)                       standalone/orchestrator.py
    1. classify_query_type → 11 intents (person/visual/technical/news/historical/
       comparison/fact/art/education/science/general)   standalone/llm_client.py
    1b. enrich_query (aliases) for person only
    2. _query_variants (own suffix table) × web_search() per variant
    3. blocklist + homepage + search-URL + video + service-path filter
    3b. GettyImages filter for person
    4. _validate_urls (10 threads, domain quarantine, relevance + img_bonus)
    5. rank by relevance → 6. Level-2 if alive < 20 → 7. deep-read (own
       _extract_main_content/_clean_content, Jina fallback) → 8. image dedup
    → 8b. _filter_images_for_report (two-phase download, Pillow) → 9. evidence
    → 10. inline LLM synthesis → 11. _build_report (sources + images + gallery
    links + synthesis)

Hermes wrapper (stale):
  web_deep_research → _safe_deep_research        hermes-agent/tools/ddg_search_tool.py
    - _query_variants_wrapper → search_deep() loop  ← does NOT forward query_type
    - Level-2 via _safe_expand_and_fetch
    - _apply_post_retrieval_filter (Jaccard, final_limit=25)
    - visual images via image_search (Bing/Jina)   ← broken URL shape (P0-4)
    - _compact_evidence (first-2-paragraphs intent, ineffective: P0-10)

Backend (shared): plugins/web-tools/ddg/ddg_search.py
  web_search: 1 strategy "duckduckgo" → _search_ddg (ddgs engine classes
    duckduckgo/yahoo/yandex/mojeek × pages) + inline DDGS merge
  search_deep: multi-query → filters → _check_url_live (HEAD→GET, proxy retry)
  content_relevance_score: phrase gate + word overlap
  visit_website_enhanced: curl_cffi fetch + overlay strip + Jina fallback
```

Doc discrepancies found: `README.md`/`CONTEXT.md`/`PROJECT_CONTEXT.md` describe features that are **dead or never wired** in code — see P0-2 (quarantine), P0-8 (video guard), P0-1 (dead-site proxy retry), P1-16 (visual allowlist), P1-15 (multi-engine strategies list).

---

## 2. Baseline (validation commands run)

| Check | Result |
|---|---|
| `python -m py_compile` on all 11 source/test .py files | ✅ ALL OK |
| `pytest plugins/web-tools/ddg/test_query_variants.py` (4 tests) | ✅ 4 passed |
| `pytest hermes-agent/test_coverage_gate.py` (4 tests) | ❌ 2 passed, 2 FAILED |
| Git state | working tree dirty; untracked: `.agents/`, `knowledge.md`, `ReviewPrompt.txt`, `test_*.py` |

Failing tests:
```
test_empty_pages_called_false  → assert _is_coverage_sufficient([], "anything") is True
test_narrow_query_coverage     → assert _is_coverage_sufficient(pages, "Yandex API") is True
```

---

## 3. P0 — Correctness bugs (fix first)

### P0-1. Dead-site proxy retry in `_check_url_live` is unreachable
`plugins/web-tools/ddg/ddg_search.py` (~line 1700+). The final block "Proxy retry for dead sites (DNS/timeout errors only…)" is written **after** a `try/except` in which every path `return`s. DNS failures raise inside the HEAD try → caught by `except Exception` → `return result`. The documented "try NECOBOX proxy as last resort for dead sites" feature **never executes**.

Fix: handle DNS/timeout inside the exception handlers (do not return immediately):
```python
except Exception as e:
    result["error"] = str(e)
    # NEW: attempt proxy retry for DNS/timeout here instead of unreachable tail block
    if not result.get("blocked") and USE_PROXY and PROXY_URL:
        ok = _proxy_retry_dead_site(result, url, timeout)
        if ok:
            return result
    return result
```
Extract the tail block into `_proxy_retry_dead_site(result, url, timeout)` and call it from both exception handlers. Delete the unreachable tail.

### P0-2. Domain quarantine never actually skips anything (both pipelines)
- `ddg_search.py::search_deep` (~1880): the pre-scan `for r in results_slice: if dom in quarantined: quarantined_urls.append(r)` runs **before** any validation, when `quarantined` is empty; the batch loop never consults `quarantined` again. URLs from a domain that gets 2+ 403/captcha failures in later batches are **still validated**.
- `standalone/orchestrator.py::_validate_urls` (~330): same pattern — `blocked_domains`/`deferred_domains` are checked in a one-time pre-scan that always sees empty sets; the batch loop processes `ordered_urls` without re-checking.

Fix (both files): check the sets **inside** the per-result loop and skip:
```python
# inside the future loop, before processing check:
if dom in blocked_domains:          # backend: quarantined
    blocked_domains_count += 1      # skip — do not validate
    continue
elif dom in deferred_domains:       # backend: defer handling
    deferred_urls.append(item)
    continue
```
(and re-scan remaining `ordered_urls` after each batch, or move the quarantine check to the point where the URL is dequeued).

### P0-3. `_safe_deep_research` does not forward `query_type` to `search_deep`
`hermes-agent/tools/ddg_search_tool.py` (~line 214): the loop calls
```python
out = search_deep(q, validate=True, classify=False, max_validate=max_validate,
                  query_variants=None, compose=False)   # missing query_type=
```
so the composite tool ignores intent: visual blocklist allowlist and video-filter awareness never apply in Hermes mode. Fix:
```python
out = search_deep(q, validate=True, classify=False, max_validate=max_validate,
                  query_variants=None, compose=False, query_type=query_type)
```

### P0-4. Hermes-mode visual images have null URLs
`ddg_search_tool.py` (~line 240): `_parse_bing_images` returns items with keys `thumbnail`, `page_url`, `title` (no `url`/`image_url`), but the wrapper reads:
```python
images.append({"url": item.get("url") or item.get("image_url") or item.get("url"), ...})
```
→ `url` is always `None`; the visual evidence pack contains broken image entries. Fix:
```python
images.append({
    "url": item.get("thumbnail") or item.get("page_url") or item.get("url"),
    "title": item.get("title") or item.get("page_url") or item.get("url"),
    "source": item.get("page_url") or item.get("url"),
})
```
Better: port the standalone approach — extract images from page HTML via `extract_fullsize_images` instead of `image_search`.

### P0-5. `visit_website_tool` handler returns unbounded page text
`ddg_search_tool.py` (~line 500):
```python
handler=lambda args, **_: visit_website_enhanced(args.get("url", ""),
                                                  max_chars=args.get("max_chars") or None),
```
`args.get("max_chars") or None` yields `None` when unset, which overrides the module default `MAX_CHARS=8000` → full page text returned (bloats context, can exceed `max_result_size_chars`). Fix:
```python
max_chars=args.get("max_chars") or 8000,
```

### P0-6. Missing Pillow dependency crashes visual pipeline
`standalone/orchestrator.py::_filter_images_for_report` (~540) does `from PIL import Image`. Pillow is **not** in the documented install deps (`README.md`: httpx, curl_cffi, ddgs, beautifulsoup4, lxml, PyQt5). Step 8b calls `_filter_images_for_report` without try/except → `ImportError` aborts the whole run for visual queries. Fix:
1. Add `Pillow` to install docs/requirements.
2. Guard the import and degrade gracefully:
```python
try:
    from PIL import Image
except ImportError:
    Image = None
...
if Image is None:
    return images   # skip size/hash filtering when Pillow unavailable
```

### P0-7. Report header always shows "Time: 0s"
`orchestrator.py::_build_report` (~1040): `timings.get("total", 0)` — `timings` has no `"total"` key (the value is `total_time`, computed after `_build_report` is called). Fix: compute `timings["total"] = round(time.time() - start_total, 1)` **before** calling `_build_report`, or pass `total_time` as a parameter.

### P0-8. `query_type == "video"` can never be produced
The classifier (`llm_client.py::classify_query_type`) returns one of 11 types — **none is `"video"`**. Yet the video filter is gated by `query_type != "video"` in 4 places (`ddg_search.py` 1952/1977, `orchestrator.py` 776/834). Video queries can never disable the filter. Fix: add `video` to the classifier enum + prompt, or remove the guard and treat it as unconditional.

### P0-9. `_apply_post_retrieval_filter._accept` accepts empty-text duplicates
`ddg_search_tool.py` (~line 300): for items with no tokens the code returns
```python
if not tokens:
    return any(... x != item and same-text ... for x in accepted)
```
i.e. returns **True (accept) when a duplicate already exists** — inverted dedup. Fix: `return not any(...)`.

### P0-10. `_compact_evidence` paragraph split is ineffective
`ddg_search_tool.py` (~175): splits text on `"\n\n"`, but backend text is produced with `soup.get_text(separator=" ")` (single spaces, no newlines) → the whole text is one "paragraph", so the summary is just `text[:1500]`. The "first two paragraphs" intent never materializes. Fix: either normalize text with newlines in the backend, or simply truncate by characters here and drop the paragraph fiction.

### P0-11. `test_coverage_gate.py` — duplicated logic + 2 failing tests
- The test file re-implements `_is_coverage_sufficient` instead of importing the real function from `ddg_search_tool.py` → the tests validate a copy that can (and did) drift.
- `test_empty_pages_called_false` asserts `True` for empty pages although the name says "called_false" — contradictory.
- `test_narrow_query_coverage` fails because the token filter `len(t) > 3` drops `"api"` (3 chars): with terms `["yandex"]` and one page, coverage is False.
- Token-length filters are inconsistent across modules: `query_variants.py` uses `> 2`, wrapper/tests use `> 3`.

Fix:
```python
# test_coverage_gate.py
from ddg_search_tool import _is_coverage_sufficient  # import real one
```
Standardize on `len(t) >= 3` everywhere (keeps 3-letter meaningful tokens like API/IoT/LLM), then:
- `test_empty_pages_called_false` → assert **False** (empty evidence should trigger expansion), rename to `test_empty_pages_insufficient`.

### P0-12. `_deep_read_and_extract` comment/code mismatch
`orchestrator.py` (~450): comment says "max 2 per domain" but `if domain_counts.get(key, 0) >= 1: continue` enforces max **1** per dedup key. Align comment with code (or change `>= 1` to `>= 2` if 2 was intended).

### P0-13. `_filter_images_for_report` bogus log arithmetic
`orchestrator.py` (~600): `proxy recovered: {len(filtered) - len(seen_hashes) + len(quarantine)}` is meaningless (mixed phases). Track phase-2 successes explicitly:
```python
phase2_recovered = 0
...
if accepted in phase 2: phase2_recovered += 1
log(f"... (quarantine: {len(quarantine)}, proxy recovered: {phase2_recovered})")
```

### P0-14. `_build_report` image URLs not properly escaped
`orchestrator.py` (~1060): only `.replace(" ", "%20")`. URLs containing `&`, `"`, or non-ASCII break markdown. Fix:
```python
from urllib.parse import quote
img_url = quote(img["url"], safe=":/?&=#%")
```

---

## 4. P1 — Logic inconsistencies & dead code

### P1-15. Dead code in `ddg_search.py`
- `_search_ddgs` (779), `_search_ddg_json` (739), `_search_jina_ddg` (878), `_search_searx` (902), `_search_jina` (937), `_parse_google_results` (586): defined, **never called** — `web_search` only invokes `_search_ddg` + an inline `DDGS()` merge. The `strategies` list in `web_search` contains a single strategy.
- `fetch_page` (952): used only from the module CLI.
- `_classify_by_content` (1475): only runs when `classify=True`; the only callers (wrapper, standalone) pass `classify=False`. Effectively dead.
- `extract_js_data` (1232): unused within `ddg_search.py` (only `visit_website_enhanced` uses its copy).
- `_relevance_score` (1791): only meaningful in compose mode.
- `_get_session(domain=None)`: `domain` param unused. `IMPERSONATE = "chrome124"` constant unused (impersonation is randomized).
- Compose path: `compose.py` and `search_deep(compose=True)` are never enabled by any caller (wrapper always `compose=False`).

Recommendation: delete or clearly mark these (keep `_search_*` only if an engine switch is planned).

### P1-16. `VISUAL_ALLOWLIST` override is dead
Verified programmatically: `BLOCKED_DOMAINS ∩ VISUAL_ALLOWLIST = ∅`. `is_blocked_domain`'s allowlist branch can never fire. Fix: either move the visual-relevant domains into `BLOCKED_DOMAINS` deliberately (so the override matters) or remove the override and its comment.

### P1-17. Duplicated entries in `BLOCKED_DOMAINS`
`afisha.yandex.ru`, `realty.yandex.ru`, `market.yandex.ru`, `travel.yandex.ru`, `dzen.ru`, `e1.ru`, `gismeteo.ru`, `vk.com`, `ok.ru` appear twice. Also `www.bing.com` vs `bing.com` handling. Deduplicate.

### P1-18. `llm_client.synthesize_answer` is unused
`standalone/llm_client.py` (~112). `orchestrator.py` inlines its own synthesis prompt via `chat_completion`. Dead code — remove or refactor the orchestrator to use it.

### P1-19. Three divergent query-variant generators
`query_variants.py::generate` (TYPE_SUFFIXES), `orchestrator._query_variants` (own SUFFIXES table incl. more types), `ddg_search_tool._query_variants_wrapper` (fallback heuristics). Different suffix tables and token filters. Standardize on `query_variants.py` as the single source; the orchestrator should import it instead of redefining its own.

### P1-20. Two block-detection implementations
`_detect_blocked` (`ddg_search.py`) vs `_is_blocked` (`visit_website_enhanced.py`) — same purpose, different names, slightly different indicator lists. Both match bare substrings `"forbidden"`, `"access denied"`, `"страница не найдена"` which can false-positive on legitimate content that merely mentions those words. Standardize one function and prefer status-code + structured checks (as already done for the `captcha` case).

### P1-21. Massive copy-paste between modules (already drifted)
- `ddg_search.py` ↔ `visit_website_enhanced.py`: `extract_fullsize_images`, `upgrade_to_fullsize`, `_ContentParser`, `_strip_block_overlay`, `_get_block_type`, `extract_js_data`, `_SCRIPT_DATA_PATTERNS`, `UA_POOL`, `IMPERSONATE_POOL`.
- `orchestrator.py` ↔ `ddg_search.py`: `_extract_main_content`, `_clean_content`, `_NOISE_LINES`, `_NOISE_BLOCKS`.

Drift example: `extract_js_data` in `ddg_search.py` guards `match.group(0)`; the copy in `visit_website_enhanced.py` doesn't. Fix: extract shared helpers into one module (e.g. `plugins/web-tools/ddg/_common.py`) and import from all three.

### P1-22. Global mutable state
`run_deep_research` mutates module globals `ddg_search.USE_PROXY/PROXY_URL`, `vwe.USE_PROXY/PROXY_URL` and clears `_sessions`. Concurrent or nested use breaks. Pass config explicitly (e.g. a `ProxyConfig` object or per-call args through the backend functions).

### P1-23. Shared curl_cffi sessions across threads
`_sessions` (module-level dict) is shared by `ThreadPoolExecutor` workers (`max_workers=5` in `search_deep`, `10` in `_validate_urls`). `curl_cffi.requests.Session` is not documented thread-safe; concurrent requests on one Session can cause flaky responses. Use `threading.local()` per-thread sessions or a lock.

### P1-24. DDGS merge drops short queries
`web_search` keeps only DDGS items with `matches >= 2` query words. For 1-word queries no DDGS result ever passes → coverage loss. Fix: `matches >= 1` for queries with fewer than 2 significant words.

### P1-25. `image_search` forces 16:9
`image_search` hardcodes `qft=+filterui:images-16by9`, which excludes portrait images (bad for person/wallpaper queries). Make the aspect filter optional.

### P1-26. Inconsistent blocked-content check in httpx fallback
`ddg_search.py::_fetch_httpx` checks `_detect_blocked` + `_is_valid_content`; `visit_website_enhanced.py::_fetch_httpx` only checks `len > 100`. Align.

### P1-27. Video filter duplicated
`search_deep` applies the video filter twice (lines ~1952 and ~1977). Extract one `_filter_video_urls(urls, query_type)` helper.

### P1-28. `search_deep` default `classify=True`
Signature default is `classify=True`, but every caller passes `False`. Flip the default to `False` so accidental calls don't pay the classification cost.

### P1-29. `_query_variants_wrapper` ignores query_type in its fallback
The static fallback appends `{token} history/trends/examples` regardless of `query_type`. If `query_variants.py` is missing (a documented post-restore state), intent-aware suffixes are lost. Port the orchestrator suffix table into the fallback.

### P1-30. GUI duplicates slug logic
`gui.py` re-implements the slug regex from `deep_research.py::slugify`. Reuse `slugify`.

### P1-31. GUI "Cancel" doesn't stop work
`ResearchThread.cancel()` only sets a flag checked in the log callback; search/validation/deep-read continue. Recommend cooperative cancellation via a threading.Event passed into `run_deep_research` (checked in loops), plus `closeEvent` waiting on the thread.

### P1-32. Unreachable Cloudflare branch in `_check_url_live`
The "Header-based bot detection" block (`server`/`cf-ray` with status 403/503 → `pass`) is dead: those statuses were already handled and `return`ed in the branch above. Remove or restructure.

---

## 5. P2 — Maintainability / hygiene

- **No dependency manifest**: no `requirements.txt`/`pyproject.toml`; pinned versions live only in `CONTEXT.md`. `ddgs` engine-class imports (`ddgs.engines.*`) are version-sensitive. Add `requirements.txt` with the pinned set (httpx==0.28.1, curl_cffi==0.14.0, ddgs==9.14.4, beautifulsoup4==4.13.4, lxml==6.0.2, PyQt5, Pillow).
- **Test files at repo root** (`test_blogspot.py`, `test_engines.py`, `test_old_urls.py`, `test_pipeline.py`, `test_quick.py`, `test_strategies.py`) are uncommitted ad-hoc scripts with `sys.path.insert` relative to CWD — fragile; move under a `tests/` dir with proper package imports.
- **Mixed-language code comments** (Russian/English) and a stray empty comment block in `_classify_by_content` (`# ` followed by blank) — cleanup.
- **`restore.ps1` smoke probe** writes `deep_test_vargas.py` into `~/.hermes/hermes-dev/` — probe artifacts should go to a temp dir.

---

## 6. P3 — Tests

Current coverage: `test_query_variants.py` (4 tests, imports the real module — good) and `test_coverage_gate.py` (4 tests, tests a copy — bad, 2 failing).

**Missing tests (all pure, network-free, highly testable):**
- `content_relevance_score` — phrase gate, short-text penalty, multi-hit bonus, namesake disambiguation ("Sara James" vs "Sara St James").
- `extract_fullsize_images` / `upgrade_to_fullsize` — thumbnail→full-size patterns, tracking-pixel filtering, relative URL resolution.
- `is_blocked_domain` — allowlist/blocklist, subdomain matching, visual override.
- `_dedup_key` (platform/mirror/query-string) in orchestrator.
- `_apply_post_retrieval_filter` — Jaccard dedup, per-source quota, empty-text case (P0-9).
- `_compact_evidence` — truncation behavior.
- `_check_url_live` with mocked sessions — status handling, proxy retry, quarantine paths.
- `classify_query_type`/`enrich_query` with a stubbed `chat_completion`.

Fix the failing tests first (P0-11), then add the above. Add a simple `pytest` section to docs and wire `tests/` collection.

---

## 7. P4 — Security / operations

- **`verify=False` on every curl_cffi Session** (both backend files): TLS verification disabled for all requests, including through the proxy → MITM exposure. At minimum document why, and pin/allow CA verification when feasible.
- **`safe="off"`** in `search_deep` web_search calls: deliberate for the research domain (adult-oriented queries) but should be a configurable flag, not a hardcode.
- **`restore.ps1` `Stop-HermesIfRunning`**: kills up to 5 arbitrary `python`/`node` processes machine-wide (matches by process name only). On a dev machine this can kill unrelated user processes. Restrict to a Hermes-specific process tree or require explicit consent (GUI already passes `-NoStopHermes`; CLI users may not know).
- **`restore.ps1` backup**: `Copy-Item $HermesHome $backupDir -Recurse` copies the entire `~/.hermes` (including `hermes-agent/venv`) — slow and large. Back up only the 7 managed files + skills.
- **`restore.ps1` false-positive success**: with `$ErrorActionPreference = 'SilentlyContinue'`, a missing venv python (`$Venv`) silently skips compile checks and `$LASTEXITCODE` may be unset → restore reports OK despite unverified code. Explicitly test `Test-Path $Venv` and set `$HadFailure = $true` when missing.
- **No SSRF concern by design**: the tool is meant to fetch arbitrary URLs; out of scope.

---

## 8. Remaining concerns (not safely auto-fixable without product decisions)

1. **Two diverged pipelines (standalone vs Hermes)**: unifying them is the root fix for most drift, but the porting strategy (wrapper gains orchestrator features vs. shared backend) is a product decision — flagged by the user; not changed here.
2. **Thread-safety of shared curl_cffi sessions** needs runtime validation before locking down a fix.
3. **Block-detection false-positive tuning** (`forbidden`, `access denied`, Russian phrases as substring matches) is heuristic by nature; aggressive tightening risks regressions on the 40-46% blocked-site reality.
4. **Whether to keep `compose.py` / `synthesize_answer` / unused search strategies** for future Hermes use — removal is safe only if no external caller depends on them.
5. **`safe="off"` and the NSFW-oriented blocklist/allowlist defaults** are deliberate product choices; changing them is a policy call.
6. **restore.ps1 process-killing behavior** — see P4; needs explicit owner approval.

---

## 9. Assumptions made

1. **No git commit performed.** `ReviewPrompt.txt` step 1 says "commit all changes first", but the working tree contains untracked WIP files (`.agents/`, `knowledge.md`, `ReviewPrompt.txt`, `test_*.py`, `Audit/`) that were not authored for this review; committing without explicit instruction would be destructive. Review reflects the working tree as-is.
2. **`Audit.md` is written at the repo root** per the literal instruction ("write to new Audit.md"); the existing `Audit/` directory is empty (confirmed).
3. **No production code was modified** (per ReviewPrompt: "Don't change anything in the code"). All fixes above are prescriptions with ready-to-use code.
4. Line numbers are approximate (refer to function names if lines shift).
5. `knowledge.md` was updated earlier in this session (per `/init` + user directive) to record that **the standalone pipeline is the reference implementation** and to enumerate the Hermes-wrapper gaps — consistent with this review's findings.

---

## 10. Concrete implementation plan (verified against code, before changes)

Every solution below was re-checked against the exact current source (line numbers verified by grep/sed). Consequences and risks are stated per fix. **No code was changed yet.**

### Fix ordering (dependency graph)

| Batch | Contains | Why this order |
|---|---|---|
| **A. Standalone-only** | FIX-7, FIX-12, FIX-13, FIX-14, FIX-6, FIX-8 | Independent, zero cross-module risk, immediately testable via `test_pipeline.py`/GUI |
| **B. Backend (shared)** | FIX-1 (`_check_url_live` rewrite), FIX-2 (quarantine in `search_deep`), FIX-16 (video filter helper) | Backend is used by both pipelines; verify here first |
| **C. Hermes wrapper** | FIX-3, FIX-4, FIX-5, FIX-9, FIX-10, FIX-11, FIX-15 | Depends on B (query_type semantics), but mostly independent |
| **D. Tests** | FIX-17 (test_coverage_gate), new unit tests | Last; locks in all fixes |

---

### FIX-1. Rewrite `_check_url_live` to make retry paths reachable
`plugins/web-tools/ddg/ddg_search.py:1604-1805` (verified). Fixes P0-1, P1-32 and new findings NEW-1, NEW-2.

Current defects (all verified in source):
1. The tail block `# Proxy retry for dead sites (DNS/timeout...)` (line 1759) is **unreachable** — every path of the two `try/except` blocks above it `return`s.
2. `result["blocked"]` is initialized `False` and only ever set `True` via the content-based `_detect_blocked` branch — a hard HTTP 403/429/451/503 (proxy disabled or failed) falls through to the generic `status >= 400` return with `blocked=False`, so blocked stats are undercounted (NEW-1).
3. The 503-recovery GET retry condition `if result["status"] == 503 and result.get("blocked") is not False` can never be true (`blocked` is `False` or already `True`) → dead (NEW-2).
4. The Cloudflare header-check block (`server`/`cf-ray` + status 403/503 → `pass`) is unreachable because those statuses are returned earlier (P1-32).

Proposed replacement (same result-dict shape, same thresholds `500 chars / 50 words`, same HEAD→GET order — behavior preserved, only dead paths activated):

```python
def _check_url_live(url, timeout=10):
    result = {
        "alive": False, "status": None, "content_type": "",
        "content_length": 0, "text_length": 0, "text_words": 0,
        "blocked": False, "proxy_used": False, "error": None, "body": None,
    }
    session = _get_session()
    if not session:
        result["error"] = "no session"
        return result

    def _proxy_retry(method="head"):
        """One proxy attempt; returns (status, body_or_None) or None."""
        if not (USE_PROXY and PROXY_URL):
            return None
        try:
            import curl_cffi
            ps = curl_cffi.requests.Session(
                impersonate=random.choice(IMPERSONATE_POOL),
                proxies={"http": PROXY_URL, "https": PROXY_URL},
                verify=False, timeout=timeout,
            )
            resp = getattr(ps, method)(url, timeout=timeout, allow_redirects=True)
            if resp.status_code < 400:
                return resp.status_code, getattr(resp, "text", "") or None
        except Exception:
            pass
        return None

    def _finalize(raw):
        """Set body/text metrics; returns True when page counts as alive."""
        result["body"] = raw
        text = re.sub(r"<[^>]+>", " ", raw)
        text = re.sub(r"\s+", " ", text).strip()
        result["text_length"] = len(text)
        result["text_words"] = len(re.findall(r"\w+", text))
        if result["text_length"] < 500 or result["text_words"] < 50:
            result["error"] = "empty or too-small page"
            return False
        result["alive"] = True
        return True

    # Phase 1: HEAD (fast fail)
    try:
        head_resp = session.head(url, timeout=timeout, allow_redirects=True)
    except Exception as e:
        err = str(e)
        result["error"] = err
        # Dead site (DNS/timeout): proxy GET as last resort (previously unreachable)
        if any(k in err.lower() for k in
               ["getaddrinfo", "timeout", "failed to resolve", "name or service"]):
            pr = _proxy_retry("get")
            if pr and pr[1] and not _detect_blocked(pr[1]):
                result["status"] = pr[0]
                if _finalize(pr[1]):
                    result["proxy_used"] = True
        return result

    result["status"] = head_resp.status_code
    result["content_type"] = head_resp.headers.get("content-type", "")[:200]
    cl = head_resp.headers.get("content-length", "0")
    result["content_length"] = int(cl) if cl.isdigit() else 0
    status = result["status"]

    # Phase 2: retryable statuses
    if status in (403, 429, 451, 503):
        pr = _proxy_retry("get" if status == 503 else "head")
        if pr:
            result["status"] = pr[0]
            result["proxy_used"] = True
            if status == 503 and pr[1]:
                if not _detect_blocked(pr[1]) and _finalize(pr[1]):
                    return result
                result["blocked"] = True
                result["error"] = "blocked (captcha/cloudflare/etc)"
                return result
            # 403/429/451 recovered via proxy HEAD → fall through to GET below
        elif status == 503:
            # Server may have recovered — one direct GET retry after short delay
            try:
                time.sleep(2)
                retry_resp = session.get(url, timeout=timeout, allow_redirects=True)
                if retry_resp.status_code < 400 and not _detect_blocked(retry_resp.text):
                    result["status"] = retry_resp.status_code
                    result["body"] = retry_resp.text
                    if _finalize(retry_resp.text):
                        return result
            except Exception:
                pass
            result["blocked"] = True
            result["error"] = f"HTTP {status}"
            return result
        else:
            # Hard block, proxy failed/disabled → NOW marked blocked correctly
            result["blocked"] = True
            result["error"] = f"HTTP {status}"
            return result
    elif status in (404, 405, 410, 500, 502, 504) or status >= 400:
        result["error"] = f"HTTP {status}"
        return result

    # Phase 3: 2xx/3xx — GET body
    try:
        body_resp = session.get(url, timeout=timeout, allow_redirects=True)
    except Exception as e:
        result["error"] = str(e)
        return result
    result["status"] = body_resp.status_code
    result["content_type"] = body_resp.headers.get("content-type", "")[:200]
    cl2 = body_resp.headers.get("content-length", "0")
    result["content_length"] = max(result["content_length"], int(cl2) if cl2.isdigit() else 0)
    if result["status"] >= 400:
        result["error"] = f"HTTP {result['status']}"
        return result

    raw = body_resp.text
    if _detect_blocked(raw):
        pr = _proxy_retry("get")
        if pr and pr[1] and not _detect_blocked(pr[1]):
            raw = pr[1]
            result["status"] = pr[0]
            result["blocked"] = False
            result["error"] = None
            result["proxy_used"] = True
        if _detect_blocked(raw):
            result["blocked"] = True
            result["error"] = "blocked (captcha/cloudflare/etc)"
            return result

    _finalize(raw)
    return result
```

**Consequences checked:**
- Result-dict keys and thresholds identical → callers (`search_deep`, orchestrator `_validate_urls`, `test_quick.py`, `test_old_urls.py`) unaffected.
- Request count per URL: alive path = 1 HEAD + 1 GET (unchanged); blocked path = 1 HEAD + 1 proxy attempt (unchanged); DNS/timeout path = 1 HEAD(fail) + 1 proxy GET (new, was 0) — the intended feature.
- The 2s sleep only on the 503 direct-retry path (matches original intent).
- Risk: medium. Mitigation: add a unit test with a mocked session (FIX-17 covers the pattern); the network smoke tests (`test_quick.py`) remain available.

---

### FIX-2. Make domain quarantine actually skip URLs
`plugins/web-tools/ddg/ddg_search.py:2011-2062` and `standalone/orchestrator.py:331-352, 420-440` (verified).

Root cause (both files): the pre-scan partitions `normal_urls`/`quarantined_urls` (backend) or `normal/deferred` (orchestrator) **once, before any validation**, when the domain-failure sets are empty; the batch loop then never re-checks them. A domain with 2+ 403/captcha failures keeps being validated.

**Backend fix** — inside the future-processing loop in `search_deep` (after `dom` is computed, before the `if not check['alive']` branch), skip URLs from quarantined domains:
```python
if dom in quarantined:
    continue  # domain already proven blocking us — do not validate
```
(`quarantined` grows during earlier batches; later batches now skip. `quarantined_count` keeps counting domains added — unchanged semantics.)

**Orchestrator fix** — same pattern in `_validate_urls` (skip `blocked_domains`), plus make deferral real by running a **second small pass** after the main loop for `deferred_urls` collected during the run (cap `MAX_DEFERRED=10`):
```python
# after main batch loop:
for item in deferred_urls[:MAX_DEFERRED]:
    if alive_count >= max_validate:
        break
    item, check = validate_one(item)
    ...same result handling...
```
**Consequences checked:** fewer wasted requests (perf win on blocked domains); stats unchanged except `validated` shrinks; `deferred_count` (orchestrator) must be incremented when a domain is deferred — currently stuck at 0 (NEW-5). Risk: low.

---

### FIX-3. Forward `query_type` in `_safe_deep_research`
`hermes-agent/tools/ddg_search_tool.py:220-227` (verified). Add one argument:
```python
out = search_deep(
    q, validate=True, classify=False, max_validate=max_validate,
    query_variants=None, compose=False, query_type=query_type,
)
```
**Honest consequence note:** the backend currently uses `query_type` only for the (dead) visual allowlist (P1-16) and the (dead) `query_type == "video"` guard (P0-8), so this change is contract-correct but behaviorally neutral until FIX-16/P1-16 land. Do it anyway — it removes the inconsistency with `_safe_search_deep` (line 60 already forwards it). Risk: none.

---

### FIX-4. Repair Hermes-mode visual image URLs
`hermes-agent/tools/ddg_search_tool.py:274-285` (verified). `_parse_bing_images` returns keys `thumbnail/page_url/title`; the loop reads `url`/`image_url` → always `None`.
```python
for item in img_out.get("results", [])[:8]:
    images.append({
        "url": item.get("thumbnail") or item.get("page_url") or item.get("url"),
        "title": item.get("title") or item.get("page_url") or item.get("url"),
        "source": item.get("page_url") or item.get("url"),
    })
```
**Consequences checked:** thumbnails (Bing CDN) become usable links; no shape change to the returned pack. Long-term improvement (port standalone's page-HTML extraction) requires backend to expose raw HTML — out of scope here, documented in §11. Risk: none.

---

### FIX-5. Cap `visit_website_tool` output
`hermes-agent/tools/ddg_search_tool.py:521` (verified):
```python
max_chars=args.get("max_chars") or 8000,
```
(`args.get(...) or None` was overriding the module default 8000 and returning unbounded text.) Consequence: bounded context; schema unchanged. Risk: none.

---

### FIX-6. Pillow: guard + document
`standalone/orchestrator.py:564+` (`from PIL import Image` inside `_filter_images_for_report`).
```python
try:
    from PIL import Image
except ImportError:
    Image = None
...
if Image is None:
    return images  # degrade: skip format/hash/size filtering
```
Also add `Pillow` to install docs (`README.md`). **Consequence:** without Pillow the visual report may include duplicates/tiny images (acceptable degradation); with Pillow behavior unchanged. Risk: none.

---

### FIX-7. Fix "Time: 0s" in report header
`standalone/orchestrator.py:1009` (call site) / `_build_report` header. Insert before `_build_report(...)`:
```python
timings["total"] = round(time.time() - start_total, 1)
```
**Consequence:** header now shows real total; `timings` dict gains one key (stats output already includes `total_time` separately). Risk: none.

---

### FIX-8. `query_type == "video"` — decision needed (P0-8)
Classifier (`standalone/llm_client.py:38-74`) never emits `"video"`; guards at `ddg_search.py:1952,1977` and `orchestrator.py:776,834` are dead. Two verified options:
- **Option A (recommended, matches documented intent):** add `video` to the classifier list/prompt in `llm_client.py`, add a `video` suffix row in `orchestrator._query_variants` (line 225+) and `query_variants.py::TYPE_SUFFIXES`, and add `"video"` to both wrapper schema enums (`_schema_search_deep`, `_schema_deep_research`). Users can then search video sources.
- **Option B (minimal):** remove the `query_type != "video"` guard and always filter video domains.
**Consequence of A:** one more intent; video queries skip the filter as intended. Consequence of B: simpler, but no way to request video. This is a product choice — flag for user decision.

---

### FIX-9. Inverted empty-text dedup in `_apply_post_retrieval_filter`
`hermes-agent/tools/ddg_search_tool.py:335-338` (verified). The empty-tokens branch returns `True` (accept) when a text-identical item already exists — inverted. Fix:
```python
if not tokens:
    return not any(
        _selected_id(x) != _selected_id(item)
        and " ".join([x.get("title") or "", x.get("text") or "", x.get("snippet") or ""]) == text
        for x in accepted
    )
```
**Consequence:** empty-text duplicates are now rejected (intended). Risk: none.

---

### FIX-10. `_compact_evidence` paragraph logic is a no-op
`hermes-agent/tools/ddg_search_tool.py:190-192` (verified): backend text is `get_text(separator=" ")` — no `\n\n`, so `split("\n\n")` yields one element and the summary is just `text[:1500]`. Two options: (a) leave output identical and correct the docstring/comment, or (b) have the backend emit `\n` separators. Recommend (a) for zero-risk now; note (b) as a backend change for later. Risk: none either way.

---

### FIX-11. Filter non-alive pages from Hermes evidence
`hermes-agent/tools/ddg_search_tool.py:231-249` (verified, NEW-3): `pages.append(...)` runs for **all** results, alive or not; evidence then mixes dead pages into the LLM pack. Fix — after the loop, before `top_alive`:
```python
pages = [p for p in pages if p.get("alive")]
```
**Consequences:** `alive_count` unchanged; `top_alive` unchanged; `_is_coverage_sufficient` now sees only alive pages (more accurate); evidence/compact pack contains only reachable sources. `raw_count` stays as the raw metric. Risk: low.

---

### FIX-12. `_deep_read_and_extract` comment vs code (P0-12)
`standalone/orchestrator.py` (~line 465): comment says "max 2 per domain", code enforces 1 (`>= 1`). Decision: if 1-per-domain was intended, fix the comment; if 2 was intended, change to `>= 2`. Recommend **comment → match code (1)**: platform dedup keys already give one page per blog; raising to 2 adds duplicate-content risk. Risk: none.

---

### FIX-13. `_filter_images_for_report` log arithmetic
`standalone/orchestrator.py` (~line 660, NEW-6): `proxy recovered: {len(filtered) - len(seen_hashes) + len(quarantine)}` is meaningless. Track `phase2_recovered += 1` in the phase-2 accept branch and log that. Risk: none.

---

### FIX-14. Properly escape image URLs in reports
`standalone/orchestrator.py:1049` (verified): only spaces are replaced. Fix:
```python
from urllib.parse import quote
img_url = quote(img["url"], safe=":/?#&=%~")
```
**Consequence:** URLs with `&`, quotes, unicode render as valid markdown links. Risk: none.

---

### FIX-15. Per-source quota should be per-domain, not per-URL
`hermes-agent/tools/ddg_search_tool.py:322,351,355` (verified, NEW-4): `source_counts` is keyed by the exact URL, but URL duplicates are already removed by `seen_page` — the quota can never bind. Key by base domain instead:
```python
def _base_domain(url):
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) > 2 else host
...
url = _base_domain(item.get("url") or "")
if source_counts.get(url, 0) >= max_per_source_url:
    continue
```
**Consequence:** actually limits per-source bias (the stated intent in the comment). Risk: low — may reduce evidence from a single large site, which is the point.

---

### FIX-16. Single video-filter helper (P1-27)
`ddg_search.py` applies the video filter twice (lines 1952, 1977). Extract `_filter_video_urls(urls, query_type)` and call it once after all URL collection (including dynamic variants). Consequence: identical behavior, less duplication. Risk: none.

---

### FIX-17. Repair `test_coverage_gate.py` (P0-11)
`hermes-agent/test_coverage_gate.py` (verified):
1. Stop duplicating `_is_coverage_sufficient`. Extract it to a pure shared module (e.g. `plugins/web-tools/ddg/_coverage.py`) imported by both the wrapper and the test — avoids the `tools.registry` import chain of `ddg_search_tool.py`.
2. Standardize the token filter to `len(t) >= 3` (keeps 3-letter terms like `api`).
3. Fix the two assertions:
   - `test_empty_pages_called_false` → assert **False** (empty evidence must trigger expansion), rename accordingly.
   - `test_narrow_query_coverage` passes once `api` is counted.
**Consequence:** the test now exercises the real code; behavior change is limited to 3-letter tokens now participating in coverage — verified harmless because the bar is `>= max(1, len(terms)//2)` (for 2 terms, 1 hit suffices).

---

### Recommended new unit tests (all network-free)
- `_check_url_live` with a mock session (status matrix: 200/403/429/451/503/404/DNS-error, proxy on/off).
- `content_relevance_score` (phrase gate, namesakes, short-text penalty, multi-hit bonus).
- `extract_fullsize_images`/`upgrade_to_fullsize` (suffix removal, relative URLs, tracking-pixel filter).
- `is_blocked_domain` (subdomains, visual override).
- `_dedup_key` (platform/mirror/query-string).
- `_apply_post_retrieval_filter` (Jaccard, per-domain quota, empty-text case).

---

## 11. New findings discovered while designing solutions (documented for later verification)

These are **not** part of the fix batches above; they are recorded per instruction for later review:

- **NEW-1.** `blocked` flag never set for status-based 403/429/451/503 failures (`_check_url_live`) → `blocked_count` stats undercount; hard 403s reported as "dead". Fixed by FIX-1.
- **NEW-2.** 503-recovery direct-GET retry condition impossible → dead. Fixed by FIX-1.
- **NEW-3.** Hermes evidence pack mixes non-alive pages. Fixed by FIX-11.
- **NEW-4.** `_apply_post_retrieval_filter` source quota keyed by exact URL → inert. Fixed by FIX-15.
- **NEW-5.** `deferred_count` in orchestrator `_validate_urls` is never incremented (always logs 0); deferral mechanism inert — same root cause as the quarantine bug. Addressed in FIX-2; verify counting after.
- **NEW-6.** `_filter_images_for_report` proxy-recovered log arithmetic meaningless. Fixed by FIX-13.
- **NEW-7.** `search_deep` returns results in batch order, **not** sorted by relevance (docs claim sorted; only compose mode sorts). Either sort `validated` by `relevance` before return or fix the docs.
- **NEW-8.** Report evidence is unbounded: every validated page passing filters contributes up to 4000 chars (`orchestrator.py:957`) → reports can reach hundreds of KB. Consider a `top_n`-style cap on evidence (product call — full-text "Sources" section may be intentional).
- **NEW-9.** `raw_count` in `_safe_deep_research` (line 231) counts pre-dedup rows across variants → inflated `panel.raw`. Move `raw_count += 1` after the `seen_page` dedup if the metric should be unique.
- **NEW-10.** Two competing scoring functions: backend `_relevance_score` (word overlap, no phrase gate, used for `result.relevance`) vs `content_relevance_score` (phrase gate). Evidence quality signals differ between pipelines. Standardize on `content_relevance_score`.
- **NEW-11.** `web_search` DDGS merge requires `matches >= 2` → single-word queries never get DDGS coverage (related to P1-24; verify together).
- **NEW-12.** `image_search` forces 16:9 (`qft=+filterui:images-16by9`) → portrait images dropped (related to P1-25).
- **NEW-13.** GUI proxy checkbox defaults ON (`gui.py`), orchestrator default OFF, and `ddg_search` reads `~/.hermes/proxy.env` at import while `visit_website_enhanced` has a hardcoded `USE_PROXY=False` — three different proxy defaults across modules. A run with proxy enabled but no proxy running adds ~1 connect-refused attempt per blocked URL (minor perf). Unify proxy config.
- **NEW-14.** `visit_website_enhanced._fetch_httpx` does not apply `_is_blocked` (returns any HTML > 100 chars), unlike `ddg_search._fetch_httpx` (P1-26).
- **NEW-15.** `visit_website_enhanced._fetch_subprocess` uses `-m 20` on curl but `timeout=15` on `subprocess.run` — mismatch.
- **NEW-16.** `classify_query_type` sends `max_tokens=20` and parses the whole response; a verbose model returning extra words degrades to `"general"`. Parse the first token only.
- **NEW-17.** `search_deep` `_SEARCH_PATTERNS` substring `/search` over-filters URLs like `/search-engine-tutorial` (minor, verified in code at ~line 1940).
- **NEW-18.** `ddgs` engine-class imports (`ddgs.engines.*`) are version-sensitive; `test_engines.py` at root is the only guard. Pin `ddgs==9.14.4` in a requirements file and add the engine test to CI if one is set up.
- **NEW-19.** `_safe_expand` substring scoring matches partial words (e.g. `sara` matches `sarah`) — same family as P0-11's token issue; verify with real data.
- **NEW-20.** `visit_website_tool` schema default is unset while backend default is 8000; after FIX-5 the schema should document the default (minor doc change).

---

## 12. Validation plan for the fix batches

1. `python -m py_compile` all modified modules.
2. `python -m pytest plugins/web-tools/ddg/test_query_variants.py hermes-agent/test_coverage_gate.py` — must be 8/8 green.
3. New unit tests from FIX-17.
4. Network smoke (optional, requires internet): `python test_quick.py` and `python test_strategies.py` — expect no regressions in liveness classification; `blocked` counts may rise slightly (NEW-1 fix) — verify this is the intended behavior change.
5. End-to-end (optional, requires local LLM): `python standalone/deep_research.py "test query" --validate 10` and one visual query to exercise FIX-6/FIX-14.

---

## 13. Implementation status (2026-08-10)

All fix batches were implemented, validated, and committed on `fix/pipeline-quality`.
Each commit is a rollback point.

| Commit | Batch | Content |
|---|---|---|
| `8c69265` | baseline | working tree as-is before any fix |
| `-` | A | standalone: `_validate_urls` quarantine+deferral (`_process_one`/`_partition` + final deferred pass), Pillow guard, `phase2_recovered` counter, `timings["total"]`, `quote()` image URLs, deep-read cap comment, `video` suffixes, classifier token parse + `video` type |
| `baec8e6` | B | backend: `_check_url_live` rewrite (proxy GET actually delivers content, `blocked` flag for hard 4xx/5xx, reachable 503 retry, DNS/timeout proxy retry), real quarantine skip in `search_deep`, `_should_filter_url`/`_is_video_url` helpers, relevance sort, DDGS min_matches, 16:9 removed, tighter search patterns |
| `e07a23e` | C | wrapper: `query_type` forwarding, thumbnail/page_url image shape, `max_chars or 8000`, alive-only evidence, per-domain source quota, `_accept` inversion, `_compact_evidence` char truncation, `video` in schema enums |
| `b9ccc40` | D | tests: `_coverage.py` shared module, `test_coverage_gate.py` rewritten (5 tests), `test_check_url_live.py` (6), `test_scoring.py` (7); `restore.ps1` now copies `query_variants.py` + `_coverage.py` |
| `4fea22d` | review | reviewer findings: defer-before-quarantine for 503/timeout/DNS in both consumers, proxy GET for 403/429/451 (was dead-end proxy HEAD), `video` in `query_variants.py`, monkeypatch-based tests |

Validation results:
- `py_compile`: all 14 modules OK.
- pytest: **22/22 passed** (`test_query_variants`, `test_check_url_live`, `test_scoring`, `test_coverage_gate`).
- Network smoke (`test_quick.py`): 3/3 real URLs correctly classified alive.

---

## 14. Integration analysis: web-media-parser → deep-research pipeline (2026-08-10)

Source: `Temp/web-media-parser/` (media crawler + browser extension, tested & working).
Verified against actual code, not docs. Goal: port **proven** full-size-image discovery and
URL-hygiene logic into `plugins/web-tools/ddg/` + `standalone/` + Hermes wrapper.

### 14.1 What the crawler has that we don't

| Capability | Where (web-media-parser) | Current project equivalent | Gap |
|---|---|---|---|
| **Imagus Sieve rules** (823-849 regex+JS rules thumbnail→fullsize) | `site_pattern_manager.py::transform_image_url` + `Imagus_sieve_*.json` | `upgrade_to_fullsize()` (ddg_search.py:1263, visit_website_enhanced.py:438) — ~10 hand-written heuristics | **Biggest win**: 800+ curated site rules vs 10 heuristics |
| **JS rule→Python converter** | `_try_parse_imagus_js` / `_build_js_callable` (pure Python, no Deno) | none | ~40% of sieve JS rules convertible without Deno; rest fail-open |
| **link→url→res fullsize discovery** (thumbnail→linked page→original, e.g. imx.to) | `get_link_rule`+`apply_link_url_transform`+`extract_res_urls`; `parser_manager._discover_linked_fullsize` (concurrency-bounded, time-budgeted) | none | Visual queries lose external image-host originals |
| **#ext# variant expansion** (image.#jpg png# → 2 URLs) | `_expand_variants` | none | Sieve targets often emit multi-variant output |
| **WordPress size-suffix strip** (`-300x200.jpg`→`.jpg`) | `transform_image_url` step 4 | `upgrade_to_fullsize` step 1 (same regex) | ✅ already present |
| **Precision-first junk filter** (ad/tracker hosts dot-boundary + path tokens + allowlist + weak signals) | `junk_filter.py` (pure Python, 240 LOC, tested 30+ cases) | `BLOCKED_DOMAINS` substring set + dead `VISUAL_ALLOWLIST` (P1-16) | Replace/augment with tested classifier |
| **Fullsize data-attribute priority** (`data-hi-res-src`, `data-fullsize`, `data-maxres`, `data-retina`…) | `webpage_parser._get_best_image_url` (15+ attrs, priority scoring, srcset w/x) | `extract_fullsize_images` regex already lists most attrs, but no priority order / x-density | Small: prefer hi-res attr when several present |
| **`normalize_url`** (strip utm/fbclid/gclid, sort query, collapse repeated path segments) | `utils.py::normalize_url` (tested) | `orchestrator._dedup_key` (strips only `?m=`) + wrapper `seen_page` (raw) | Better dedup; also collapses `threads/threads/threads` bloat |
| **429 Retry-After backoff** (seconds or HTTP-date) | `webpage_parser._get_content` | `_check_url_live` treats 429 as hard fail | Small, real perf/coverage win |
| **Binary-media guard** (Content-Type image/video → skip HTML parse) | `webpage_parser._get_content` | `_check_url_live` fetches full body | Avoids parsing megabytes of binary |
| **Consent-cookie static extraction** (from onclick JS, incl. inline fn bodies) | `_extract_consent_cookies_from_js` / `_find_inline_function_body` | `_strip_block_overlay` (removes overlay markup only) | Partial win for age-gated sites |
| **Bot-trap / hidden-element defense** | `_is_element_visible` | none in image extraction | Optional |
| **Priority URL queue** (media ×25, path similarity, same-gallery child/sibling) | `priority_url_queue.py` | linear pipeline, no site crawling | **Not applicable** — we don't crawl site trees |
| **Deno JS engine** (happy-dom workers) | `js_engine/` | none | **Not applicable** — external binary, fail-open only; keep Python converter path |
| aiohttp stack / downloader / GUI / task queue | `http_engine`, `media_downloader`, `gui/` | curl_cffi/httpx sync stack | **Not applicable** — different architecture |

### 14.2 Recommended integration plan (priority order)

**INT-1. Imagus Sieve static engine (high value, low risk, network-free)**
- Port a **slim `sieve.py`** into `plugins/web-tools/ddg/` containing: sieve JSON loading (domain-indexed +
  global rules), `img`+`to` regex substitution (`$n`→`\g<n>`), the JS→Python callable converter
  (no Deno), `#ext#` expansion, WordPress strip, dedup. ~250 lines, copied from `site_pattern_manager.py`.
- Ship `Imagus_sieve_2026.07.15_823.json` (920 KB) as `plugins/web-tools/ddg/resources/` and load lazily.
- Wire into `upgrade_to_fullsize()` (both copies) as a final `sieve.apply(url, source_url)` step returning
  the first transformed candidate, and into `extract_fullsize_images` output list. Keep old heuristics as fallback.
- `restore.ps1` must copy the JSON + module to `~/.hermes/`.
- Risk: low (fail-open — no rules → return url unchanged). Perf: index by domain, compile once.

**INT-2. Junk filter (high value, low risk)**
- Copy `junk_filter.py` (or a trimmed variant) into `plugins/web-tools/ddg/`.
- Use `is_ad_url()` in `_should_filter_url` (ddg_search.py:1828) and in `_filter_images_for_report`
  (skip ad-host images); use `should_skip_junk_url()` for Level-2 expansion candidates.
- Replace dead `VISUAL_ALLOWLIST` mechanism (P1-16) with the allowlist file approach.
- Ship `junk_allowlist.txt`; `restore.ps1` copies it.

**INT-3. link→url→res fullsize discovery for visual queries (medium value, medium risk)**
- Add `get_link_rule`/`apply_link_url_transform`/`extract_res_urls` (from `site_pattern_manager.py`)
  into the sieve module; implement a sync `discover_fullsize(thumb_url, page_url)` using the existing
  curl_cffi `_fetch`, bounded by a per-run time budget (pattern: `FULLSIZE_DISCOVER_CONCURRENCY`/
  `_TIME_BUDGET`).
- Call it from `_deep_read_and_extract` only for `query_type == "visual"` when an extracted image URL
  is a thumbnail whose page context is known.
- Risk: medium — extra network calls; mitigation: budget + only for visual, only when sieve has a
  matching `link` rule (no rule → skip instantly).

**INT-4. `normalize_url` + Retry-After + binary guard (small, safe)**
- Port `normalize_url` into `_common.py`; use for dedup in orchestrator/wrapper.
- In `_check_url_live`: parse `Retry-After` on 429 (sleep+retry once), and short-circuit binary
  Content-Type (don't GET body / don't text-scan binary).

**INT-5. Fullsize data-attribute priority + consent-cookie extraction (small)**
- In `extract_fullsize_images`, when several data-* candidates exist for the same image, prefer
  hi-res attrs (`data-hi-res-src`/`data-full`/`data-maxres`/`data-original` over `data-src`).
- Optionally port `_extract_consent_cookies_from_js` into `_fetch` (ddg_search) as a last-resort
  retry for age-gated pages.

**INT-6. NOT recommended** (documented for awareness): priority URL queue, Deno engine, aiohttp
stack, downloader/GUI — different architecture; porting costs exceed value for a search pipeline.

### 14.3 Decisions needed from the user
1. Which INT items to implement (1-5 above), and in which batch order.
2. INT-1: use the newest sieve file (2026.07.15, 823 rules) as the shipped default?
3. INT-3: OK to add extra network fetches for visual queries only?

### 14.4 Implementation status (2026-08-10)

User decision: implement **INT-1 + INT-2 + INT-4 + INT-5** with the newest sieve file
(2026.07.15, 823 rules). INT-3 (link→url→res, extra network fetches) was left
unimplemented — documented as pending decision. Commit `71d1fdf` (+ review follow-up):

- **`sieve.py`** (new, ~430 LOC): Imagus static engine ported from `site_pattern_manager.py`
  — sieve JSON loading (domain-indexed + global + full fallback scan), `img`+`to` regex
  substitution, JS→Python callable converter (no Deno; ~336/823 rules usable, 158 JS-skipped
  fail-open), `#ext#` expansion, WordPress size-suffix strip, fail-open on missing file,
  thread-safe lazy singleton. Wired into `upgrade_to_fullsize(url, source_url)` in BOTH
  `ddg_search.py` and `visit_website_enhanced.py` (applied last, heuristics as fallback);
  orchestrator passes `source`/`source_page`.
- **`junk_filter.py`** (new): precision-first ad/tracker classifier (host suffix + path token +
  weak banner-size signal + allowlist file + fail-open), ported unchanged. `is_ad_url` in
  `_should_filter_url` (search results) and `_filter_images_for_report` (pre-download);
  `should_skip_junk_url` (forum chrome) in Level-2 expansion only (orchestrator + wrapper
  `_safe_expand`) — NOT on search results (review finding #3).
- **`_common.py`** (new): `normalize_url` (strip utm/fbclid/gclid, sort query, drop fragment,
  collapse repeated path segments) + `base_domain`. Used for orchestrator `seen_urls` dedup
  and wrapper per-source quota (replaces local `_base_domain` copy).
- **`_check_url_live`**: `_parse_retry_after` (429 backoff, seconds or HTTP-date, bounded) +
  binary Content-Type guards (skip HTML parse for image/video/audio payloads).
- **`extract_fullsize_images`** (both copies): data-* attributes sorted so hi-res hints
  (data-hi-res-src/data-fullsize/data-maxres/data-original/…) win the later dedup.
- **`restore.ps1`**: copies `sieve.py`, `junk_filter.py`, `_common.py`, sieve JSON,
  `junk_allowlist.txt`; compile-checks the new modules.
- **Tests**: `test_sieve.py` (17), `test_junk_filter.py` (15), `test_common.py` (10) —
  **75/75 pass** across all suites, all modules py_compile clean. Network smoke OK.

Review findings applied (commit `71d1fdf` follow-up): (1) `transform_candidates` now
accumulates candidates from ALL matching rules instead of stop-on-first-match;
(2) `_common.base_domain` wired into the wrapper (no dead code); (3) junk-transition
rules moved out of `_should_filter_url` into Level-2 expansion only; (4) thread-safe
sieve singleton init.

Open (not implemented): INT-3 fullsize discovery chain (needs user OK for extra
network fetches on visual queries); unused `sieve.candidates()` public API (kept,
used by tests); `$&` in `to` templates unsupported (same as source project).

---

### 14.4 Remaining open items (from §11 / §8 / §14)

- `search_deep` results now sorted by relevance (NEW-7 fixed); report evidence size cap (NEW-8) — product call, not changed.
- Standardize `_relevance_score` vs `content_relevance_score` (NEW-10).
- Unify proxy defaults across modules (NEW-13).
- Dead code cleanup (P1-15: unused `_search_*` strategies, `fetch_page`, `compose.py`, `synthesize_answer`, `VISUAL_ALLOWLIST`).
- Thread-safety of shared curl_cffi sessions (P1-23), `verify=False` (P4), restore.ps1 process killing (P4).
- Integration items INT-1..INT-5 from §14 (pending user decision).

---

## 15. Test-run analysis — `standalone/logs/research_2026-08-10_190748.log` (2026-08-10)

Query: "Bikini girls image gallery, bottomless bikini pics, …" → `query_type: general`.
Run: 383.8s total, 4 variants → 154 URLs → 132 kept → 100 validated → **18 alive**,
12 dead, 67 blocked, 14 domain-blocked. Level-2: +10 pages. Deep-read: 4 pages,
30 raw images → 10 unique (all from ONE junk SEO page). Evidence: 3 pages.

### 15.1 Confirmed bugs (reproduced against code)

**B1 — `_detect_blocked()` false-positives on the word "captcha" (ROOT CAUSE of 67/100 blocked).**
Reproduced: `commons.wikimedia.org` and `scrolller.com` → HTTP 200, real HTML,
but `blocked=True, error="blocked (captcha/cloudflare/etc)"` even with a working proxy
(proxy verified alive: direct AND via-proxy curl both 200). The rule
`'captcha' in html and '<form' in html → blocked` fires because MediaWiki pages
carry `wgConfirmEditCaptchaNeededForGenericEdit:"hcaptcha"` in JS config + a search form.
Any JS-heavy page mentioning captcha in config (WordPress/MediaWiki) is condemned.
Domino effect: orchestrator's domain quarantine (2 blocks → skip whole domain)
then banned `wikimedia.org`, `scrolller.com`, `shutterstock.com`, `dreamstime.com`
etc. — 14 domains, several of them fully legitimate.
Fix: require real challenge markers (cf-chl-*, challenge-platform, px-captcha,
"verify you are human", hcaptcha iframe/widget, recaptcha+verify) instead of the
bare word; drop the `'<form'` heuristic. Also drop `'страница не найдена'`
(plain 404 text, not a block) from indicators.

**B2 — Naive registrable-domain: "BLOCK DOMAIN: co.uk" and `_dedup_key`/`base_domain`.**
`_validate_urls._base_domain` (orchestrator, ~line 315) and `_common.base_domain`
take the last two labels → `markhewittphotography.co.uk` → `co.uk`; after 2 blocked
`.co.uk` pages the whole TLD is quarantined (and dedup keys merge unrelated hosts).
Same naive logic in `_dedup_key` and the wrapper's `_base_domain`. Affects
`co.uk`, `co.za`, `com.au`, `co.jp`, `com.br`, …
Fix: shared `registrable_domain()` in `_common.py` with a public-suffix table,
used by orchestrator validation/dedup, wrapper, and junk filter `_third_party`.

**B3 — Evidence/image relevance re-scored on truncated snippets (rel=1.00 → 0.00).**
Deep-read: `telegra.ph` rel=1.00 (text=1.00, imgs=20, kw=✓), `autoadult` rel=1.00.
Evidence selection (Step 9) and image gate (Step 8) recompute
`content_relevance_score(query, snippet)` on `snippet[:500]` / DDG snippet instead
of using the deep-read score → both pages drop to 0.00, are skipped from evidence,
and their 20 images are discarded as "from irrelevant pages". Result: the report
kept 10 images, ALL from the lowest-relevance page (`bringmetotheocean.ru` rel=0.44).
Fix: store `p["deep_score"]` in `_deep_read_and_extract` and reuse it in Steps 8/9
(keep the img_bonus logic consistent).

**B4 — `&amp;` not unescaped in image URLs.**
Log: `https://i0.wp.com/img.index.hu/…/BIG_0017096802.jpg&amp;ssl=1`. `extract_fullsize_images`
returns raw attribute values; the literal `&amp;` breaks download and the markdown
`![](...)` link. Fix: `html.unescape()` on extracted URLs before dedup/filter.

**B5 — `vk.ru` missing from BLOCKED_DOMAINS** (`vk.com`/`ok.ru` present).
`vk.ru/album-…` and `vk.ru/topic-…` passed the blocklist and were validated (HTTP 418).

### 15.2 Quality issues (documented, product-level decisions)

**Q1 — Query-type classification:** an explicit image-gallery query ("…image gallery,
…bikini pics…") was classified `general`. Cascading cost: no img_bonus (validation
logged `imgs=0` everywhere even for gallery pages), relevance thresholds 0.15
instead of 0.05, no image size/dedup filter, no Gallery Links section, no visual
allowlist (pinterest/flickr/imgur would have been kept). Worth a prompt/example
review in `classify_query_type`.

**Q2 — SEO "keyword-soup" spam in search results:** ~70/154 URLs are
`bottomless+bikini+pics`-style keyword-stuffed pages across hundreds of throwaway
domains. `junk_filter.is_ad_url` correctly does NOT block them (hosts aren't
ad networks), but they waste the 100-slot validation budget. Consider a
keyword-soup detector (path is mostly query words joined by `+`/`-`) at Step 3.

**Q3 — Level-2 candidates quality:** 125 candidates → 16 alive → 10 added;
15/16 alive were a single domain (`xxgasm.com`) and `xxgasm.com/report-abuse/`
passed the expansion filter. Consider capping Level-2 per dedup key more tightly
and adding utility-page paths to the junk-transition rules.

**Q4 — Variant display:** all four variants print the same first 60 chars
(`q[:60]`) — display-only, not a bug, but hides what actually differs.

**Q5 — Leftover temp file `_tmp_replace_check_url_live.py` in repo root** — cleanup.

### 15.3 What the run proves works

Filters worked: 5 blocked (t.me/vk.com/ok.ru/facebook), 7 homepages, 7 search-URLs,
2 video, 1 service — all correctly classified. Blocklist + homepage/search/service
filtering is solid. `_check_url_live` HEAD→GET flow, binary-media guard, deferred
503 handling, and relevance sorting all behaved as designed. Proxy itself was
reachable (verified after the run); the 81/81 "proxy failed" markers were a side
effect of B1 (nothing reached the proxy because direct pages already "looked
blocked"), not a proxy outage.

### 15.4 Implementation status (2026-08-10, commits after `71d1fdf`)

| Item | Status | Commit |
|---|---|---|
| B1 block detection (captcha-config false positive) — `_detect_blocked` + `vwe._is_blocked`: real challenge markers only (hcaptcha widget/iframe, recaptcha+verify), dropped bare `'forbidden'`/`'страница не найдена'` | ✅ verified end-to-end: commons.wikimedia & scrolller (blocked in the log) now ALIVE, HTTP 200 | `ac2775e` |
| B5 `vk.ru` added to BLOCKED_DOMAINS | ✅ | `ac2775e` |
| Q2 keyword-soup (`+`-joined query words) filter in Step 3 | ✅ 7/8 real spam URLs flagged, hyphen galleries untouched | `ac2775e` |
| Q5 leftover `_tmp_replace_check_url_live.py` removed (was git-tracked) | ✅ | `ac2775e` |
| B2 public-suffix `registrable_domain` (`_common.py`) wired into `_validate_urls`, `_dedup_key`, wrapper, junk-filter third-party | ✅ `markhewittphotography.co.uk` ≠ `co.uk` | `398e8bb` |
| B3 `deep_score` stored in deep-read; Steps 8/9 reuse it (no snippet re-scoring) | ✅ | `398e8bb` |
| B4 `html.unescape()` on extracted image URLs (both copies) | ✅ | `398e8bb` |
| P1-23 thread-local curl_cffi sessions (`_reset_sessions()` replaces `_sessions.clear()`) | ✅ | `c54dab9` |
| NEW-13 unified proxy defaults for vwe (env/file, mirrors ddg_search) | ✅ | `c54dab9` |
| P4 `verify=False` → configurable `DDG_TLS_VERIFY=1` (default unchanged) | ✅ | `c54dab9` |
| P4 restore.ps1 stops only hermes/`gui_launcher` or python/node with `hermes` in cmdline | ✅ | `c54dab9` |
| Q1 classifier: visual now includes explicit gallery/image/pics/photos keywords | ✅ (needs an LLM-server re-run to confirm) | `c54dab9` |
| Q3 Level-2 pre-filter: utility-path tokens + dedup-key cap (2/domain) before validation | ✅ | `c54dab9` |
| Dead code: `synthesize_answer` removed; `_search_*`/`compose`/`fetch_page`/`image_search`/`VISUAL_ALLOWLIST` KEPT (reachable via CLI/tests/search_deep — not dead) | ✅ | `c54dab9` |

Tests: 83/83 pass (75 prior + 8 new: registrable-domain incl. IPv4, detect_blocked
precision incl. cdn-cgi, unescape). All modules py_compile clean. Still open
(product decisions): INT-3 fullsize discovery chain (extra network fetches),
NEW-8 report size cap.

Code-review findings applied (`6732116`): (1) Level-2 utility-path filter narrowed
to unambiguous system tokens AND first-path-segment only (content words like
'about'/'help'/'press' removed — no more false skips of legit galleries);
(2) bare `cdn-cgi` no longer counts as a block — requires challenge context
(`cf-chl`/`challenge`); (3) IPv4 hosts guarded in `registrable_domain`
(192.168.0.1 stays itself).

