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


def _is_broad(query: str) -> bool:
    tokens = [t for t in re.findall(r"\b\w+\b", query.lower()) if len(t) > 2]
    return len(tokens) < MIN_TOKENS_FOR_INTENT


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
