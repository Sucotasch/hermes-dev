"""Intent-aware query variant generator for deep research.

Generates focused search variants from a single query using intent keywords
for narrow/technical queries. Falls back to minimal generic suffixes only for
broad/short queries. Supports query_type-aware suffixes.
"""
from __future__ import annotations

import re

# Type-specific suffixes
TYPE_SUFFIXES = {
    "technical": [
        "API", "without API key", "open source", "integration",
        "setup", "documentation", "scraping", "library",
    ],
    "visual": [
        "free gallery photos portfolio", "high resolution original",
        "wallpapers collection", "art exhibition showcase",
    ],
    "person": [
        "career biography filmography", "free gallery photos portraits",
        "personal life interview", "aliases stage names real name",
    ],
    "historical": [
        "history origins timeline", "detailed chronology evolution",
        "archival sources primary", "background context facts",
    ],
    "news": [
        "latest news recent", "current status today",
        "developments updates", "official announcements",
    ],
    "comparison": [
        "vs alternative comparison", "pros cons advantages",
        "detailed analysis benchmark", "which is better",
    ],
    "video": [
        "video sources clips footage", "watch online stream full",
        "video archive collection", "trailer official release",
    ],
    "general": [
        "free", "image search", "examples", "best resources",
    ],
}

BROAD_SUFFIXES = ["history", "trends", "examples", "best resources"]
MAX_VARIANTS = 5
MIN_TOKENS_FOR_INTENT = 4

# Aspect-based decomposition (STORM-lite): each aspect is one facet of the
# research topic. The Hermes wrapper searches each aspect separately and then
# balances evidence across facets via MMR's aspect_key, so a multi-faceted
# query gets coverage of every facet instead of 18 pages about one of them.
# Keep each aspect SHORT so variants stay focused.
ASPECTS = {
    "technical": ["overview how it works", "setup configuration", "benchmarks comparison", "troubleshooting"],
    "news": ["latest updates", "background context", "analysis reaction"],
    "visual": ["gallery photos", "sources pages", "biography facts"],
    "historical": ["origins timeline", "key figures events", "primary sources archives"],
    "comparison": ["feature comparison", "pros cons", "benchmarks tests"],
    "video": ["video sources clips", "watch online full", "official trailers"],
    "general": ["overview", "examples", "history", "current status"],
}


def generate_with_aspects(query: str, query_type: str = "general"):
    """Return list of (aspect_label, variant_query); base query has aspect 'core'.

    Replaces the flat suffix variants with facet-labelled ones so the evidence
    selection can balance coverage across aspects. Falls back gracefully when
    called with an unknown query_type (uses 'general' aspects).
    """
    if not query or not query.strip():
        return []
    tokens = [t for t in re.findall(r"\b\w+\b", query.lower()) if len(t) > 2]
    core = " ".join(tokens[:4]) if tokens else query.strip()
    base = query.strip().rstrip(".")
    pairs = [("core", base)]
    seen = {base.lower()}
    for aspect in ASPECTS.get(query_type, ASPECTS["general"]):
        variant = f"{core} {aspect}"
        if variant.lower() not in seen:
            seen.add(variant.lower())
            pairs.append((aspect, variant))
        if len(pairs) >= MAX_VARIANTS:
            break
    return pairs


def _is_broad(query: str) -> bool:
    tokens = [t for t in re.findall(r"\b\w+\b", query.lower()) if len(t) > 2]
    return len(tokens) < MIN_TOKENS_FOR_INTENT


# Small stopword set for result-driven refinement — self-contained so
# query_variants stays importable from ddg_search without circular imports.
_REFINE_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "were",
    "have", "has", "had", "its", "but", "not", "you", "your", "all", "can",
    "just", "more", "most", "how", "what", "when", "where", "who", "why",
    "about", "into", "over", "under", "also", "than", "then", "them", "they",
    "their", "there", "these", "those", "will", "would", "could", "should",
    "page", "pages", "site", "sites", "www", "com", "org", "net", "html",
    "home", "main", "menu", "index", "article", "articles", "post", "posts",
    "blog", "video", "videos", "photo", "photos", "picture", "pictures",
    "image", "images", "gallery", "free", "new", "news", "info", "information",
    "best", "top", "guide", "guides", "review", "reviews", "related", "via",
}


def _suggest_query_variants(query: str, raw_results, max_variants: int = 3) -> list[str]:
    """Refine a thin result pool: re-search facets the pool under-represents.

    ``raw_results`` is a list of dicts with title/url/snippet (as produced by
    ``ddg_search.search_deep``). High-signal terms are tokens that occur in
    >= 2 raw items, are not stopwords and are not already part of the original
    query. The most frequent such terms are appended to a short core query;
    each suggestion triggers one extra ``web_search`` in the caller, so the
    number is capped at ``max_variants``. Returns [] when the pool already
    covers the query (no refinement worth an extra request).
    """
    if not query or not query.strip() or not raw_results:
        return []
    query_tokens = set(re.findall(r"\b\w+\b", query.lower()))
    core = " ".join([t for t in re.findall(r"\b\w+\b", query.lower()) if len(t) > 2][:4])
    if not core:
        return []

    freq: dict[str, int] = {}
    for item in raw_results:
        text = " ".join([
            str(item.get("title") or ""),
            str(item.get("snippet") or ""),
        ]).lower()
        # Per-item frequency: a term counts once per result even when it shows
        # up in both title and snippet — "career history" pages are one hit,
        # not two signals.
        seen_in_item: set[str] = set()
        for w in re.findall(r"\b\w+\b", text):
            if len(w) < 3 or w in _REFINE_STOPWORDS or w in query_tokens:
                continue
            if w not in seen_in_item:
                seen_in_item.add(w)
                freq[w] = freq.get(w, 0) + 1

    # Only multi-occurrence terms are real facets worth an extra request:
    # a term found in a single result is more likely noise than a gap.
    ranked = sorted(
        (kv for kv in freq.items() if kv[1] >= 2),
        key=lambda kv: (-kv[1], kv[0]),
    )
    suggested: list[str] = []
    seen: set[str] = {core.lower()}
    for term, _ in ranked:
        candidate = f"{core} {term}"
        if candidate.lower() in seen:
            continue
        seen.add(candidate.lower())
        suggested.append(candidate)
        if len(suggested) >= max_variants:
            break
    return suggested


def generate(query: str, query_type: str = "general"):
    if not query or not query.strip():
        return []
    tokens = [t for t in re.findall(r"\b\w+\b", query.lower()) if len(t) > 2]
    if not tokens:
        return [query.strip()]
    base = query.strip().rstrip(".")
    variants = [base]

    # Get type-specific suffixes
    type_suffixes = TYPE_SUFFIXES.get(query_type, TYPE_SUFFIXES["general"])

    if _is_broad(query):
        candidates = [f"{tokens[0] if tokens else ''} {s}" for s in BROAD_SUFFIXES]
    else:
        core = " ".join(tokens[:4])
        candidates = []
        seen = {core.lower()}
        for s in type_suffixes:
            candidate = f"{core} {s}"
            if candidate.lower() not in seen:
                seen.add(candidate.lower())
                candidates.append(candidate)
    for c in candidates:
        if c not in variants:
            variants.append(c)
    return variants[:MAX_VARIANTS]
