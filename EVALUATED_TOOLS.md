# Evaluated alternatives — donsetch, ratsearch, Moli, crawl4ai, maigret, reddit-dl, untidetect-tools

Status: evaluated 2026-08-30 (DonSeTch, ratsearch), 2026-09-02 (Moli),
2026-09 (five-repo reuse review: donsetch, crawl4ai, maigret, reddit-dl,
untidetect-tools — see §4, plus the donsetch deep dive in §5 and the MCP
notes in §6).
Recorded so the agent knows these projects exist and what is worth porting
into our own web tooling (webtools_bridge + pipeline).

## Why we evaluate external projects at all

Our goal is not to copy random scrapers. It is: give the Hermes/DSH agents
**eyes and hands** on the web — read any page, find relevant sources, survive
anti-bot walls, all without API keys and without a heavyweight headless-browser
dependency. External projects are a **source of ideas** (and occasionally a
drop-in upgrade) for that goal. The agent that uses the tools makes the call on
what to adopt.

## 1. DonSeTch — github.com/dondai44423/donsetch (Rust MCP server)

**What it is.** A single ~36MB Rust binary implementing 3 MCP tools for any AI
agent: `web_fetch`, `web_search`, `web_crawl`. Zero API keys. The pitch: "The
web, for AI agents." Everything built from scratch (own HTTP/2, own extraction,
own PDF parser, own search aggregator).

**Standout capabilities (verified on this machine, v3.4.3):**
- **Real Chrome TLS** — drives Chrome's own BoringSSL natively; ClientHello IS
  Chrome's (verified JA4 + Akamai h2 fingerprint + HPACK). Our curl_cffi is a
  *simulated* table; DonSeTch's is the real engine.
- **Keyless search** — 10+ engines in parallel, fused by cross-engine consensus
  + local semantic rerank. (verified working: `donsetch search "rust async
  patterns"` → 3 results, engines brave/ddg/yahoo.)
- **Solve-and-bounce** — headless browser solves a JS challenge, harvests
  cookies, hands them to the fast TLS tier, goes back to sleep. Browser almost
  never fetches content. Solves Cloudflare/DataDome/PerimeterX/Akamai walls.
- **Probe mode** — `must_contain` verifies a claim against the full page and
  returns MATCH/NO-MATCH + up to 3 excerpts (~60 tokens instead of a 4k fetch).
- **Reference handles** — links rendered as `[text](L12)`, results as `S1..Sn`,
  `fetch S3` just works. URLs cost ~80 tokens, handles ~3.
- **Resurrection fetch** — `archive=auto` serves nearest Wayback snapshot,
  honestly labeled with age (we already have wayback fallback — same idea).
- **Stable error codes** — `wall.challenge`, `guard.ssrf`, `deadline.hit`,
  `archive.stale` — branch on codes, not prose.
- **Page memory** — fetch fingerprints; re-fetch reports `changed` with
  section diffs; `since_last=true` collapses re-check to ~30 tokens.
- **Domain intelligence** — Reddit, npm/PyPI/crates, GitHub, Stack Overflow,
  Wikipedia, docs sites restructured from keyless surfaces, labeled
  `via=adapter:...`, kill-switchable.
- **Browser actions** — type/click/press/scroll up to 16 steps inside `fetch`
  (for search flows / load-more / form submits).

**Honest limits (theirs):** no login sites; no ML-DSA post-quantum (BoringSSL);
search rate-limits without a proxy; not built for mass scraping.

**Verified limitation: does NOT solve Cloudflare JS challenges.** DonSeTch's
tier-1 real-TLS fetch can reach many Cloudflare sites (the "behind the wall"
content), but when the site requires a full JS challenge (cf_clearance cookie),
the solve-and-bounce browser tier is needed — and that tier is blocked in the
DSH sandbox (`OpenProcess failed: 5`). Outside the sandbox, the browser tier
would work, so DonSeTch remains a credible upgrade for unsandboxed use.

**Why we did NOT adopt it outright:** in our current DSH sandbox the headless
browser tier fails (`OpenProcess failed: 5`, cannot create cache under
`%LOCALAPPDATA%` because the sandbox blocks it; `dirs` crate reads the real
Windows API path, not `$env:LOCALAPPDATA`). Without the browser tier it is
"just another fetch/search" — no better than our bridge. **Outside the sandbox
it is a credible replacement / upgrade for webtools_bridge** (single binary,
real TLS, consensus search, PDF/OCR). Re-evaluate if we ever run it unsandboxed
or via Docker (it ships a Dockerfile + compose).

## 2. ratsearch — github.com/gbkorr/ratsearch (shell scripts)

**What it is.** ~100-line POSIX scripts for local-LLM RAG, coded by hand:
- `ratsearch.sh` — interactive chat with a local llama-server; model reads
  Wikipedia articles from an **offline .zim archive** via `zimdump list/show`
  + `html2text` + `fzf`.
- `batsearch.sh` — one-shot: model performs web searches via `w3m + duckduckgo`
  and returns answers to stdout.

**Value for us:** not as a search layer (no validation/relevance/scoring), but
**the offline-Wikipedia-via-.zim idea** is a real asset: a free, deterministic
fact-fallback when live engines are down/rate-limited. `zimdump list` over a
`wikipedia_en_all_nopic.zim` gives a full title index; `zimdump show --idx`
gives article HTML → text. Cheap to add as another "backend" in our search.

## 3. Moli — github.com/lexmount/moli (Rust headless browser)

**What it is.** Production-ready headless browser for AI agents. A complete
browser runtime (V8, DOM, CSS, network, storage) in ONE Rust binary (~97 MB),
structure-first: layout/pixels are generated only on demand. Use via CLI
(`moli fetch --dump markdown ...`), CDP, WebDriver Classic, or WebDriver BiDi.

**Standout capabilities (verified on this machine, v1.1.1; binary updated
to v1.1.2 on 2026-09-04 — DOM/CSS/CDP bugfixes + perf: V8 pointer compression,
deferred response copies; `fetch --dump markdown` and `--dump semantic_tree_text`
output formats re-verified unchanged, `mfetch` bridge path re-verified OK):**
- Real V8 + network stack — executes **external** `<script src>`, so it renders
  SPAs (React/Vue/Next.js CSR sites that ship an empty `<div id="root">`) that
  our Deno happy-dom worker (inline JS only, sandboxed no-net) cannot extract.
- Structure-first: `moli fetch --dump markdown` skips layout/paint entirely for
  text extraction; screenshots only on demand. Median RSS 73 MiB vs 773 MiB
  Chrome Headless; ~15% of Chrome's CPU on agent workloads (Lexbench).
- Full protocol surface: CDP (`Playwright.connectOverCDP` works), WebDriver.
- Output formats: HTML, Markdown, JSON, semantic text tree, screenshot, PDF.
- Honest limits: no GPU/WebGL/Canvas parity, no pixel-perfect Chrome; 81.9%
  task success (Chrome 99.8%) on Lexbench-Headless-Browser; login walls remain.

**⚠️ Verified limitation — does NOT solve Cloudflare/AWS WAF challenges.** We
tested `moli fetch --dump semantic_tree_text --wait-until done` on
stackoverflow.com: it returns the «Just a moment...» challenge interstitial,
identical to curl_cffi — no `cf_clearance`/Turnstile solving in `fetch` mode.
Same for AWS WAF (IMDB). So Moli is a **SPA renderer, not an anti-bot bypass**;
for challenged pages the read ladder's wayback fallback stays the answer.

**Why we adopted it as a render tier (not as a replacement):** the DSH sandbox
blocks Moli's HTTPS stack in the default mode (HTTP works, TLS fails <50 ms —
same sandbox class as the git schannel issue). **BUT with the standard
`sandbox_permissions: danger-full-access` escalation on the pwsh call, Moli's
HTTPS works inside the sandbox too** (verified: `mfetch` → `source: moli`,
`https://example.com` returns real content). This is the designed DSH approval
mechanism, not a hack — same level as the git token fix. We vendored the binary
(`moli/moli.exe`, gitignored) and wired it as `mfetch` / the tier-2 escalation
in `cmd_render` — fail-open: any Moli failure falls back to the plain read
ladder. Works with `--proxy` for geo/rate blocks; needs the NecoBox proxy in
proxy mode for HTTPS from the sandbox host.

## What we are porting into webtools_bridge

Priority-ordered. Implemented items are marked.

- [x] **Challenge detection & honest signal** — detect anti-bot interstitial
  text (Cloudflare/AWS WAF markers) even when long; `challenge: true` in read
  output; wayback fallback fires on challenge shells. (already shipped: 96402f6)
- [x] **Wayback resurrection** — nearest snapshot fallback with `wayback_ts`
  label. (shipped in v2)
- [x] **Probe mode** — `probe` subcommand: URL + claim → MATCH/NO-MATCH + up to
  3 excerpts. Token-cheap verification. (shipped: see bridge `cmd_probe`)
- [x] **Stable result flags** — `blocked: "cloudflare" | "aws_waf" | "recaptcha" |
  "login" | "generic"` alongside `challenge: true` so agents branch on codes.
  (shipped: `_challenge_kind` / `_is_login_gate`)
- [x] **Reference handles** — compact `S3`/`L12` handles in search/read output
  to cut token cost for long agent sessions. (shipped: `handle` fields)
- [x] **Moli full-browser tier** — vendored `moli/moli.exe` (gitignored) as
  `mfetch` subcommand + tier-2 escalation in `cmd_render` (Deno → Moli → read
  ladder). Real V8, external scripts, JS challenges; fail-open in the sandbox
  where Moli's HTTPS is blocked. (shipped: `cmd_mfetch`, `_moli_fetch_call`)
- [ ] **Cross-engine consensus in search** — tag each result with which engines
  surfaced it; prefer multi-engine agreement in relevance. (needs per-engine
  provenance from the ddgs backend — deferred)
- [ ] **Offline Wikipedia (.zim) backend** — optional offline fact layer.
- [ ] **Page-memory / since_last** — fetch fingerprint + diff (lower priority).

Candidates surfaced by the five-repo review (§4/§5); wall memory shipped 2026-10:

- [x] **Persistent wall memory + backoff** — disk-backed per-host
  `verdict/rung/boxed_until` beside the bridge cache, so a wall that beat us is
  not re-paid every run. donsetch-verified numbers copied (`BASE_DELAY=200 ms`,
  `MAX_BACKOFF_RUNG=6`, 7 s ladder cap, jitter 0.75–1.25, `rung+2` on 429/503,
  `rung-1` on success, 7 d TTL, 2000-host cap); the long wall-box ladder
  (15 m → 2 h cap) is our own tuning — donsetch's verified code has only the
  short throttle ladder. Shipped: `plugins/web-tools/ddg/wall_memory.py` +
  `webtools_bridge.py` wiring (read gate, mfetch notes, fail-open import,
  kill switch `HERMES_WALL_MEMORY=0`, tests in
  `plugins/web-tools/ddg/test_wall_memory.py`). Phase 2 shipped:
  pipeline integration via `_validate_one` (the single `_check_url_live`
  call site) — gate + notes live in two module helpers (`_wall_gate_check`,
  `_wall_note_check`), the hot `_check_url_live` is untouched; 429/503 →
  `note_throttled` (donsetch rung semantics, finally wireable — the pool
  sees HTTP status), 403/451/challenge-shells → `note_wall`, alive →
  `note_ok`, transport failures and 404s → no state. `wall_memory.py` added
  to the restore manifest + compile targets. Deep-read (orchestrator) is
  deliberately NOT gated: it only fetches URLs that just passed validation.
  Tests: `plugins/web-tools/ddg/test_wall_pipeline.py`.
- [x] **Cookie file support** — `--cookies cookies.txt` (Netscape) plumbed into
  the previously unused `_fetch(cookies=...)` seam. Shipped: parser +
  RFC 6265 matcher in `_common.py` (`parse_netscape_cookies`,
  `cookie_header_for`: domain/path matching, `Secure` never over HTTP,
  longest-path-first, 50-pair/4096-byte caps — donsetch jar semantics),
  `visit_website(..., cookies=None)` forwards to the direct curl_cffi rung
  only (Jina/Wayback never see site cookies), bridge flag `--cookies` wires
  read/probe/render/mfetch. `#HttpOnly_` prefix honored, BOM-safe, fail-open.
  Verified live end-to-end against a local echo server: the direct fetch
  carries the cookie (`bridge_probe=ok123` round-tripped). Known pre-existing
  quirk: htmldate's `find_date(url, html)` refetches the URL via urllib3 for
  date metadata WITHOUT user cookies — metadata-only, fail-open, no leakage;
  phase-2 gating will cover both requests. Tests:
  `plugins/web-tools/ddg/test_cookie_file.py` (16 passed).
- [ ] **Browser→HTTP cookie handoff (solve-and-bounce)** — harvest clearance
  cookies after a Moli solve and replay them in the HTTP tier. Needs a spike
  first (§5d): JSON output carries no cookies, `--eval` cannot see HttpOnly, and
  `--profile-dir` persistence fails in the sandbox — so this must be prototyped
  in the standalone chain, and the CDP path is UNCERTAIN.
- [x] **Feed/RSS extraction** — first-class RSS 2.0 / Atom / JSON Feed path
  shipped in `visit_website_enhanced.py`: `_is_feed` conservative first-512-char
  body sniff (no content-type — `_fetch` returns body only; XHTML excluded,
  late `<rss` mentions in code samples do not fire), `_feed_to_page` renders
  items as readable text with the `<link>`→`<rsslink>` rewrite ([donsetch]
  verified hack — void `<link>` drops every RSS item URL in HTML parsers),
  dates kept raw, tags stripped, `_FEED_MAX_ITEMS=60` [donsetch] +
  `_FEED_SUMMARY_CAP=700` [ours], fail-open to the normal ladder. Applies to
  the direct fetch rung only. Verified live through the bridge on
  hnrss.org/frontpage: `source=feed`, 8000 chars, 20 item links. Tests:
  `plugins/web-tools/ddg/test_feed_parse.py` (10 passed, incl. the
  void-`<link>` regression case).
- [ ] **Conditional revalidation (304)** — `If-None-Match`/`If-Modified-Since`
  for the strong-read (6 h) and search (1 h) cache tiers only; skip
  `Vary: *` / `no-store|private`, clamp `max-age` to ≤3600 s.
- [x] **Authority + coverage penalties** — official/depth multipliers and
  entity/version/year coverage penalties, shipped as a SORT-ONLY placement
  factor in `ddg_search.py` (`_authority_coverage_factor`, attached as
  `authority_factor`, final sort uses `relevance × factor`): official host
  ×1.6, docs-shape when docs-seeking ×1.15, paper host when paper-seeking
  ×1.35, phrase-in-title ×1.15 (all [donsetch] verified); anchor/version/
  year coverage misses ×0.3 ([donsetch] verified). Relevance VALUES and
  every threshold (relevant_alive, aspects, compose) untouched by design.
  [ours] adaptations: small unambiguous official-host seed table;
  docs/paper word lists bilingual (RU/EN); version/year evidence is the
  short window (title+snippet+URL) to dodge footer noise; anchors check
  the page text too. Honest gaps: donsetch's news-freshness tiers are NOT
  portable (DDG snippets carry no dates; htmldate arrives post-ranking) —
  documented, skipped. Kill switch `HERMES_AUTHORITY=0`. Tests:
  `plugins/web-tools/ddg/test_authority.py` (incl. the deterministic
  before/after ordering comparison).

## 4. Reuse review (2026-09; tinyfish-cookbook added 2026-10)

Asked: of `thezveroboy/reddit-dl`, `TheGP/untidetect-tools`,
`unclecode/crawl4ai`, `soxoj/maigret`, `dondai44423/donsetch` — what is worth
using in our project? Verdicts, with the reason:

| Repo | Licence | Verdict |
|---|---|---|
| `dondai44423/donsetch` | AGPL-3.0 | **Ideas only — richest source.** Code must NOT be copied (see §5). |
| `unclecode/crawl4ai` | Apache-2.0 | **Already harvested.** Tier-1/Tier-2 ideas ported (sitemap seeding, best-first, aspect coverage, PDF, tables, head metadata). Remaining features are browser-tier (rejected) or redundant with ours. Only MCP was new → §6. |
| `soxoj/maigret` | MIT | **Not adopted.** Far from deep search: it is username→account OSINT over ~6k sites. Its genuinely interesting technique is *false-positive suppression* (`error_detection.py`: site-specific `fail_flags`, then generic bot-protection wording, then status — 403/401/429/999/5xx, with 403 never meaning "username free"). We already have the equivalent shape in `_detect_blocked` + challenge detection, and the 6k-site DB would add noise, not signal. |
| `thezveroboy/reddit-dl` | none | **Not adopted.** Single-site media downloader. Its one transferable pattern is a user-supplied `cookies.txt` (Reddit caps anonymous requests at 25 posts) — same idea as donsetch's login/cookie vault and the fix for our unused `_fetch(cookies=)`. |
| `TheGP/untidetect-tools` | none (a list) | **Not adopted.** Curated catalogue, no code: commercial anti-detect browsers, paid captcha solvers, SMS-activation. Conflicts with our zero-API-key, no-account stance; Tier 3 (Playwright/undetected browser) is permanently rejected. Reference only. |
| `tinyfish-io/tinyfish-cookbook` | MIT | **Service skipped, two prompt-level patterns taken.** The TinyFish API itself is paid/keyed/cloud — same verdict as crawl4ai Cloud MCP. Taken instead: the **doctor-skill pattern** (shipped as the Doctor section in `skills/hermes-web-tools/SKILL.md`; verified by a full end-to-end run, 52.5 s, all checks green, zero recipe friction) and the **research-landscape recipe** (shipped as `skills/research-landscape/SKILL.md`; validated by a live 4-facet investigation — 4/4 facets covered with exactly one bounded retry each, 2 keystone numbers probe-verified, 5 "found live" refinements folded back into v1.1). Their Vault concept additionally confirms our `--cookies` direction. |
| `enzo-adami/hermes-plugin-web-local` | MIT | **Plugin not adopted; the find of the batch.** It led us to Hermes' own 28 KB `tools/url_safety` SSRF module (in the live framework install, absent from this repo) — our bridge had **zero SSRF checks** on agent-supplied URLs. Shipped: std-only `_ssrf_gate` on the read/render/mfetch entries (loopback/private/link-local/CGNAT 100.64/10/reserved ranges, cloud metadata via 169.254.0.0/16; literal IPs without DNS, hostnames via every A/AAAA record; fail-open everywhere; kill switch `HERMES_SSRF_GUARD=0`; blocked results not cached). Honest limits accepted: no per-redirect-hop rechecks, no DNS-rebinding defense — transport-level work (Hermes' module does it; not vendored). Also took its charset lesson: the httpx rung no longer loses the body on a malformed charset header. |
| `Adolanium/hermes-jina` | MIT | **Plugin not adopted** (we need auto-fallback in the ladder, not explicit opt-in). Took the operational fact: **anonymous Jina concurrency = 2** → `_JINA_SEMAPHORE(2)` now caps our fallback rung (the deep-read pool runs 3 workers; exceeding the cap burned rungs on 429s). Plus the honest third-party disclosure — blocked/weak URLs go to `r.jina.ai` — now in the skill's limitations. Their "search needs a key" confirms we never touch Jina search. |
| `ry0narushima/bettersearch` | MIT | **Service skipped**: built on DeepSeek Search API (keyed/paid) — the exact gap our zero-key bridge exists to fill. Its prompt methodology (roles/query-craft/fact-checking, ~27 KB of references) noted as an optional future pattern source, same category as tinyfish's landscape template. |

Cross-cutting conclusion: the only real capability gaps these repos revealed
are (a) browser→HTTP **cookie handoff**, (b) **persistent wall memory +
backoff**, (c) **feed/RSS** extraction, (d) conditional **revalidation (304)**.
No repo is worth a dependency; all four are worth re-implementing.

## 5. donsetch deep dive — verified mechanics (idea source, AGPL: do not copy code)

Read from a scratch checkout in `.refs/donsetch/` (gitignored). Constants
verified in source; the checkout was PARTIAL (no `src/ghost/mod.rs`,
`src/ghost/cache.rs`, `src/adapters/mod.rs`, no `Cargo.toml`), so a few
mechanisms are inferred from call sites and tested fixtures — marked UNCERTAIN.

### 5a. Solve-and-bounce (browser solves a wall, HTTP keeps the cookies)
- Oracle is **multi-signal, not cookie-only**: `small = html.len() < 30_000`
  and `marker_hit = walls::detect(...)` → challenged; a *clear* verdict needs
  `clear_streak >= 2` (two polls), plus a mid-navigation guard
  (`!url.starts_with("about:")` and `html.len() > 500`) so a blank page cannot
  fake a clear. Poll interval starts 200 ms → 500 ms after 5 s.
  (`src/ghost/ops.rs::solve`, `small_dom_settle_floor`, `can_hydrate`.)
- Clearance cookie names corroborate: `cf_clearance`, `datadome`, `_px3`,
  `ak_bmsc`, `bm_sz`, `reese84`.
- Harvest is **cookie-store side** (CDP), not `document.cookie` — that is
  precisely why HttpOnly cookies survive. Exact CDP method UNCERTAIN
  (`Network.getCookies` vs `Network.getAllCookies`).
- Jar limits: `MAX_COOKIE_BYTES=4096`, `MAX_COOKIES_PER_DOMAIN=100`,
  `MAX_COOKIES_TOTAL=3000`, `MAX_COOKIE_HEADER_PAIRS=50`,
  `MAX_COOKIE_HEADER_BYTES=4096`; attach sorts longest-path-first and skips
  expired / `secure && !https`.
- Safety invariant (`tests/it/secure_cookie_leak.rs`): a `Secure` cookie must
  never replay over plain HTTP — enforced at both ingresses and again at attach.
- **We already have the seam**: `visit_website_enhanced._fetch(url, referrer,
  cookies=None)` accepts cookies and no caller uses it; Moli can *import*
  Netscape cookies (`--cookie-file`) but cannot *export* them (§5d).

### 5b. Wall memory, backoff, pacing, revalidation
- Vendor fingerprints and the "require a co-signal" rule live in
  `src/detect/walls.rs` (`Vendor::{Cloudflare,DataDome,Akamai,PerimeterX,
  Imperva,Sucuri,Wordfence,Generic}`; scan window 256 KiB for 403/429/503,
  else 64 KiB). Pitfall worth copying: bare vendor prose in an *article*
  must not fire; `x-datadome` is present on ALL Forbes responses, so status
  decides; `challenges.cloudflare.com` alone is Turnstile's public embed.
- Persistent state: `governor_path() = paths::cache_dir()/crawl-governor.json`,
  `GOVERNOR_DISK_VERSION=1`, `GOVERNOR_HOST_CAP=2000`,
  `GOVERNOR_TTL=7*86_400 s`; row carries `rung`, `boxed_remaining_secs`,
  `crawl_delay_secs`; `next_allowed` is process-local and never serialized;
  saved only on `on_throttled`.
- Backoff ladder: `BASE_DELAY=200 ms`, `MAX_BACKOFF_RUNG=6`,
  `MAX_LADDER_WAIT=7 s`, jitter deterministic 0.75–1.25 (xorshift), delay =
  `base * 2^rung * jitter` capped at 7 s → rungs 0–6 ≈ 150 ms … 12.8 s→7 s.
  `on_throttled` (429/5xx): lane `rung+2`, host box `rung+1` which **bypasses**
  the 7 s cap; `on_error`: `rung+1`; `on_success`: `rung-1`.
- Revalidation (`src/fetch/revalidate.rs`): `if-none-match` / `if-modified-since`;
  store only 200 with body ≤ 8 MiB; skip `Vary: *` and `no-store|private`;
  TTL = `max-age` clamped to ≤ 3600 s; FIFO, `MAX_ENTRIES=512`,
  `MAX_TOTAL_BYTES=64 MiB`. On 304 they **re-score** wall detection (a bare
  304 was being scored as Blocked) and hard-error if no stored entry exists.
- ⚠️ **"Cookie-lifetime convergence" does not exist in the source.** Targeted
  greps for `lifetime|observed_lifetime|last_solved|converg` find only
  unrelated hits; the README claim is marketing. Do not re-implement a
  phantom algorithm. `MAX_AGE_SECS = 31*86_400` (`src/fetch/bypass.rs:180`)
  prunes `bypass-*.count` files — it is **not** a cookie rule.

### 5c. Search fusion, rerank, authority, coverage, feeds
- Fusion: RRF with `RRF_K=60.0` per *engine family* (max per family, then
  summed, so correlated engines cannot out-mass independents),
  `CONSENSUS_MULT=0.5` (`score *= 1 + 0.5*(n-1)`), `BM25_WEIGHT=0.3`,
  `PRIOR_WEIGHT=0.15`, `MAX_PER_DOMAIN=2`, vertical-only hits `×0.4`,
  `VERTICAL_WEIGHT=0.6`. Dedup key drops scheme, lowercases host, strips
  `www.`/trailing `/`/`index.html` and tracking params; a separate
  **syndication title key keeps punctuation** (C/C++/C# recall).
- Rerank: local cross-encoder `Xenova/ms-marco-MiniLM-L-6-v2`, ~23 MB
  quantized ONNX, sha256-pinned, `MAX_SEQ_LEN=512`, blend
  `final = 0.6·minmax(rrf) + 0.4·minmax(xenc)`, and it **fails open**
  (scores unchanged if the model is unavailable). We already have an optional
  guarded cross-encoder (`DDG_RERANK=1`), so only the blend + fail-open
  pattern is new; the download is avoidable.
- Coverage is a **penalty**, not diversity: missing hyphenated anchor ×0.3,
  version major mismatch ×0.3, year mismatch ×0.3 — pure regex, we have
  nothing like it.
- Authority: official-domain ×1.6 (×1.8 for code intent), docs-seeking ×1.15,
  paper authority ×1.35, phrase-in-title ×1.15, and a **news freshness table**
  (≤1 d 1.5, 2–3 d 1.3, 4–7 d 1.15, 8–30 d 1.0, else 0.85). Hardcoded; only
  per-engine trust is learned.
- Feeds: first-class extractor (`src/extract/feed.rs`) — detects by
  Content-Type (`rss`/`atom`/`feed+json`) or body sniff, supports RSS 2.0 /
  Atom / JSON Feed, `MAX_ITEMS=60`, rewrites `<link>` → `<rsslink>` before HTML
  parsing (void-element mangling), no date cutoff. **We have zero feed
  support** — this is a real gap for `news` queries.
- Sitemap/frontier: `<loc>` ≤2048, `<lastmod>` ≤64, `priority` seeds the
  frontier score, sitemap-index recursion, gzip cap 64 MiB, robots
  `crawl-delay` clamped to 60 s; frontier is a max-heap with a
  `WINDOW=4`, `WEIGHTS=[8,4,2,1]` head shaper (order-only jitter — every URL is
  still fetched).

### 5d. Moli cookie reality check (measured on this machine, Moli 1.1.x)
Empirically verified, because the handoff design depends on it:
- `moli fetch --dump json` returns only `final_url, status, title, headers,
  redirect_chain, html` — **no cookie array**; `--trace-network` also carries
  no `Set-Cookie`. So JSON output is NOT a harvest path.
- `--eval "document.cookie"` works but **cannot see HttpOnly**, so
  `cf_clearance` is invisible there. It also cannot be combined with `--dump`
  (`error: the argument '--dump <DUMP>' cannot be used with '--eval'`).
- `--cookie-file` (Netscape) is an **import** path and works (verified: an
  imported `test_cookie=probe_value` was replayed).
- `--profile-dir` writes `profile.json` declaring a JSON cookie backend at
  `partitions/default/cookies.json`, BUT profile init **fails every time in the
  DSH sandbox**: `failed to sync profile manifest parent directory … os error 5`
  (also with `danger-full-access`). The on-disk cookie file was never created.
  So the profile route is not usable here — and is no longer needed (next bullet).
- ✅ **RESOLVED — `moli serve` IS a real CDP endpoint, and CDP harvest works
  in-sandbox.** Measured (`Network.enable` → `Page.navigate` → cookie calls):
  - `/json/version` → `{"Browser":"Chrome/145.0.0.0","Protocol-Version":"1.3"}`;
    `/json/list` → a live page target `moli-default` with a
    `webSocketDebuggerUrl` (ws://127.0.0.1:<port>/devtools/page/moli-default).
  - `Network.getCookies`, `Network.getAllCookies` **and** `Storage.getCookies`
    all returned the **HttpOnly** cookie in full:
    `{"name":"cf_clearance","value":"…","domain":…,"path":"/","expires":…,`
    `"httpOnly":true,"secure":false,"session":false,"sourceScheme":…,"sourcePort":…}`.
  - `Runtime.evaluate("document.cookie")` returned only the non-HttpOnly cookie —
    direct proof that the CDP route is *required* for clearance cookies.
  - The whole spike ran inside the DSH sandbox over loopback HTTP, with **no
    `--profile-dir`** — so the `os error 5` blocker above does not apply to this
    route. Python clients are present: `websocket-client` 1.8.0, `websockets` 10.4.
- ⚠️ **The remaining gap is "solve", not "bounce".** Harvest + injection are now
  both verified feasible; what is still missing is a mechanism that *clears a
  real Cloudflare/DataDome challenge* to produce a clearance cookie worth
  harvesting. `moli fetch` does NOT do it (§3: returns the «Just a moment…»
  interstitial on stackoverflow.com, same as curl_cffi). A CDP-driven solve
  (click a Turnstile checkbox via `Input.dispatchMouseEvent`, poll for the DOM to
  clear, then harvest) is *expressible* with this endpoint and mirrors donsetch's
  click loop — but whether it defeats a live challenge is UNVERIFIED and needs a
  real-wall test outdoors.

## 6. MCP (Model Context Protocol) — reference notes for possible future use

**Status: notes only, not a priority and not implemented.** Recorded because
the bridge's seven subcommands are already tools, and MCP is the standard way
to expose them to hosts we do not own (Claude Code, Codex CLI, Cursor) instead
of maintaining a bespoke skill per agent. Our current consumers (Harness skill,
standalone CLI/GUI, Hermes plugin) do not need it.

### What we would expose
One MCP tool per subcommand — `search`, `read`, `image`, `expand`, `render`,
`probe`, `mfetch`. Start with three: `search` (discovery), `read` (evidence),
`probe` (verification — the token-economy win: MATCH/NO-MATCH + ≤3 excerpts
instead of an 8k-char page). `expand`/`render`/`mfetch` are escalation paths.

### donsetch reference (Rust; four tools)
Exactly `web_fetch`, `web_search`, `web_crawl`, `web_screenshot` — full input
schemas captured in `.refs/donsetch/tests/fixtures/tools_list.json`. Schemas
are generated from one shared spec table, so MCP and CLI cannot drift. Token
devices worth copying into any future schema: `must_contain`, `focus`,
`budget_tokens`, `tier` (auto/1/2), `resume`, `since_last`, `toc`/`section`,
`actions` (1–16 steps), `deadline_ms` ("honest error with next_action, never a
silent hang").

Transports: **stdio NDJSON** (one message per line, single writer task,
stdin EOF → graceful shutdown with no orphan browsers) and **HTTP/SSE**
(`POST/GET/DELETE /mcp`, `Mcp-Session-Id`, bearer token, loopback `Host`
checks against DNS rebinding, `Origin` refused unless loopback, `/health`).
Supervisor owns lifecycle; `--supervised` restarts after a panic. A
`compat.rs` probe detects which of `content`/`structuredContent` a client
actually shows and folds structured state into a leading `[meta]` block
(override `DONSETCH_MCP_TEXT_ONLY=1`). Errors: JSON-RPC codes only for
protocol faults; domain failures carry stable string codes
(`"code": "network.timeout"`) plus `next_action`.

Registration (from their README, verified):
```json
{ "mcpServers": { "donsetch": { "command": "donsetch", "args": ["mcp", "--supervised"] } } }
```
Without a global install: `"command": "npx", "args": ["donsetch", "mcp"]`.
HTTP instead of stdio: `donsetch mcp --http --port 8765`, token via
`DONSETCH_HTTP_TOKEN`.

### crawl4ai reference
Cloud MCP over HTTP (verified in its README):
```bash
claude mcp add --transport http crawl4ai https://api.crawl4ai.com/mcp \
  --header "Authorization: Bearer $CRAWL4AI_KEY"
```
Self-hosted Docker image `unclecode/crawl4ai:latest` also bundles MCP (needs
`CRAWL4AI_API_TOKEN`). Note: needs a key — unlike everything else we run.

### Python implementation shape
Two options, both Python ≥3.10 (versions verified on PyPI):
- official `mcp` SDK — **2.2.0** (v2 line; `pip install mcp` now installs 2.x.
  v1.x remains patched: pin `mcp>=1.28,<2` to stay on v1). Its own README
  example uses `from mcp.server import MCPServer` — **not** the FastMCP
  spelling.
- `fastmcp` — **4.0.10** (Apache-2.0, standalone Prefect project; FastMCP 1.0
  was folded into the official SDK in 2024).

FastMCP shape (type hints + docstring generate the schema):
```python
from fastmcp import FastMCP
mcp = FastMCP("hermes-web")

@mcp.tool
def web_verify(url: str, claim: str) -> dict:
    """Return MATCH/NO-MATCH plus up to 3 excerpts for one claim."""
    ...
if __name__ == "__main__":
    mcp.run()   # stdio by default; mcp.run(transport="http", host=..., port=...)
```
Auth exists only on HTTP transport (OAuth 2.1 / bearer); stdio needs none.

### Client registration
```bash
claude mcp add --transport stdio hermes-web -- python server.py
```
Cursor (`~/.cursor/mcp.json`):
```json
{ "mcpServers": { "hermes-web": { "command": "python", "args": ["server.py"] } } }
```
Codex CLI is `[mcp_servers.*]` in `~/.codex/config.toml` — **UNCERTAIN**: the
docs redirect cross-origin and the exact TOML shape was not verified. Do not
copy an unverified block.

### Open questions / risks before ever implementing
- **Our stdout contract fights MCP**: the bridge prints ASCII-only status lines
  and puts the UTF-8 payload in a `--out` file (cp1251 safety). MCP returns
  structured results in-band, so the wrapper's status printing must be
  silenced and the file handshake dropped.
- **Lifecycle**: who reaps Deno/Moli child processes when the client exits?
  donsetch hard-shuts on stdin EOF for exactly this reason.
- **Cache visibility**: our TTL cache (search 1 h / strong read 6 h / weak
  5 min / probe 5 min) becomes invisible to the client; expose freshness in
  `structuredContent`.
- **Transport choice**: stdio is simplest and auth-free; HTTP needs a token and
  loopback `Host`/`Origin` hardening (copy donsetch's rules if we ever do it).

### Sources
[donsetch](https://github.com/dondai44423/donsetch) ·
[crawl4ai](https://github.com/unclecode/crawl4ai) ·
[MCP Python SDK](https://pypi.org/project/mcp/) ·
[FastMCP](https://pypi.org/project/fastmcp/) ·
[Claude Code MCP](https://code.claude.com/docs/en/mcp) ·
[Cursor MCP](https://cursor.com/docs/mcp)

## Reference

- DonSeTch repo: https://github.com/dondai44423/donsetch (AGPL-3.0)
  - binary: GitHub Releases `donsetch-win32-x64.tar.gz` (~17MB archive)
  - npm: `npm install -g donsetch` (needs cacache write perms — fails in sandbox)
  - env: needs `SSL_CERT_FILE` (or system certs) for TLS in sandbox
- ratsearch repo: https://github.com/gbkorr/ratsearch
  - .zim archives: https://dumps.wikimedia.org/other/kiwix/zim/wikipedia/
- Moli repo: https://github.com/lexmount/moli (Apache-2.0 / MIT)
  - binary: GitHub Releases `moli-x86_64-pc-windows-msvc.zip` (~39MB archive,
    ~97MB extracted moli.exe) → copy to `hermes-dev/moli/moli.exe` (gitignored)
  - CLI: `moli fetch --dump markdown [--http-proxy ...] [--timeout ms] URL`
  - env: `MOLI_BIN` (bridge override), `SSL_CERT_FILE`/`CURL_CA_BUNDLE` for TLS
