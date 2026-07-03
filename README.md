# Hermes Deep Research Pipeline

**Hermes Deep Research** is a robust, dual-mode deep web research pipeline designed originally for the Hermes Agent. It operates with zero external search API dependencies (no SerpApi, SearchAPI, or Felo keys required), enabling multi-engine evidence collection, comprehensive URL validation, image extraction, and anti-bot bypassing. 

The pipeline can be used as a seamlessly integrated plugin for the Hermes Agent or as a fully independent standalone application (CLI & GUI) powered by local LLMs like `llama.cpp`, `vLLM`, `Ollama`, or `LMStudio`.

---

## 🚀 Real Capabilities

- **Zero-Dependency Multi-Engine Search**: Gathers broad data pools across multiple search engines (DuckDuckGo, Yahoo, Yandex, Mojeek) without requiring paid API keys.
- **Intent-Aware Query Variants**: Automatically decomposes queries into "Core + Constraint + Variants" (e.g., extracting symptoms, error codes, official manuals, or community threads) to maximize search coverage.
- **Intelligent Two-Level Fetching**: 
  - *Level 1*: Wide search pool collection, HEAD-first URL validation, liveness checks, and relevance scoring.
  - *Level 2*: Autonomous expansion (`web_expand_and_fetch`) that visits live pages from Level 1, extracts hyperlinks, and dives deeper if initial coverage is insufficient (e.g., alive URLs < 15).
- **Anti-Bot & Access Bypass**: Employs `curl_cffi`, `httpx` fallbacks, and Jina to navigate past Cloudflare protections, age gates, and cookie consent overlays.
- **Auto-Triggered Visual Context**: Mandates and automatically executes `image_search` for queries classified under visual culture, arts, or people.
- **Agent-Side Synthesis**: Supports raw JSON outputs allowing the overarching Agent (or a local LLM via CLI) to synthesize the evidence pack into heavily cited Markdown reports.

---

## 🧠 Architecture & Algorithm of Operation

### Execution Modes
The pipeline features a unified backend (`plugins/web-tools/ddg/`) driven by two distinct modes:
1. **Hermes Plugin Mode**: Wrappers register tools directly into the Hermes Agent's `tools.registry`. The main entry point is `hermes-agent/tools/ddg_search_tool.py`.
2. **Standalone Mode (CLI/GUI)**: Completely bypasses the Hermes framework. Uses `standalone/orchestrator.py` to drive the backend scripts directly, relying on `standalone/llm_client.py` to interface with any OpenAI-compatible local API server for synthesis.

### The Algorithm: Deep Research Workflow

When a query is ingested, the system follows a strict, multi-pass workflow:
1. **Intent Classification**: The LLM evaluates the user query and assigns a `query_type` (visual, technical, news, historical, comparison, general).
2. **Dynamic Variant Generation**: Token heuristics create multiple context-rich query formulations.
3. **Level 1 Collection & Filtering (Search & Validate)**:
   - Queries are dispatched across engines.
   - Results pass through domain blocklists (filtering junk/SEO spam).
   - Validated via HEAD-first requests, then GET.
   - Domains returning `403` are skipped; `503` are deferred/quarantined.
4. **Relevance Scoring**: Discovered URLs are ranked based on token overlap with the initial query.
5. **Level 2 Expansion (Coverage Gate)**: If the pool yields fewer than 15 alive sources (or if visual/people facets demand more context), `web_expand_and_fetch` scrapes Level 1 pages for secondary hyperlinks to ingest.
6. **Deep Reading & Deduplication**: Platform-aware page scraping runs via `visit_website_enhanced` using anti-bot mechanisms. Content is deduplicated.
7. **Synthesis**: The LLM synthesizes a cohesive, narrative report from the JSON evidence pack, optionally formatted by `compose.py` if running in compose mode.

### Core Files & Responsibilities

| Component Path | Architectural Role |
| --- | --- |
| `hermes-agent/tools/ddg_search_tool.py` | Hermes-specific wrapper. Registers tools and normalizes parameters without enforcing policy. |
| `plugins/web-tools/ddg/ddg_search.py` | Search backend. Handles queries, multi-engine routing, HEAD-validation, and blocklists. |
| `plugins/web-tools/ddg/visit_website_enhanced.py` | Smart fetcher. Reads pages using `curl_cffi` / `httpx`, stripping bot-checks and overlays. |
| `plugins/web-tools/ddg/query_variants.py` | Intent-aware reformulator. Generates diverse queries to maximize search surface. |
| `standalone/orchestrator.py` | Pipeline manager for the standalone version, bypassing Hermes logic. |
| `standalone/llm_client.py` | HTTP client for interfacing with local OpenAI-compatible endpoints (Ollama, llama.cpp). |

---

## ⚙️ Installation and Configuration

### Prerequisites
- Python 3.11 or higher.
- *(For Plugin Mode)*: Hermes Agent installed (directory `~/.hermes/` exists).
- *(For Standalone Mode)*: A running LLM server (llama.cpp, Ollama, LMStudio, vLLM).
- PowerShell (Windows) for auto-deployment, or basic shell for macOS/Linux.

### 1. Install Dependencies
```bash
pip install httpx curl_cffi ddgs beautifulsoup4 lxml PyQt5
```

### 2. Setup Mode Selection

#### Option A: Standalone Use (No Hermes required)
Simply clone the repository and run from the directory. No further file-moving is required.
```bash
git clone https://github.com/Sucotasch/hermes-dev.git
cd hermes-dev
```

#### Option B: Deploying to Hermes Agent
You need to inject these tools into the Hermes Agent's local directory.

**On Windows:**
```powershell
# Dry-run to verify paths (Safe, no changes)
powershell.exe -File restore.ps1 -DryRun -SkipBackup -NoStopHermes

# Actual execution
powershell.exe -File restore.ps1
```

**On Linux/macOS (Manual Copy):**
```bash
cp hermes-agent/tools/ddg_search_tool.py ~/.hermes/hermes-agent/tools/
cp plugins/web-tools/ddg/*.py ~/.hermes/plugins/web-tools/ddg/
cp skills/web-deep-search/SKILL.md ~/.hermes/skills/web-deep-search/
```

Verify successful installation for the plugin:
```bash
python -m py_compile ~/.hermes/plugins/web-tools/ddg/ddg_search.py
python -m py_compile ~/.hermes/hermes-agent/tools/ddg_search_tool.py
```

---

## 💻 Examples of Using Main Functions

### 1. Standalone CLI Usage
The `deep_research.py` script executes the full Level-1 + Level-2 pipeline and dumps the synthesized output to a Markdown file.

```bash
# Basic deep research with default settings
python standalone/deep_research.py "Recent advances in solid-state batteries"

# Connecting to a custom local LLM (e.g., LMStudio or Ollama)
python standalone/deep_research.py "Compare Vaillant boiler F28 vs F29 error codes" --server http://127.0.0.1:11434

# Increasing validation depth and saving to a specific report file
python standalone/deep_research.py "History of Byzantine architecture" --validate 100 --output my_report.md

# Quiet mode (suppresses progress logs for clean pipeline integration)
python standalone/deep_research.py "NVIDIA RTX 5090 specs" --quiet
```

### 2. Standalone GUI Control Center
For users who prefer visual feedback and an interface for tweaking search constraints, a PyQt5 GUI is available:

```bash
# Launch the graphical application
python standalone/gui.py

# Alternatively, on Windows, just double-click:
gui_launcher.bat
```
*The GUI allows you to easily switch between LLM endpoints (`/v1/models` or Ollama's `/api/tags`) and visualize the data pool as it validates in real-time.*

### 3. Native Python Usage (Internal API)
If you are integrating the core backend directly into another Python application, you can bypass the wrappers and call `search_deep` directly.

```python
from plugins.web_tools.ddg.ddg_search import search_deep

# Explicitly defining constraints and variants for a technical lookup
results = search_deep(
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

print(f"Total validated sources found: {len(results)}")
for item in results:
    print(f"[{item['score']}] {item['url']} - {item['title']}")
```

## Known Limitations

- 40-46% of URLs blocked by Cloudflare/WAF — proxy retry helps 5-10%
- IMDB, Wikipedia, Reddit blocked — require JS execution or API access
- `content_relevance_score` can match partial words — phrase check mitigates but not perfect
- `image_search` returns page URLs, not always direct .jpg links
- No headless browser — JS-heavy SPA content is missed

## Development Rules

1. Edit files in this repo, never in `~/.hermes` directly
2. Commit before restoring
3. `query_type` is the sole intent mechanism — no keyword detection in code
4. `registry.register()` calls stay at top level of wrapper
5. Backend (`ddg_search.py`) remains policy-free — no topic branching
6. Proxy is retry mechanism only — main sessions always direct
7. Platform domains (blogspot, livejournal) use path-based dedup, not base domain
