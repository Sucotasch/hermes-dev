# Hermes Deep Research Pipeline

Deep research tools for Hermes Agent — network search, URL validation, image extraction, anti-bot bypass. Unified GUI with standalone CLI and Hermes plugin modes.

## Architecture

```
User query
  → LLM assigns query_type (visual/technical/news/historical/comparison/general)
  → web_search_deep (multi-query, validate, dedup)
  → web_expand_and_fetch (Level 2 expansion if alive < 15)
  → image_search (if query_type == "visual")
  → LLM synthesizes answer from evidence pack
```

### Two execution modes

1. **Hermes plugin mode** — wrapper registers tools with Hermes `tools.registry`. Main entry for interactive use.
2. **Standalone CLI/GUI** — `standalone/deep_research.py` or `standalone/gui.py` drives the same plugin code directly via `orchestrator.py`, with a local LLM (llama.cpp, Ollama, LMStudio) for synthesis.

### Files

| File | Role |
|------|------|
| `hermes-agent/tools/ddg_search_tool.py` | Wrapper: tool registration, query_type routing |
| `plugins/web-tools/ddg/ddg_search.py` | Backend: search strategies, validation, blocklist, images |
| `plugins/web-tools/ddg/visit_website_enhanced.py` | Fetcher: curl_cffi, httpx, Jina, overlay stripping |
| `plugins/web-tools/ddg/query_variants.py` | Intent-aware query variant generator |
| `plugins/web-tools/ddg/compose.py` | Markdown formatter (compose mode) |
| `standalone/gui.py` | PyQt5 GUI: unified control center |
| `standalone/orchestrator.py` | Standalone pipeline (reuses plugins/web-tools/ddg/) |
| `standalone/deep_research.py` | CLI entry point |
| `standalone/llm_client.py` | LLM client (OpenAI-compatible API) |
| `skills/web-deep-search/SKILL.md` | Deep research skill documentation |

## Setup

### Requirements
- Python 3.11+ (any machine)
- Hermes Agent installed (`~/.hermes/` exists)
- PowerShell (for restore.ps1) or manual file copy
- PyQt5 (for GUI mode)

### Install dependencies
```bash
pip install httpx curl_cffi ddgs beautifulsoup4 lxml PyQt5
```

### Deploy to Hermes
```bash
# Clone repo anywhere
git clone <repo-url> hermes-dev
cd hermes-dev

# Auto-restore (detects repo location and ~/.hermes/ automatically)
powershell.exe -File restore.ps1

# Dry-run first (safe, no changes)
powershell.exe -File restore.ps1 -DryRun -SkipBackup -NoStopHermes
```

On Linux/macOS (no PowerShell): copy files manually:
```bash
cp hermes-agent/tools/ddg_search_tool.py ~/.hermes/hermes-agent/tools/
cp plugins/web-tools/ddg/*.py ~/.hermes/plugins/web-tools/ddg/
cp skills/web-deep-search/SKILL.md ~/.hermes/skills/web-deep-search/
cp CONTEXT.md ~/.hermes/
```

### Verify after deploy
```bash
python -m py_compile ~/.hermes/plugins/web-tools/ddg/ddg_search.py
python -m py_compile ~/.hermes/plugins/web-tools/ddg/visit_website_enhanced.py
python -m py_compile ~/.hermes/hermes-agent/tools/ddg_search_tool.py
```

## Key Commands

```bash
# Launch GUI
python standalone/gui.py
# or double-click gui_launcher.bat

# Restore custom tools to ~/.hermes
powershell.exe -File restore.ps1

# Dry-run (no changes)
powershell.exe -File restore.ps1 -DryRun -SkipBackup -NoStopHermes

# Compile check
python -m py_compile plugins\web-tools\ddg\ddg_search.py
python -m py_compile plugins\web-tools\ddg\visit_website_enhanced.py
python -m py_compile hermes-agent\tools\ddg_search_tool.py

# Run tests
python -m pytest plugins/web-tools/ddg/test_query_variants.py
python -m pytest hermes-agent/test_coverage_gate.py

# Standalone CLI
python standalone/deep_research.py "your query" --server http://localhost:8888
```

## How It Works

### GUI (standalone/gui.py)

PyQt5 unified control center with two modes:

**Hermes Mode:**
- "Check & Restore" button — verifies all 5 web tools loaded
- If tools missing → runs `restore.ps1` automatically

**Standalone Mode:**
- Provider selection: Ollama, LMStudio, Custom
- Model dropdown — auto-populated from server
- "Test" button — pings server, shows connected models
- Proxy settings: checkbox + URL
- Progress bar with stage labels
- File logging toggle

### Search Pipeline
1. **Multi-query collection**: 4-6 query variants → 50-150 raw URLs per query
2. **Homepage + search URL filter**: removes homepages and search result pages
3. **Blocklist filter**: removes analytics, ads, search engines, aggregators
4. **URL validation**: HEAD-first (dead/blocked early), then GET for alive pages
5. **Domain quarantine**: 403/captcha → skip domain; 503/timeout → defer to end
6. **Content relevance scoring**: phrase-based keywords + word-overlap scoring
7. **Level 2 expansion**: if relevant_alive < 15, expand from top alive pages
8. **Deep-read**: platform-aware dedup, mirror domain handling
9. **Evidence selection**: phrase keywords + gallery detection for visual queries
10. **Synthesis**: LLM builds answer from evidence pack with inline citations

### Anti-Bot
- **curl_cffi impersonation**: rotates Chrome 110-124 TLS fingerprints
- **Proxy retry**: retries blocked URLs through proxy (main session always direct)
- **Regional block detection**: catches Russian "данный контент недоступен" etc.
- **JS-block detection**: catches "JavaScript is disabled" pages
- **Overlay stripping**: removes age-gate, cookie consent, modal popups
- **Domain quarantine**: 403/captcha → skip; 503/timeout → defer with limit

### Image Extraction
- `extract_fullsize_images()`: og:image, srcset, gallery, data-original, JSON-LD
- `upgrade_to_fullsize()`: thumbnail URL → full-size via regex patterns
- `image_search()`: Bing via Jina Reader (DDG i.js is broken)
- **Gallery detection**: 15+ images + keywords → bonus relevance for visual queries

## Known Limitations

- 40-46% of URLs blocked by Cloudflare/WAF — proxy retry helps 5-10%
- IMDB, Wikipedia, Reddit blocked — require JS execution or API access
- `content_relevance_score` can match partial words — phrase check mitigates but not perfect
- `image_search` returns page URLs, not always direct .jpg links
- No headless browser — JS-heavy SPA content is missed
- GettyImages may return wrong person for visual queries (acceptable error rate)
- vintage-erotica-forum.com often returns 503 — deferred to end of validation

## Development Rules

1. Edit files in this repo, never in `~/.hermes` directly
2. Commit before restoring
3. `query_type` is the sole intent mechanism — no keyword detection in code
4. `registry.register()` calls stay at top level of wrapper
5. Backend (`ddg_search.py`) remains policy-free — no topic branching
6. Proxy is retry mechanism only — main sessions always direct
7. Platform domains (blogspot, livejournal) use path-based dedup, not base domain
