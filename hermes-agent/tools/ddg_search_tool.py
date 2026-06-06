"""Direct registration for DuckDuckGo-based web tools living outside Hermes core.

Minimal wrapper: only binds backend functions to the registry and normalizes
`web_search_deep` to raw results without built-in categorization/markdown.
All synthesis is deferred to the LLM after the tool returns.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

from tools.registry import registry

HERMES_DIR = Path(__file__).resolve().parents[2]
PLUGINS_DIR = HERMES_DIR / "plugins" / "web-tools" / "ddg"


def _load_by_path(module_name: str, module_file: str):
    abs_path = PLUGINS_DIR / module_file
    if not abs_path.exists():
        return None
    spec = importlib.util.spec_from_file_location(module_name, abs_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        return None
    return module


_search = _load_by_path("plugins.web_tools.ddg.ddg_search", "ddg_search.py")
_visit = _load_by_path("plugins.web_tools.ddg.visit_website_enhanced", "visit_website_enhanced.py")

search_deep = _search.search_deep if _search and hasattr(_search, "search_deep") else None
visit_website_enhanced = (
    _visit.visit_website if _visit and hasattr(_visit, "visit_website") else None
)
image_search = _search.image_search if _search and hasattr(_search, "image_search") else None


def _safe_search_deep(query, validate=True, max_validate=200, query_variants=None):
    if search_deep is None:
        return {"error": "ddg backend unavailable"}
    return search_deep(
        query,
        validate=validate,
        classify=False,
        max_validate=max_validate,
        query_variants=query_variants,
        compose=False,
    )


def _safe_expand(source_urls, query, max_new_links=25):
    if not isinstance(source_urls, list):
        source_urls = [source_urls]
    if visit_website_enhanced is None:
        return {"error": "visit_website_enhanced unavailable"}

    def _norm(text):
        if not text:
            return ""
        return re.sub(r"\s+", " ", text).strip()

    uniq = {}
    query_terms = [t.lower() for t in re.findall(r"\b\w+\b", query.lower()) if len(t) > 2]

    for url in source_urls:
        try:
            page = visit_website_enhanced(url)
        except Exception:
            continue
        if not page:
            continue

        title = _norm(page.get("title") or page.get("url") or "")
        links = page.get("links") or page.get("extracted_links") or []

        for link in links:
            if not isinstance(link, dict):
                continue
            href = _norm(link.get("url") or "")
            anchor = _norm(link.get("text") or link.get("title") or "")
            if not href:
                continue
            if href.startswith("javascript:") or href.startswith("mailto:"):
                continue
            if href.startswith("//"):
                href = "https:" + href

            if href in uniq:
                continue

            score = sum(1 for t in query_terms if t in anchor.lower() or t in href.lower())
            uniq[href] = {
                "url": href,
                "anchor": anchor,
                "source_title": title,
                "score": score,
            }

    ranked = sorted(uniq.values(), key=lambda x: x["score"], reverse=True)[:max_new_links]
    return {
        "query": query,
        "sources_count": len(source_urls),
        "candidates": ranked,
        "candidates_count": len(ranked),
    }


# --- Schemas (helpers, not registry calls) ---

def _schema_search_deep():
    return {
        "name": "web_search_deep",
        "description": "Deep DuckDuckGo search. Returns raw validated pages with extracted text; the LLM synthesizes the answer.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query text."},
                "validate": {"type": "boolean", "description": "Verify each result page is alive."},
                "max_validate": {"type": "integer", "description": "Maximum URLs to validate (default 200)."},
                "query_variants": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional explicit query variants.",
                },
            },
            "required": ["query"],
        },
    }


def _schema_expand():
    return {
        "name": "web_expand",
        "description": "Second-level deep research expansion. From alive Level-1 results, collect linked candidate URLs ranked by simple relevance to the query.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Original research query used to score relevance."},
                "source_urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Alive pages from Level 1 to expand from.",
                },
                "max_new_links": {
                    "type": "integer",
                    "description": "Max second-level candidates to return.",
                },
            },
            "required": ["query", "source_urls"],
        },
    }


def _schema_visit():
    return {
        "name": "visit_website_tool",
        "description": "Visit a single page and return its structured content.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Page URL."},
                "max_chars": {"type": "integer", "description": "Optional content length cap."},
            },
            "required": ["url"],
        },
    }


def _schema_image_search():
    return {
        "name": "image_search",
        "description": "Image search with extracted image URLs and page metadata.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query text."},
            },
            "required": ["query"],
        },
    }


# --- Registry calls (must be module-level for AST discovery) ---

registry.register(
    name="web_search_deep",
    toolset="web",
    schema=_schema_search_deep(),
    handler=lambda args, **_: _safe_search_deep(
        args.get("query", ""),
        validate=args.get("validate", True),
        max_validate=int(args.get("max_validate", 200) or 200),
        query_variants=args.get("query_variants"),
    ),
    check_fn=lambda: search_deep is not None,
    emoji="🔎",
    max_result_size_chars=20000,
)

registry.register(
    name="web_expand",
    toolset="web",
    schema=_schema_expand(),
    handler=lambda args, **_: _safe_expand(
        source_urls=args.get("source_urls", []),
        query=args.get("query", ""),
        max_new_links=int(args.get("max_new_links", 25) or 25),
    ),
    check_fn=lambda: visit_website_enhanced is not None,
    emoji="🔗",
    max_result_size_chars=20000,
)

registry.register(
    name="visit_website_tool",
    toolset="web",
    schema=_schema_visit(),
    handler=lambda args, **_: visit_website_enhanced(
        args.get("url", ""),
        max_chars=args.get("max_chars") or None,
    ),
    check_fn=lambda: visit_website_enhanced is not None,
    emoji="🌐",
    max_result_size_chars=20000,
)

registry.register(
    name="image_search",
    toolset="web",
    schema=_schema_image_search(),
    handler=lambda args, **_: image_search(
        args.get("query", ""),
    ),
    check_fn=lambda: image_search is not None,
    emoji="🖼️",
    max_result_size_chars=20000,
)
