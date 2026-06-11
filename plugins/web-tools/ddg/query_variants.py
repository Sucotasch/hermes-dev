"""Intent-aware query variant generator for deep research.

Generates focused search variants from a single query using intent keywords
for narrow/technical queries. Falls back to minimal generic suffixes only for
broad/short queries.
"""
from __future__ import annotations

import re

INTENT_SUFFIXES = [
    "API",
    "without API key",
    "free",
    "open source",
    "image search",
    "integration",
    "setup",
    "documentation",
    "scraping",
    "library",
]
BROAD_SUFFIXES = ["history", "trends", "examples", "best resources"]
MAX_VARIANTS = 5
MIN_TOKENS_FOR_INTENT = 4


def _is_broad(query: str) -> bool:
    tokens = [t for t in re.findall(r"\b\w+\b", query.lower()) if len(t) > 2]
    return len(tokens) < MIN_TOKENS_FOR_INTENT


def generate(query: str):
    if not query or not query.strip():
        return []
    tokens = [t for t in re.findall(r"\b\w+\b", query.lower()) if len(t) > 2]
    if not tokens:
        return [query.strip()]
    base = query.strip().rstrip(".")
    variants = [base]
    if _is_broad(query):
        candidates = [f"{tokens[0] if tokens else ''} {s}" for s in BROAD_SUFFIXES]
    else:
        core = " ".join(tokens[:4])
        candidates = []
        seen = {core.lower()}
        for s in INTENT_SUFFIXES:
            candidate = f"{core} {s}"
            if candidate.lower() not in seen:
                seen.add(candidate.lower())
                candidates.append(candidate)
    for c in candidates:
        if c not in variants:
            variants.append(c)
    return variants[:MAX_VARIANTS]
