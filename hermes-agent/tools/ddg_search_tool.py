"""Direct registration for DuckDuckGo-based web tools living outside Hermes core.

Minimal wrapper: only binds backend functions to the registry and normalizes
`web_search_deep` to raw results without built-in categorization/markdown.
All synthesis is deferred to the LLM after the tool returns.
"""
from __future__ import annotations

import importlib.util
import re
import sys
import time
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
    # Ensure the plugin's own directory is importable for intra-plugin imports
    # (e.g. `ddg_search.py` -> `query_variants.py`)
    plugin_dir = str(abs_path.parent)
    if plugin_dir not in sys.path:
        sys.path.insert(0, plugin_dir)
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


def _safe_search_deep(query, validate=True, max_validate=200, query_variants=None, query_type=None):
    if search_deep is None:
        return {"error": "ddg backend unavailable"}
    return search_deep(
        query,
        validate=validate,
        classify=False,
        max_validate=max_validate,
        query_variants=query_variants,
        compose=False,
        query_type=query_type,
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


def _safe_expand_and_fetch(query, source_urls, max_new_links=20, max_chars=5000):
    """Level-2 expansion + fetch: return candidate list plus fetched page snippets."""
    expand = _safe_expand(
        source_urls=source_urls,
        query=query,
        max_new_links=max_new_links,
    )
    candidates = expand.get("candidates", [])
    fetched = []
    for c in candidates:
        url = c.get("url")
        if not url:
            continue
        try:
            page = visit_website_enhanced(url)
        except Exception:
            continue
        if not page:
            continue
        text = page.get("text") or page.get("content") or ""
        fetched.append({
            "url": url,
            "title": page.get("title") or c.get("anchor") or url,
            "anchor": c.get("anchor"),
            "text": text[:max_chars],
            "chars": len(text),
        })
        if len(fetched) >= max_new_links:
            break
    return {
        "query": query,
        "candidates_count": len(candidates),
        "fetched_count": len(fetched),
        "items": fetched,
    }


def _query_variants_wrapper(query, query_type="general"):
    """Return generated variants; fall back to static heuristics if unavailable."""
    try:
        import query_variants
        generated = query_variants.generate(query, query_type=query_type)
        if generated:
            return generated
    except Exception:
        pass
    base = [query]
    tokens = [t for t in re.findall(r'\b\w+\b', query.lower()) if len(t) > 3]
    if tokens:
        base += [
            f'{tokens[0]} history',
            f'{tokens[0]} trends',
            f'{tokens[0]} examples',
            f'best {tokens[0]} resources',
        ]
    return base


def _compact_evidence(pages, max_per_page=1500):
    """Compress evidence pack: truncated text + metadata instead of full text.

    Returns compact list with summary, url, title, relevance.
    1500 chars balances context usage (25 pages × 1500 = 37,500 chars) with
    content depth for local 92k models (72k usable after Hermes overhead).
    """
    compact = []
    for p in pages:
        text = p.get("text", "")
        # Backend text is single-space normalized (no paragraph breaks),
        # so truncate by characters rather than pretending to take "paragraphs".
        summary = (text or "")[:max_per_page]
        compact.append({
            "url": p.get("url", ""),
            "title": p.get("title", ""),
            "relevance": round(p.get("relevance", 0), 2),
            "summary": summary,
            "alive": p.get("alive", False),
        })
    return compact


def _safe_deep_research(query, max_validate=200, max_new_links=20, max_chars=5000, query_type=None):
    """Composite deep research pipeline."""
    if search_deep is None:
        return {"error": "ddg backend unavailable"}

    variants = _query_variants_wrapper(query, query_type=query_type)

    seen_page = set()
    pages = []
    alive_count = 0
    raw_count = 0
    start = time.time()

    for q in variants:
        try:
            out = search_deep(
                q,
                validate=True,
                classify=False,
                max_validate=max_validate,
                query_variants=None,
                compose=False,
                query_type=query_type,
            )
        except Exception:
            continue
        for r in out.get("results", []):
            u = r.get("url")
            if not u or u in seen_page:
                continue
            seen_page.add(u)
            raw_count += 1
            if r.get("alive"):
                alive_count += 1
            pages.append({
                "url": u,
                "title": r.get("title") or u,
                "snippet": r.get("snippet") or "",
                "text": (r.get("text") or r.get("snippet") or "")[:max_chars],
                "alive": r.get("alive"),
                "status": r.get("status"),
                "source_query": q,
            })

    level1_time = round(time.time() - start, 2)

    # Only alive pages are real evidence — dead/blocked pages must not reach the LLM
    pages = [p for p in pages if p.get("alive")]

    top_alive = [p["url"] for p in pages if p.get("alive")][:20]
    need_expand = alive_count < 15 or not _is_coverage_sufficient(pages, query)
    expand_items = []
    if need_expand and top_alive:
        expand_out = _safe_expand_and_fetch(
            query=query,
            source_urls=top_alive,
            max_new_links=max_new_links,
            max_chars=max_chars,
        )
        for item in expand_out.get("items", []):
            u = item.get("url")
            if u and u not in seen_page:
                seen_page.add(u)
                expand_items.append(item)
                alive_count += 1

    evidence = pages + expand_items

    # Post-retrieval dedupe + source quotas (based on TinySearch chunk pool selection).
    # Outcome: Jasper reducer for duplicated content and single-source bias.
    evidence = _apply_post_retrieval_filter(evidence, query=query, final_limit=25)

    images = []
    if query_type == "visual" and image_search is not None:
        try:
            img_out = image_search(query)
            for item in img_out.get("results", [])[:8]:
                images.append({
                    "url": item.get("thumbnail") or item.get("page_url") or item.get("url"),
                    "title": item.get("title") or item.get("page_url") or item.get("url"),
                    "source": item.get("page_url") or item.get("url"),
                })
        except Exception:
            pass

    # Compact evidence for small context windows
    compact_evidence = _compact_evidence(evidence)

    return {
        "query": query,
        "variants_used": variants,
        "panel": {
            "raw": raw_count,
            "alive": alive_count,
            "level1": len(pages),
            "level2": len(expand_items),
            "images": len(images),
            "level1_time": level1_time,
        },
        "pages": compact_evidence,
        "images": images,
    }


def _apply_post_retrieval_filter(evidence, query, final_limit=80):
    """Apply Jaccard-based deduplication + per-source URL quotas to evidence pages."""

    def _tokenize(text: str):
        import re

        return frozenset(re.findall(r"[a-zA-Z0-9_./:#-]+", (text or "").lower()))

    def _jaccard(a, b):
        if not a and not b:
            return 1.0
        if not a or not b:
            return 0.0
        inter = len(a & b)
        union = len(a | b)
        return inter / union if union else 0.0

    max_per_source_url = 4
    jaccard_threshold = 0.85

    def _base_domain(url):
        from urllib.parse import urlparse
        host = (urlparse(url or "").hostname or "").lower()
        parts = host.split(".")
        return ".".join(parts[-2:]) if len(parts) > 2 else host

    accepted = []
    accepted_sets = []
    source_counts = {}

    def _selected_id(item):
        return item.get("url") or id(item)

    def _accept(item):
        text = " ".join([item.get("title") or "", item.get("text") or "", item.get("snippet") or ""])
        tokens = _tokenize(text)
        if not tokens:
            # Reject duplicates of empty-text items (previously inverted — accepted them)
            return not any(
                _selected_id(x) != _selected_id(item)
                and " ".join([x.get("title") or "", x.get("text") or "", x.get("snippet") or ""]) == text
                for x in accepted
            )
        worst = 0.0
        for s in accepted_sets:
            if s:
                worst = max(worst, _jaccard(tokens, s))
        return worst < jaccard_threshold

    for item in evidence:
        if len(accepted) >= final_limit:
            break
        # Per-source quota is per base domain: per-URL keys never bind because
        # duplicate URLs are already removed earlier in _safe_deep_research.
        domain = _base_domain(item.get("url") or "")
        if source_counts.get(domain, 0) >= max_per_source_url:
            continue
        if not _accept(item):
            continue
        source_counts[domain] = source_counts.get(domain, 0) + 1
        accepted.append(item)
        text = " ".join([item.get("title") or "", item.get("text") or "", item.get("snippet") or ""])
        accepted_sets.append(_tokenize(text))

    return accepted


def _is_coverage_sufficient(pages, query):
    """Topic-coverage gate. Delegates to the shared _coverage module so tests
    exercise the production code; if the module is missing (e.g. after a partial
    restore), conservatively report 'insufficient' to trigger Level-2 expansion."""
    try:
        from _coverage import is_coverage_sufficient
        return is_coverage_sufficient(pages, query)
    except Exception:
        return False


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
                "query_type": {
                    "type": "string",
                    "enum": ["visual", "technical", "news", "historical", "comparison", "video", "general"],
                    "description": "Agent-decided intent label. Backend does not branch on it.",
                },
            },
            "required": ["query"],
        },
    }


def _schema_expand_and_fetch():
    return {
        "name": "web_expand_and_fetch",
        "description": "Second-level expansion plus fetch. Returns fetched Level-2 pages for synthesis.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Original research query used to score relevance."},
                "source_urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Alive high-quality pages from Level 1 to expand from.",
                },
                "max_new_links": {
                    "type": "integer",
                    "description": "Max second-level fetched pages to return.",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Optional content length cap per fetched page.",
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


def _schema_deep_research():
    return {
        "name": "web_deep_research",
        "description": "One-call deep research. Runs multi-query Level 1 search, auto-expands Level 2 if needed, collects images for visual topics, and returns unified evidence pack.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Research query."},
                "max_validate": {"type": "integer", "description": "Max URLs to validate per query (default 200)."},
                "max_new_links": {"type": "integer", "description": "Max Level-2 pages to fetch (default 20)."},
                "max_chars": {"type": "integer", "description": "Text length cap per page (default 5000)."},
                "query_type": {
                    "type": "string",
                    "enum": ["visual", "technical", "news", "historical", "comparison", "video", "general"],
                    "description": "Agent-decided intent label. Controls image search routing.",
                },
            },
            "required": ["query"],
        },
    }


registry.register(
    name="web_search_deep",
    toolset="web",
    schema=_schema_search_deep(),
    handler=lambda args, **_: _safe_search_deep(
        args.get("query", ""),
        validate=args.get("validate", True),
        max_validate=int(args.get("max_validate", 200) or 200),
        query_variants=args.get("query_variants"),
        query_type=args.get("query_type"),
    ),
    check_fn=lambda: search_deep is not None,
    emoji="🔎",
    max_result_size_chars=20000,
)

registry.register(
    name="web_expand_and_fetch",
    toolset="web",
    schema=_schema_expand_and_fetch(),
    handler=lambda args, **_: _safe_expand_and_fetch(
        source_urls=args.get("source_urls", []),
        query=args.get("query", ""),
        max_new_links=int(args.get("max_new_links", 20) or 20),
        max_chars=int(args.get("max_chars", 5000) or 5000),
    ),
    check_fn=lambda: visit_website_enhanced is not None,
    emoji="🔗🌐",
    max_result_size_chars=40000,
)

registry.register(
    name="visit_website_tool",
    toolset="web",
    schema=_schema_visit(),
    handler=lambda args, **_: visit_website_enhanced(
        args.get("url", ""),
        max_chars=args.get("max_chars") or 8000,
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

registry.register(
    name="web_deep_research",
    toolset="web",
    schema=_schema_deep_research(),
    handler=lambda args, **_: _safe_deep_research(
        query=args.get("query", ""),
        max_validate=int(args.get("max_validate", 200) or 200),
        max_new_links=int(args.get("max_new_links", 20) or 20),
        max_chars=int(args.get("max_chars", 5000) or 5000),
        query_type=args.get("query_type"),
    ),
    check_fn=lambda: search_deep is not None and visit_website_enhanced is not None,
    emoji="🧠",
    max_result_size_chars=60000,
)
