# Evaluated alternatives — DonSeTch, ratsearch & Moli

Status: evaluated 2026-08-30 (DonSeTch, ratsearch) and 2026-09-02 (Moli).
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

**Standout capabilities (verified on this machine, v1.1.1):**
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
