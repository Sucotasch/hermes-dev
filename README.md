# Hermes Deep Research Pipeline

Deep research tools for Hermes Agent — network search, URL validation, image extraction, anti-bot bypass.

## Architecture

```
User query
  → LLM assigns query_type (visual/technical/news/historical/comparison/general)
  → web_search_deep (multi-query, validate, dedup)
  → web_expand_and_fetch (Level 2 expansion if alive < 15)
  → image_search (if query_type == "visual")
  → LLM synthesizes answer from evidence pack
```

### Files

| File | Role |
|------|------|
| `hermes-agent/tools/ddg_search_tool.py` | Wrapper: tool registration, query_type routing |
| `plugins/web-tools/ddg/ddg_search.py` | Backend: search strategies, validation, blocklist, images |
| `plugins/web-tools/ddg/visit_website_enhanced.py` | Fetcher: curl_cffi, httpx, Jina, overlay stripping |
| `plugins/web-tools/ddg/query_variants.py` | Intent-aware query variant generator |
| `plugins/web-tools/ddg/compose.py` | Markdown formatter (compose mode) |
| `skills/web-deep-search/SKILL.md` | Deep research skill documentation |

## Setup

### Requirements
- Python 3.11+ (any machine)
- Hermes Agent installed (`~/.hermes/` exists)
- PowerShell (for restore.ps1) or manual file copy

### Install dependencies
```bash
pip install httpx curl_cffi ddgs beautifulsoup4 lxml
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
```

## How It Works

### Search Pipeline
1. **Multi-query collection**: 4-6 query variants → 50-150 raw URLs per query
2. **Blocklist filter**: removes analytics, ads, search engines, aggregators
3. **URL validation**: HEAD-first (dead/blocked early), then GET for alive pages
4. **Content relevance scoring**: word-overlap scoring filters irrelevant alive pages
5. **Level 2 expansion**: if relevant_alive < 15, expand from top alive pages
6. **Image search**: if query_type == "visual", collect image results
7. **Synthesis**: LLM builds answer from evidence pack with inline citations

### Anti-Bot
- **curl_cffi impersonation**: rotates Chrome 110-124 TLS fingerprints
- **DNS circuit breaker**: skips remaining strategies on DNS failure
- **Proxy retry**: retries blocked URLs through NECOBOX proxy
- **JS-block detection**: catches "JavaScript is disabled" pages
- **Overlay stripping**: removes age-gate, cookie consent, modal popups

### Image Extraction
- `extract_fullsize_images()`: og:image, srcset, gallery, data-original, JSON-LD
- `upgrade_to_fullsize()`: thumbnail URL → full-size via regex patterns
- `image_search()`: Bing via Jina Reader (DDG i.js is broken)

## Known Limitations

- 40-46% of URLs blocked by Cloudflare/WAF — proxy retry helps 5-10%
- IMDB, Wikipedia, Reddit blocked — require JS execution or API access
- `content_relevance_score` is keyword-based, can't disambiguate similar names
- `image_search` returns page URLs, not always direct .jpg links
- No headless browser — JS-heavy SPA content is missed

## Development Rules

1. Edit files in this repo, never in `~/.hermes` directly
2. Commit before restoring
3. `query_type` is the sole intent mechanism — no keyword detection in code
4. `registry.register()` calls stay at top level of wrapper
5. Backend (`ddg_search.py`) remains policy-free — no topic branching
