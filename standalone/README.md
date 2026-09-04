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

# Set query intent manually (skips LLM classification)
python deep_research.py "query" --query-type visual
python deep_research.py "query" --qtype technical
```

## No-LLM mode

The LLM plays a small role: classify the query intent at the start and synthesize
a summary at the end. Both steps are optional — **the pipeline runs fully without
an LLM server**:

- `--query-type <type>` (CLI) or the *type dropdown* (GUI) sets the intent manually —
  no classification request is made. Types: `person, visual, technical, news,
  historical, comparison, fact, art, education, science, video, general`.
- Without an LLM the report is built **without the synthesis section** —
  all articles, images and metadata are still there. The report carries an
  explicit note (`_LLM synthesis skipped — no LLM server reachable at …_`)
  instead of failing.
- LLM availability is probed once with a **15s bound** (not the 120s chat
  timeout), so a dead or hanging server costs seconds, not minutes.
- `stats["llm"]` in the result dict tells you which mode ran (`true`/`false`).

Default behavior is unchanged: with no `--query-type` and a live server, the LLM
classifies (fallback `general` on any failure) and synthesizes as before.

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
8. Level 2 expansion (if alive < 15)
9. Deep-read with platform-aware dedup
10. Evidence selection; LLM synthesis (skipped, with a note, when no LLM)
11. Report saved as .md file
