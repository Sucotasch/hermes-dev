# Hermes Deep Research Pipeline

**Hermes Deep Research** is a robust, dual-mode deep web research pipeline designed originally for the Hermes Agent. It operates with **zero external search API dependencies** (no SerpApi, SearchAPI, or Felo keys required), enabling multi-engine evidence collection, comprehensive URL validation, full-size image extraction, and anti-bot bypassing.

The pipeline can be used as a seamlessly integrated plugin for the Hermes Agent or as a fully independent standalone application (CLI & GUI) powered by local LLMs like `llama.cpp`, `vLLM`, `Ollama`, or `LMStudio`.

---

## Capabilities

- **Zero-Dependency Multi-Engine Search**: DuckDuckGo, Yahoo, Yandex, Mojeek — no paid API keys.
- **Intent-Aware Query Variants**: LLM classifies intent (visual/technical/news/person/...), then generates aspect-tagged variants. A result-driven refinement hook (`_suggest_query_variants`) re-searches underrepresented facets when the initial pool is thin.
- **Sitemap URL Seeding** (Crawl4AI-inspired, `sitemap_seeding.py`): before hyperlink expansion, the Level-2 pool is seeded from the sites themselves — robots.txt → sitemap (gzip-aware, bomb-guarded) → junk-filter → BM25 ranking against the query. Bounded (≤4 domains, ≤6s/domain), fail-open, no new dependencies.
- **Best-First Level-2 Expansion** (Crawl4AI-inspired): expansion candidates are scored *before* fetching (anchor-term hits ×2, URL hits ×1, uncovered-aspect boosts); sources are walked relevance+RRF-first; already-known URLs are skipped pre-fetch so the fetch budget goes to new pages only.
- **Aspect-Coverage Tracking**: variants whose facet produced zero alive evidence surface as `aspects_uncovered` in the panel and steer Level-2 ordering via boost terms.
- **Two-Level Fetching**:
  - *Level 1*: wide search pool, HEAD-first validation, liveness checks, relevance scoring, domain quarantine (403 → block, 503 → defer and retry).
  - *Level 2*: expansion visits live pages and harvests secondary hyperlinks when coverage is low; sitemap-seeded URLs compete with hyperlink candidates on the same score scale.
- **PDF Text Extraction**: PDF URLs (`.pdf`, arxiv `/pdf/`) are extracted via `pypdf` on the first request (4MB/30-page bounds, fail-open to the HTML fetch ladder) — `source:"pdf"`.
- **Table Harvesting**: comparison/spec tables are extracted by a second bounded trafilatura pass (`include_tables=True`) into a separate `tables` field — previously discarded entirely.
- **Head Metadata**: meta description / og:description / article:published_time from the already-fetched HTML — a cheap relevance signal (`description` field) plus a `published` backfill when htmldate is missing.
- **Crawl-Layer Filtering (ported from web-media-parser)**: segment-aware skip of legal/account/noise URLs before validation, ad/tracker URL classification with allowlist, SEO keyword-soup spam detection, platform-aware dedup (blogspot/livejournal use path-based keys), mirror-domain collapsing.
- **Anti-Bot & Access Bypass**: `curl_cffi` impersonation, `httpx` fallback, Jina reader with CAPTCHA/challenge detection, DNS circuit breaker, proxy retry, consent/age cookie pre-set.
- **Full-Size Image Discovery**: Imagus sieve rules ported to Python (`sieve.py`) — regex `img`→`to` substitutions, `#ext#` variant expansion, WordPress size-suffix stripping; budgeted thumbnail→original resolution (`discovery.py`) for visual queries; two-phase image filtering (direct download + proxy retry) with format/hash/size checks and progress logging.
- **Quality Evidence Selection**: RRF fusion of multi-variant results, title-normalized dedup, MMR diversification (aspect-aware), saturation-based expansion stop, BM25-ranked evidence chunk selection, optional cross-encoder reranking (`DDG_RERANK=1`).
- **Clean Content Extraction**: Trafilatura main-content extraction (guarded, legacy fallback) plus readability-style cleaning of nav/footer/boilerplate.
- **No-LLM Standalone Mode**: the standalone pipeline runs fully without an LLM server — set the query type manually (`--query-type` / GUI dropdown) and the report is built without the synthesis section (honest note instead of an error). LLM availability is probed once with a 15s bound.
- **Agent-Side Synthesis**: raw JSON evidence packs for the agent, or local-LLM synthesis into cited Markdown reports.

---

## Architecture

### Execution Modes
A unified backend (`plugins/web-tools/ddg/`) is driven by three modes:
1. **Hermes Plugin Mode**: wrappers register tools into the Hermes Agent's `tools.registry`. Entry point: `hermes-agent/tools/ddg_search_tool.py`.
2. **Standalone Mode (CLI/GUI)**: bypasses the Hermes framework; `standalone/orchestrator.py` drives the backend, `standalone/llm_client.py` talks to any OpenAI-compatible local API server.
3. **DeepSeek Harness Bridge Mode** (v2+): exposes the same backend as plain Python CLI primitives for the DeepSeek Harness environment. See `webtools_bridge.py` (this repo) + an external skill `hermes-web-tools` (installed at `~/.dsh/skills/`). The bridge installs a no-op registry stub to load the Hermes wrapper without the full Hermes framework, then provides five subcommands (`search`, `read`, `image`, `expand`, `render`). A vendored Deno 2.7.7 engine + happy-dom (in `deno/`, gitignored) provides lightweight inline-JS execution without a headless browser.

### Deep Research Workflow
1. **Intent Classification** — LLM assigns `query_type` (visual, technical, news, person, comparison, ...).
2. **Variant Generation** — aspect-tagged query variants; refinement hook on thin pools.
3. **Search & Filter** — multi-engine collection → blocklist, junk/ad crawl skip, keyword-soup and video/service URL filtering.
4. **Validation** — HEAD-first then GET; relevance scoring; domain quarantine (403 skip, 503 defer + final retry pass).
5. **Level 2 Expansion** — if alive < 20, harvest links from live pages (utility-token and ad filters applied).
6. **Deep-Read & Image Extraction** — Trafilatura/readability content, sieve fullsize discovery (visual), relevance gating with gallery bonus.
7. **Image Filter** — format/hash/size dedup, two-phase download with proxy recovery (progress-logged).
8. **Evidence Selection** — BM25 chunk ranking; wrapper adds RRF fusion + MMR diversification + title dedup.
9. **Synthesis** — LLM writes the analysis from the evidence pack; Markdown report assembled (articles + images + gallery links + synthesis).

### Core Files

| Component | Role |
| --- | --- |
| `hermes-agent/tools/ddg_search_tool.py` | Hermes wrapper; registers 5 tools (`web_search_deep`, `web_expand_and_fetch`, `visit_website_tool`, `image_search`, `web_deep_research`), RRF/MMR selection. |
| `plugins/web-tools/ddg/ddg_search.py` | Search backend: multi-engine routing, validation, blocklists, fullsize extraction. |
| `plugins/web-tools/ddg/visit_website_enhanced.py` | Smart fetcher (`curl_cffi`/`httpx`/Jina) + Trafilatura content extraction + PDF fast path + tables + head metadata. |
| `plugins/web-tools/ddg/query_variants.py` | Aspect-tagged variants + result-driven `_suggest_query_variants`. |
| `plugins/web-tools/ddg/sitemap_seeding.py` | Bounded robots.txt → sitemap → BM25 URL seeding for Level-2 (gzip-aware, fail-open). |
| `plugins/web-tools/ddg/selection.py` | Shared RRF fusion, title dedup, MMR diversification, saturation stop, cross-encoder hook. |
| `plugins/web-tools/ddg/sieve.py` | Imagus sieve static engine (fullsize image rules, JS→Python callable converter). |
| `plugins/web-tools/ddg/discovery.py` | Budgeted thumbnail→fullsize link resolution via sieve `link→url→res` chains. |
| `plugins/web-tools/ddg/junk_filter.py` | Ad/tracker URL classifier + WP-1 crawl-skip (legal/account/noise segments), allowlist-aware. |
| `plugins/web-tools/ddg/evidence_rank.py` | BM25 evidence chunk selection + Jina antibot detection. |
| `plugins/web-tools/ddg/_common.py`, `_coverage.py` | URL normalization, registrable domain, coverage accounting. |
| `plugins/web-tools/ddg/compose.py` | Markdown answer formatting (compose mode). |
| `standalone/orchestrator.py` | Standalone pipeline manager (crawl layer, sieve, BM25, image filter). |
| `standalone/llm_client.py` | OpenAI-compatible local LLM client (Ollama, llama.cpp, vLLM, LMStudio). |
| `webtools_bridge.py` | Harness bridge: registry-stub loader + `search`/`read`/`image`/`expand`/`render` CLI over the same backend. |
| `js_engine/render_worker.js` | Deno happy-dom worker (inline-JS execution for `render`); engine binaries live in gitignored `deno/`. |
| `restore.ps1` + `restore_check.py` | One-button deploy/health-check for Hermes plugin mode (see below). |

---

## Installation

### Prerequisites
- Python 3.11+
- *(Plugin mode)* Hermes Agent installed (`~/.hermes/` exists)
- *(Standalone mode)* a running local LLM server
- PowerShell (Windows) for auto-deploy

### 1. Dependencies
```bash
pip install httpx curl_cffi ddgs beautifulsoup4 lxml trafilatura htmldate PyQt5 Pillow
```
`restore.ps1` installs missing packages into the Hermes venv automatically; standalone re-runs of `restore.ps1`/tests need them in the local Python too.

### 2a. Standalone (no Hermes required)
```bash
git clone https://github.com/Sucotasch/hermes-dev.git
cd hermes-dev
python standalone/deep_research.py "your query" --server http://127.0.0.1:11434
```

### 2b. Deploy to Hermes Agent (Windows) — one-button restore
`restore.ps1` is a check-and-repair tool: it syncs the manifest files from the repo into `~/.hermes`, installs missing venv deps, py_compile-checks everything, and runs `restore_check.py` for the final verdict (5 tools registered + deps importable). No backups are made — the git repo is the source of truth. It cannot hang: process stop is opt-in, WMI is not touched by default.

```powershell
# Show what would happen (changes nothing)
powershell.exe -File restore.ps1 -DryRun

# Full check + restore (files, deps, compile, verdict)
powershell.exe -File restore.ps1

# Optional: stop Hermes first / run the live smoke probe
powershell.exe -File restore.ps1 -StopHermes -RunSmoke
```
The standalone GUI's **"Check & Restore"** button runs the same script.

**Linux/macOS (manual copy):**
```bash
cp hermes-agent/tools/ddg_search_tool.py ~/.hermes/hermes-agent/tools/
cp plugins/web-tools/ddg/*.py ~/.hermes/plugins/web-tools/ddg/
cp skills/web-deep-search/SKILL.md ~/.hermes/skills/web-deep-search/
```

Verify:
```bash
python restore_check.py   # or: <hermes venv>/python.exe restore_check.py
```

---

## Usage Examples

### 1. Standalone CLI
```bash
python standalone/deep_research.py "Recent advances in solid-state batteries"
python standalone/deep_research.py "Compare Vaillant boiler F28 vs F29 error codes" --server http://127.0.0.1:11434
python standalone/deep_research.py "History of Byzantine architecture" --validate 100 --output my_report.md
python standalone/deep_research.py "NVIDIA RTX 5090 specs" --quiet
# No LLM server? Set the type yourself, get the report without synthesis:
python standalone/deep_research.py "best desktop wallpaper sites" --qtype visual --server http://127.0.0.1:1
```

### 2. Standalone GUI
```bash
python standalone/gui.py     # or double-click gui_launcher.bat
```
Features: LLM endpoint test (auto-discovers `/v1/models` or `/api/tags`), parameter presets (Minimal / Balanced / Visual / Maximum), proxy toggle, real-time progress with stage mapping, file logging, and the Hermes **"Check & Restore"** button.

### 3. Native Python API
```python
from plugins.web_tools.ddg.ddg_search import search_deep

out = search_deep(
    query="Vaillant boiler F28 error",
    query_variants=[
        "Vaillant boiler F28 error code causes fix",
        "Vaillant F28 fault reset procedure official",
        "Vaillant boiler F28 self-clean ignition repair",
        "Vaillant F28 error forum threads community",
    ],
    validate=True,
    max_validate=40,
    timeout_per_url=3,
)
for item in out["results"]:  # results sorted by relevance
    if item.get("alive"):
        print(f"[{item['relevance']}] {item['url']} - {item['title']}")
```

### 4. DeepSeek Harness bridge (agent's own web tooling)
The bridge turns the same backend into plain CLI primitives for the DeepSeek Harness
(where the built-in `web_search` needs an API key that this environment lacks).
All output goes to a UTF-8 JSON file (unique default in `%TEMP%`); stdout carries only
an ASCII status line (Windows cp1251-safe).

```powershell
# search / read / image / expand / render
python webtools_bridge.py search --query "solid state battery" --max-validate 8 --out "$env:TEMP\w_search.json"
python webtools_bridge.py read --url "https://example.com" --max-chars 8000 --out "$env:TEMP\w_read.json"
python webtools_bridge.py image --query "red panda" --out "$env:TEMP\w_image.json"
python webtools_bridge.py expand --query "topic" --urls "https://a.example,https://b.example" --out "$env:TEMP\w_expand.json"
python webtools_bridge.py render --url "https://js-heavy.example" --out "$env:TEMP\w_render.json"
```

Bridge-only extras over the backend:
- **Disk cache** — search 1 h, strong reads 6 h, weak/failed reads 5 min, image 30 min, expand 1 h. Repeat calls are ~0.5 s instead of 30-60 s.
- **Wayback fallback** — when direct fetch is blocked/empty, reads an archive.org snapshot (`source:"wayback"`, `wayback_ts` set). Honors archive.org rate limits (429 backoff).
- **`--proxy`** — enable NecoBox `http://127.0.0.1:2080` retry for the whole call.
- **`--impersonate newest`** (default) — patches the backend's TLS pool to chrome136/133a/131 + safari180/184/260.
- **`render`** — Deno 2.7.7 + happy-dom executes a page's inline JS (no headless browser); fail-open to the plain `read` ladder when rendering yields <300 chars.
- **`--no-cache` / `--no-wayback` / `--render-timeout` / `--wait-ms`** toggles.

Deno engine setup (once, ~130 MB, gitignored):
```powershell
$srcBin = "d:\Arx\Software Downloads\_Images_EDIT-pack\web-media-parser\dist\WebMediaParser\bin"
New-Item -ItemType Directory -Force -Path deno | Out-Null
Copy-Item "$srcBin\deno.exe" deno\deno.exe
Copy-Item -Recurse "$srcBin\deno_cache" deno\deno_cache
```
The companion skill `hermes-web-tools` (in the Harness skill store, outside this repo)
documents invocation, output schemas, and the agent-side synthesis discipline.

---

## Tests
```bash
python -m pytest plugins/web-tools/ddg/ hermes-agent/tools/test_ddg_search_tool.py -q
```

## Known Limitations
- 40-46% of URLs blocked by Cloudflare/WAF — proxy retry recovers 5-10%
- IMDB, Wikipedia, Reddit blocked without JS execution or API access
- Relevance scoring is keyword-based — wrong-person/same-name pages can slip into visual reports
- No headless browser — JS-heavy SPA content is missed
- Visual queries download every candidate image to check size/format — the phase takes minutes (progress is now logged); the number embedded in the report can be capped in the GUI (0 = all)

## Development Rules
1. Edit files in this repo, never in `~/.hermes` directly
2. Commit before restoring; the git repo is the source of truth (restore makes no backups)
3. `query_type` is the sole intent mechanism — no keyword detection in code
4. `registry.register()` calls stay at top level of the wrapper
5. Backend (`ddg_search.py`) remains policy-free — no topic branching
6. Proxy is a retry mechanism only — main sessions always direct
7. Platform domains (blogspot, livejournal) use path-based dedup, not base domain
