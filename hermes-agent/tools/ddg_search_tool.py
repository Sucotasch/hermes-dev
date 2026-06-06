"""Direct registration for DuckDuckGo-based web tools living outside Hermes core.

Loads `ddg_search.py`, `visit_website_enhanced.py`, and `compose.py`
from `plugins/web-tools/ddg/` by path and exposes them through the native
registry. No ``config.yaml`` edits are required.
"""
from __future__ import annotations

import importlib.util
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
_compose = _load_by_path("compose", "compose.py")

# Ensure backend can import compose by name
if _compose is not None and "compose" not in sys.modules:
    sys.modules["compose"] = _compose

search_deep = _search.search_deep if _search and hasattr(_search, "search_deep") else None
visit_website_enhanced = (
    _visit.visit_website if _visit and hasattr(_visit, "visit_website") else None
)
image_search = _search.image_search if _search and hasattr(_search, "image_search") else None


def _safe_search_deep(query, validate=True, classify=True, max_validate=50, query_variants=None):
    if search_deep is None:
        return {"error": "ddg backend unavailable"}
    return search_deep(
        query,
        validate=validate,
        classify=classify,
        max_validate=max_validate,
        query_variants=query_variants,
        compose=False,
    )


def compose_markdown(query, deep_result, include_images=True):
    if deep_result is None:
        deep_result = {}
    if _compose is not None and hasattr(_compose, "_build_markdown_answer"):
        try:
            return _compose._build_markdown_answer(query, deep_result, include_images=include_images)
        except Exception as exc:
            return f"# Deep search result\n\nQuery: {query}\n\nCompose failed: {exc}"
    return _build_fallback_markdown(query, deep_result, include_images=include_images)


def _build_fallback_markdown(query, deep_result, include_images=True):
    summary = deep_result.get("summary", {}) or {}
    results = deep_result.get("results", []) or []
    categories = deep_result.get("categories", {}) or {}

    lines = [
        "# Deep search result",
        "",
        f"Query: {query}",
        "",
        f"Raw: **{summary.get('raw_count', summary.get('total_raw', 0))}**, validated: **{summary.get('validated_count', summary.get('validated', 0))}**, alive: **{summary.get('alive_count', summary.get('alive', 0))}**",
        "",
        "## Facts",
        "",
    ]
    if not results:
        lines.append("- No validated sources")
    for item in results:
        if not item.get("alive"):
            continue
        title = (item.get("title") or "").strip() or "(no title)"
        body = (item.get("body") or item.get("text") or "").strip()
        snippet = body.splitlines()[0] if body else "(no description)"
        url = (item.get("url") or "").strip()
        lines.append(f"- {title}")
        if url:
            lines.append(f"  - **Link:** [{url}]({url})")
        lines.append(f"  - **Fact:** {snippet}")

    lines += ["", "## Sources by category", ""]
    if categories:
        for cat, items in sorted(categories.items(), key=lambda pair: len(pair[1]), reverse=True):
            lines.append(f"- {cat}: {len(items)}")
    else:
        lines.append("- No categorized sources")

    if include_images:
        seen = set()
        images = []
        for item in results:
            if not item.get("alive"):
                continue
            for entry in (item.get("image_urls") or item.get("images") or []):
                url = (entry.get("url") or entry.get("image_url") or entry.get("src") or "").strip()
                if not url or url in seen:
                    continue
                images.append(((entry.get("alt") or entry.get("title") or "image").strip(), url))
                seen.add(url)
                if len(images) >= 9:
                    break
            if len(images) >= 9:
                break
        if images:
            lines += ["", "## Illustrations", ""]
            for alt, url in images:
                lines.append(f"![{alt}]({url})")

    lines.append("")
    return "\n".join(lines)


def _safe_native_fallback(query):
    try:
        from tools.web_search import web_search
    except Exception:
        return None

    try:
        payload = web_search(query, page=1, count=10, region="wt-wt", safe="moderate") or {}
        results = [r for r in payload.get("results", []) if isinstance(r, dict)]
        if not results:
            return None

        def _plain(r):
            return {
                "title": r.get("title") or "",
                "url": r.get("url") or "",
                "body": r.get("description") or r.get("snippet") or "",
                "alive": True,
            }

        plain = [_plain(r) for r in results[:8]]
        return {
            "depth": "fallback",
            "search_query": query,
            "results": plain,
            "summary": {
                "total_raw": len(plain),
                "validated": len(plain),
                "alive": len(plain),
                "validated_count": len(plain),
                "alive_count": len(plain),
            },
        }
    except Exception:
        return None


# --- Schemas (helpers, not registry calls) ---

def _schema_search_deep():
    return {
        "name": "web_search_deep",
        "description": "Deep DuckDuckGo search with optional validation, classification and composed Markdown output.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query text."},
                "validate": {"type": "boolean", "description": "Verify each result page is alive before classification."},
                "classify": {"type": "boolean", "description": "Sort results into topical buckets."},
                "max_validate": {"type": "integer", "description": "Maximum URLs to validate per round."},
                "query_variants": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional explicit query variants. If omitted, a built-in heuristic generates fallback variants.",
                },
                "compose": {
                    "type": "boolean",
                    "description": "Return composed Markdown answer instead of raw JSON. Deprecated on tool side; wrapper returns composed output when true.",
                },
            },
            "required": ["query"],
        },
    }


def _schema_visit_website_tool():
    return {
        "name": "visit_website_tool",
        "description": "Visit a URL and return its rendered content using the enhanced DuckDuckGo-backed fetcher.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "format": "uri", "description": "Target URL."},
                "extract": {"type": "boolean", "description": "Return processed markdown when supported."},
            },
            "required": ["url"],
        },
    }


def _schema_image_search():
    return {
        "name": "image_search",
        "description": "Search images by query across regions and safety settings.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Image search query."},
                "page": {"type": "integer", "description": "Results page number."},
                "count": {"type": "integer", "description": "Maximum results to return."},
                "region": {"type": "string", "description": "Region code, e.g. wt-wt."},
                "safe": {"type": "string", "description": "Safe-search strictness."},
            },
            "required": ["query"],
        },
    }


# --- Top-level module registration (must be direct calls for AST discovery) ---

registry.register(
    name="web_search_deep",
    toolset="web",
    schema=_schema_search_deep(),
    handler=lambda args, **_: (lambda result: compose_markdown(args.get("query", ""), result) if args.get("compose") else result)(
        _safe_search_deep(
            query=args.get("query", ""),
            validate=args.get("validate", True),
            classify=args.get("classify", True),
            max_validate=int(args.get("max_validate", 50)),
            query_variants=args.get("query_variants"),
        )
        or _safe_native_fallback(args.get("query", ""))
        or {"error": "search backend unavailable"}
    ),
    check_fn=lambda: search_deep is not None,
    emoji="🔎",
    max_result_size_chars=20000,
)

registry.register(
    name="visit_website_tool",
    toolset="web",
    schema=_schema_visit_website_tool(),
    handler=lambda args, **_: visit_website_enhanced(
        url=args.get("url", ""),
        extract=bool(args.get("extract", False)),
    ),
    check_fn=lambda: visit_website_enhanced is not None,
    emoji="🌍",
)

registry.register(
    name="image_search",
    toolset="web",
    schema=_schema_image_search(),
    handler=lambda args, **_: image_search(
        query=args.get("query", ""),
        page=int(args.get("page", 1)),
        count=int(args.get("count", 10)),
        region=args.get("region", "wt-wt"),
        safe=args.get("safe", "moderate"),
    ),
    check_fn=lambda: image_search is not None,
    emoji="🖼️",
)
