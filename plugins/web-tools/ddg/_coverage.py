"""Topic-coverage gate shared by the Hermes wrapper and its tests.

Kept dependency-free (stdlib only) so tests exercise the exact code used in
production instead of a duplicated copy.
"""
from __future__ import annotations

import re


def _tokens(text: str) -> list:
    """Significant query tokens (>= 3 chars keeps terms like 'api')."""
    return [t.lower() for t in re.findall(r"\b\w+\b", text) if len(t) >= 3]


def is_coverage_sufficient(pages, query: str) -> bool:
    """True when query terms appear in >= 2 pages each, across at least half the terms.

    Used to decide whether Level-2 expansion is needed.
    """
    terms = _tokens(query)
    if not terms:
        return True
    hits = {t: 0 for t in terms}
    page_texts = []
    for p in pages or []:
        text = " ".join([
            p.get("title", ""),
            p.get("text", ""),
            p.get("snippet", ""),
        ]).lower()
        page_texts.append(text)
        for t in terms:
            if t in text:
                hits[t] += 1
    # A single page covering every query term is sufficient (narrow queries)
    if any(all(t in text for t in terms) for text in page_texts):
        return True
    covered = sum(1 for count in hits.values() if count >= 2)
    return covered >= max(1, len(terms) // 2)


# ── Aspect coverage (Crawl4AI AdaptiveCrawler-inspired, minimal port) ─────────
#
# The wrapper searches the query per aspect (query_variants.generate_with_aspects
# tags each variant with a facet label). Level-1 evidence carries that label in
# `page["aspect"]`. These helpers report which facets ended up with zero
# evidence so Level-2 expansion can target them (sitemap seeding query boost +
# reporting in the panel). Purely additive — is_coverage_sufficient unchanged.

# An aspect counts as covered when at least this many distinct pages carry its
# label (core counts too: "core" is the base query facet).
_ASPECT_COVERED_PAGES = 1


def aspect_coverage(pages, aspects):
    """Map aspect label -> number of distinct evidence pages carrying it.

    ``aspects`` is the list of (aspect_label, variant_query) pairs the wrapper
    searched. Pages without an aspect label count toward "core". Fail-open:
    any malformed input returns {} (caller treats everything as uncovered).
    """
    try:
        labels = [str(a) for a, _ in aspects] if aspects else []
        if not labels:
            return {}
        counts = {label: 0 for label in labels}
        for p in pages or []:
            if not isinstance(p, dict):
                continue
            label = str(p.get("aspect") or "core")
            if label in counts:
                counts[label] += 1
        return counts
    except Exception:
        return {}


def uncovered_aspects(pages, aspects):
    """Aspect labels with zero evidence pages, preserving aspect order.

    Returns []. When ``aspects`` is empty/None the feature is a no-op —
    single-facet queries never trigger aspect-targeted expansion.
    """
    counts = aspect_coverage(pages, aspects)
    if not counts:
        return []
    return [
        label for label, n in counts.items()
        if n < _ASPECT_COVERED_PAGES
    ]


def aspect_boost_terms(aspects, uncovered):
    """Extra search terms harvested from uncovered aspect variants.

    The variant for an uncovered aspect is ``"<query core> <aspect words>"``;
    its aspect-specific words (e.g. "benchmarks comparison") are useful as a
    scoring boost for Level-2 candidates. Returns a flat list of significant
    words, deduplicated, [] on any error.
    """
    try:
        if not aspects or not uncovered:
            return []
        uncovered_set = set(uncovered)
        terms = []
        seen = set()
        for label, variant in aspects:
            if str(label) not in uncovered_set:
                continue
            for t in re.findall(r"\b\w+\b", str(variant or "").lower()):
                if len(t) >= 4 and t not in seen:
                    seen.add(t)
                    terms.append(t)
        return terms
    except Exception:
        return []
