# Deep Research — Standalone

Standalone deep research tool. Connects to a local LLM server (llama.cpp, vLLM, etc.) and performs deep web research.

## Requirements

- Python 3.11+
- llama.cpp server running (or any OpenAI-compatible server) — **optional**, see [No-LLM mode](#no-llm-mode)
- Packages: `pip install beautifulsoup4 lxml curl_cffi ddgs httpx`

## Usage

```bash
# Basic usage
python deep_research.py "your research query"

# Custom server
python deep_research.py "query" --server http://192.168.1.100:8080

# More validation
python deep_research.py "query" --validate 100

# Custom output file
python deep_research.py "query" --output my_report.md

# Quiet mode (no progress output)
python deep_research.py "query" --quiet

# Set query intent manually (skips the classification call ONLY —
# enrichment and synthesis still use the LLM, patiently)
python deep_research.py "query" --query-type visual
python deep_research.py "query" --qtype technical

# Fully offline: zero LLM requests (no classification, no enrichment,
# no synthesis). Pair with a type; without one it defaults to 'general'.
python deep_research.py "query" --no-llm --qtype visual

# Proxy retry for blocked/dead URLs (NECOBOX), same as the GUI checkbox
python deep_research.py "query" --proxy --proxy-url http://127.0.0.1:2080
```

## No-LLM mode (explicit opt-in)

The LLM plays a small role: classify the query intent at the start and synthesize
a summary at the end. Both steps can be skipped — **the pipeline runs fully
without an LLM server**:

- `--no-llm` (CLI) or the *No LLM checkbox* (GUI): **zero** LLM requests are
  made — no classification, no enrichment, no synthesis. The report carries an
  explicit note (`_LLM synthesis skipped (no-LLM mode)._`) instead of a summary
  section; `stats["llm"]` is `false`.
- `--query-type <type>` (CLI) or the *type dropdown* (GUI) sets the intent
  manually — this skips **only the classification call**. Enrichment (person
  queries) and synthesis are still attempted with the full patient timeout
  (local servers can take 30–90s to load the model on the first request —
  the pipeline never decides early that the LLM is "dead"). Types: `person,
  visual, technical, news, historical, comparison, fact, art, education,
  science, video, general`.
- `--no-llm` without a type defaults to `general` **with a warning line in
  the log** (visual/person pipelines differ meaningfully — pick the type).
- With Auto type and a dead server, classification fails open to `general`
  and synthesis gets an honest "failed" note; the pipeline never crashes.

Why explicit opt-in (design note): an early one-shot "LLM dead?" probe was
tried and removed — it misfired on local servers that were still loading
the model, silently degrading reports that could have had a synthesis.

## Architecture

```
deep_research.py    CLI entry point
orchestrator.py     Pipeline orchestration
gui.py              PyQt5 GUI (type dropdown, presets, log)
llm_client.py       llama.cpp HTTP client

../plugins/web-tools/ddg/
  ddg_search.py           Backend (search, validation, blocklist)
  visit_website_enhanced.py  Fetcher (curl_cffi, httpx, Jina, PDF)
  query_variants.py       Query variant generator
  compose.py              Markdown formatter
```

## How it works

1. Intent: user-set type (`--query-type`) or LLM classification (visual/technical/news/…)
2. Multi-query search collects URLs
3. Homepage + search URL + video URL filter
4. Blocklist filters junk domains
5. URL validation (HEAD-first, then GET)
6. Domain quarantine (403 → skip, 503 → defer)
7. Content relevance scoring
8. Level 2 expansion (if alive < 20): sitemap seeding (BM25-ranked URLs
   from the alive domains' sitemaps) + hyperlink walk, shared per-domain caps
9. Deep-read with platform-aware dedup
10. Evidence selection; LLM synthesis (skipped with an honest note in
    `--no-llm` mode; attempted patiently otherwise)
11. Report saved as .md file
