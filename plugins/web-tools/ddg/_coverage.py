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
