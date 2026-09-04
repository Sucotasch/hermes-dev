---
name: hermes-web-tools
description: >-
  Zero-API web search / page-read / image-search primitives for the DeepSeek
  Harness, bridged from the local Hermes deep-search pipeline. USE FOR: any task
  that needs live web information, source-grounded facts, fetching a specific
  page's text, or finding images — when the harness built-in `web_search` tool
  is unavailable (no DEEPSEEK_API_KEY in this environment). DO NOT USE for
  purely local/file tasks. TRIGGERS: "search the web", "find sources",
  "look up online", "read this page/URL", "fetch that article", "find images
  of", "is there anything online about". Invoke only through the bridge script
  `d:\Arx\Software Downloads\Hermes copy\hermes-dev\webtools_bridge.py`
  (Python 3.12 subprocess); never call the Hermes LLM path — the DeepSeek
  agent synthesizes.
---

# Hermes Web Tools (bridge skill)

## Why this exists
The DeepSeek Harness built-in `web_search` tool is **non-functional in this
environment**: it fails with `DeepSeek search has no API key for "DEEPSEEK_API_KEY"`.
This skill fills that gap with a **free, zero-API** pipeline (DuckDuckGo multi-engine
search + curl_cffi anti-bot fetch + Trafilatura extraction) living in the local
Hermes repo at `d:\Arx\Software Downloads\Hermes copy\hermes-dev\`.

It is a **tool bridge**, not an autonomous researcher. The harness agent decides
queries, reads the returned evidence, and synthesizes the answer. No external/auxiliary
LLM is ever called by the tools — synthesis is 100% agent-side (DeepSeek).

## Runtime
- **Bridge script:** `d:\Arx\Software Downloads\Hermes copy\hermes-dev\webtools_bridge.py`
  (kept inside the Hermes deep-search project directory; read/execute only — the script
  writes its JSON output to the system TEMP dir, the only outside-workspace location the
  harness can write to).
- **Backend:** `d:\Arx\Software Downloads\Hermes copy\hermes-dev\hermes-agent\tools\ddg_search_tool.py`
- **Python:** 3.12 (deps installed: `httpx`, `curl_cffi`, `ddgs`, `bs4`, `lxml`, `trafilatura`)
- The bridge installs a **no-op `tools.registry` stub** so the Hermes wrapper imports
  without the full Hermes framework, then exposes seven subcommands: `search`, `read`,
  `image`, `expand`, `render`, `probe`, `mfetch`.
- **v2 additions:** disk cache, Wayback Machine fallback, newest TLS-impersonation pool
  (chrome136/133a/131 + safari180/184/260), optional NecoBox proxy retry (`--proxy`),
  unique default `--out` per call.
- **v3 (Deno JS engine):** `render` subcommand executes a page's inline JavaScript via
  Deno 2.7.7 + happy-dom (no headless browser). The engine is **vendored in the repo**
  at `hermes-dev\deno\` (`deno.exe` + `deno_cache`, gitignored — see README for the
  one-time copy step); resolution order: `WEB_MEDIA_PARSER_DENO`/`DENO_BIN` env →
  vendored `deno\deno.exe` → web-media-parser bundled `deno.exe` → PATH → `deno`
  Python package. Sandboxed: worker runs with NO `--allow-net/read/write/env`.

### Critical Windows constraint
The Windows console is **cp1251**. Printing non-ASCII to stdout crashes Python
(`UnicodeEncodeError`). Therefore the bridge:
- writes **all real output to a UTF-8 JSON file** (default unique path in `%TEMP%`,
  override with `--out`),
- prints **only an ASCII status line** to stdout.

**Always read the JSON file with the `read` tool, never rely on stdout text.**

## Invocation
Run from a `pwsh` tool call. Use `$env:TEMP` for the output path (the harness can write
there). Since v2 the default `--out` is unique per call, but passing an explicit `--out`
is still recommended so you know where to read the result from.

```powershell
# SEARCH — multi-engine search, returns validated pages WITH extracted text
python "d:\Arx\Software Downloads\Hermes copy\hermes-dev\webtools_bridge.py" search --query "your query" --max-validate 8 --out "$env:TEMP\w_search.json"

# READ — structured content of one specific page (auto Wayback fallback on blocks)
python "d:\Arx\Software Downloads\Hermes copy\hermes-dev\webtools_bridge.py" read --url "https://example.com" --max-chars 8000 --out "$env:TEMP\w_read.json"

# IMAGE — image search (experimental; see Limitations)
python "d:\Arx\Software Downloads\Hermes copy\hermes-dev\webtools_bridge.py" image --query "your query" --out "$env:TEMP\w_image.json"

# EXPAND — Level-2: harvest + fetch new URLs linked from source pages
python "d:\Arx\Software Downloads\Hermes copy\hermes-dev\webtools_bridge.py" expand --query "your query" --urls "https://src1.example,https://src2.example" --max-new-links 10 --out "$env:TEMP\w_expand.json"

# RENDER — fetch a page and execute its INLINE JavaScript (Deno happy-dom),
# then return the post-render text. Use when a page's content is built by
# inline scripts; see Limitations (does NOT help network-bound SPAs).
python "d:\Arx\Software Downloads\Hermes copy\hermes-dev\webtools_bridge.py" render --url "https://example.com" --max-chars 8000 --out "$env:TEMP\w_render.json"

# PROBE — token-cheap claim verification: does the page actually say X?
# Returns MATCH/NO-MATCH + up to 3 short excerpts (~60 tokens) instead of
# a full page. Use to verify facts quickly or to spot-check many URLs.
python "d:\Arx\Software Downloads\Hermes copy\hermes-dev\webtools_bridge.py" probe --url "https://example.com" --claim "some factual claim" --out "$env:TEMP\w_probe.json"

# MFETCH — full-browser render via Moli (vendored moli/moli.exe): REAL V8 +
# network, so it renders SPAs (React/Vue/Next.js CSR sites) that the Deno
# happy-dom worker (inline JS only, sandboxed no-net) cannot extract.
# ⚠️ Moli does NOT solve Cloudflare/AWS WAF JS challenges — for challenged
# pages the read ladder's wayback fallback is the answer.
# ⚠️ REQUIRES sandbox_permissions: danger-full-access on the pwsh call —
# the sandbox blocks Moli's TLS stack (like git's schannel issue); with the
# escalation Moli works for HTTPS. Without it, mfetch degrades to read.
python "d:\Arx\Software Downloads\Hermes copy\hermes-dev\webtools_bridge.py" mfetch --url "https://example.com" --out "$env:TEMP\w_mfetch.json"
```

Optional flags:
- `--proxy` — enable NecoBox proxy retry (`http://127.0.0.1:2080`, override with
  `--proxy-url`) for the whole call; helps on geo/rate-limit blocks.
- `--impersonate newest|legacy` — TLS fingerprint pool (default `newest`).
- `--no-cache` — bypass the disk cache for this call.
- `--no-wayback` — skip the Wayback fallback in `read`.
- `--render-timeout N` (render, default 8 s) — hard cap for the Deno worker.
- `--wait-ms N` (render, default 500) — grace period for page scripts.

## Output schema
**search** → `{ "count": int, "top": [ { handle, url, title, alive, relevance, source_query, text(≤1200) } ] }`
- `relevance` is backend 0–1 topical score. Prefer `alive:true` and higher relevance.
- `handle` (`S1`, `S2`, …) is a compact reference for this result — cite it as
  `S3` instead of pasting the full URL (cheap in agent turns).
- `text` is Trafilatura-extracted body (or snippet fallback). For deeper reading of a
  promising hit, call `read` on its `url`.

**read** → `{ url, title, source("direct"|"jina"|"wayback"|"failed"), chars, text, links[], images[], wayback_ts?, challenge?, blocked? }`
- `text` is the full extracted body (capped at `--max-chars`, default 8000).
- `source:"failed"` means every method (direct → Jina → Wayback) was blocked/empty —
  treat the page as unavailable; do not invent its content.
- `source:"wayback"` means the direct fetch was blocked and the text came from an
  archive.org snapshot (`wayback_ts` = snapshot timestamp). Treat it as historical
  content, not the live page.
- `challenge: true` means the direct fetch returned only an anti-bot interstitial
  (Cloudflare "Just a moment", AWS WAF, etc.) and Wayback had no snapshot either.
  **Do not cite or extract facts from it** — the text field contains the challenge
  HTML/CSS, not real content. Retry with `--proxy` or use a different approach.
- `blocked` is a **stable code** to branch on: `cloudflare | aws_waf | recaptcha |
  login | generic`. Agents can branch on the code instead of matching prose.
- `links`/`images` are extracted outbound URLs (handy for follow-up reading); each
  link carries a compact `handle` (`L1`, `L2`, …) for cheap citation.

**image** → `{ "count": int, "images": [ { url, title, page_url } ] }`
- `url` is a thumbnail or page URL. See Limitations — verify relevance before citing.

**expand** → `{ query, candidates_count, fetched_count, items: [ { url, title, anchor, text, chars, relevance, published } ] }`
- Fetches new URLs harvested from the source pages (saturation-guarded, relevance-scored).

**render** → `{ url, title, source("deno-render"|"moli"|"direct"|"jina"|"wayback"|"failed"), chars, text, links[], images[] }`
- Executes the page's inline JS in Deno happy-dom, then extracts post-render
  title/text/links/images. `source:"deno-render"` means JS execution succeeded.
- Fail-open: if Deno is unavailable or rendering yields nothing, escalates to
  Moli (full browser) when available, then falls back to the plain `read` path
  (`source:"direct"|"jina"|"wayback"|"failed"`).

**mfetch** → `{ url, title, source("moli"|"direct"|"jina"|"wayback"|"failed"), chars, text, challenge?, blocked? }`
- Full-browser render via Moli (vendored `moli/moli.exe`). Real V8 + network
  stack — executes external scripts, passes Cloudflare/AWS WAF JS challenges
  that the Deno happy-dom worker cannot.
- `source:"moli"` means Moli successfully rendered the page. `challenge` and
  `blocked` fields follow the same semantics as `read`.
- Fail-open: if Moli is unavailable, blocked by the sandbox (common in this
  environment), or returns a challenge shell, falls back to the plain `read`
  ladder. Use `mfetch` when `render` returns a weak/challenge result.

**probe** → `{ url, match(bool), claim, source, chars, excerpts[], blocked?, wayback_ts? }`
- Token-cheap verification: `match:true` + up to 3 short excerpts proves the page
  contains the claim; `match:false` means the exact claim text was NOT found
  (page may still discuss the topic in other words — read it if it matters).
- `match:null` + `blocked` means the page is an anti-bot/login wall — could not
  verify. Retry with `--proxy`.
- Use `probe` instead of `read` when you only need to confirm a fact exists on a
  page (~60 tokens of evidence vs a full 8k-char fetch).

## Caching (v2)
- `search`: 1 h · `read` (strong content): 6 h · `read` (weak/failed): 5 min ·
  `image`: 30 min · `expand`: 1 h · `probe`: 5 min.
- Repeat calls with the same query/URL are **~0.5 s instead of 30–60 s**.
- Weak/failed reads re-expire quickly so a later call retries and can hit Wayback.

## Synthesis discipline (agent-side, mandatory)
1. **Route by intent (`query_type`):** `general | technical | news | historical | comparison | visual | video`.
   - `visual` → also run `image`; everything else → `search` (+ `read` on top hits).
   - `news`/`historical` → add a current-year term mentally for recency.
2. **Coverage gate:** do not answer from a single weak hit. Aim for ≥3 distinct,
   `alive` sources that actually cover the claim. If `search` returns mostly dead/blocked
   pages, broaden the query or add query variants before concluding "nothing online".
3. **Cite everything:** attribute each factual claim to its source URL/index in the same
   sentence. Name the source; avoid "studies show / experts say". Separate grounded facts
   from your own reasoning and label speculation as such. If no source covers a point,
   state that explicitly instead of inventing a reference.
4. **No tool-side LLM:** the tools return raw evidence JSON only. You (DeepSeek) synthesize.
5. **Speed vs depth:** a single `search` is ~30–60s (first time) and usually enough. The
   full `web_deep_research` composite (multi-query Level-1 + Level-2 expansion) takes
   **~3–5 minutes** — only invoke via Hermes directly when shallow search is insufficient.

## Reliability & limitations (honest)
- **`search` — strong and fast.** Multi-engine (DDG/Yahoo/Yandex/Mojeek via `ddgs`).
  Some engines time out (e.g. startpage) — fail-open, others still answer.
- **`read` — works, but ~40–46% of sites block the direct fetch** (Cloudflare/WAF,
  JS-only SPAs, geo/regional blocks). The v2 fallback ladder is:
  direct (newest TLS fingerprints) → Jina → **Wayback Machine**. IMDB-type pages
  that were previously unreadable now come back via `source:"wayback"`.
- **Wayback** only helps when a snapshot exists; exact URLs (e.g. Reddit comment
  threads) are often unarchived — then `source:"failed"` is honest.
- **`image` — experimental / noisy in this environment.** Results may include
  off-topic or Bing-redirect thumbnails. Manually verify each image is relevant to the
  query before presenting it; do not treat image hits as authoritative evidence.
- **`render` — honest limits.** happy-dom executes INLINE scripts only; it does NOT
  load external `<script src>` or perform network requests (sandboxed, no `--allow-net`).
  So it helps pages whose content is built by inline JS, but **not** network-bound SPAs
  (Wix/React/Next.js apps that fetch content over the network) — for those it falls back
  to `read`, which is usually equally weak. Verify `source:"deno-render"` before trusting
  the post-JS text.
- **No headless browser:** JavaScript-rendered SPAs often yield little/no text.
- **egress works** (confirmed HTTP 200 from duckduckgo), but some hosts rate-limit;
  repeat calls are cache-protected, `--proxy` adds a retry path via local NecoBox.
- **NecoBox proxy** (`127.0.0.1:2080`) is local and free: pass `--proxy` when a site
  rate-limits or geo-blocks the direct fetch.

## Fallback playbook
- Need a page's text but `read` returned `source:"failed"` → `search` the page topic;
  the validation step often already extracted that page's text into a `top` item.
- Page blocked but likely archived → the bridge already tried Wayback; if you know the
  page was different historically, add `--no-cache` to force a retry.
- Need more than 10 hits → raise `--max-validate` (e.g. 20–40); costs time.
- Need depth/sources across subtopics → run several targeted `search` calls in parallel
  (each with its own `--out`), then synthesize; `expand` finds follow-up links.
- Never present `chars:0` / `source:"failed"` content as fact.