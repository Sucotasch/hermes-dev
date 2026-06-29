# Deep Research — Standalone

Standalone deep research tool. Connects to a local LLM server (llama.cpp, vLLM, etc.) and performs deep web research.

## Requirements

- Python 3.11+
- llama.cpp server running (or any OpenAI-compatible server)
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
```

## Architecture

```
deep_research.py    CLI entry point
orchestrator.py     Pipeline orchestration
llm_client.py       llama.cpp HTTP client

../plugins/web-tools/ddg/
  ddg_search.py           Backend (search, validation, blocklist)
  visit_website_enhanced.py  Fetcher (curl_cffi, httpx, Jina)
  query_variants.py       Query variant generator
  compose.py              Markdown formatter
```

## How it works

1. LLM classifies query intent (visual/technical/news/etc.)
2. Multi-query search collects URLs
3. Blocklist filters junk domains
4. URL validation (HEAD-first, then GET)
5. Content relevance scoring
6. Evidence pack compacted for LLM context
7. LLM synthesizes final answer
8. Report saved as .md file
