"""Unit tests for the topic-coverage gate in deep research.

Tests the real shared implementation (plugins/web-tools/ddg/_coverage.py),
not a duplicated copy, so the test cannot drift from production behavior.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "web-tools" / "ddg"))

from _coverage import (
    aspect_boost_terms,
    aspect_coverage,
    is_coverage_sufficient,
    uncovered_aspects,
)


def test_high_coverage():
    pages = [
        {"title": "Yandex Search API docs", "text": "Yandex Search API allows image search integration free."},
        {"title": "Yandex integration", "text": "Yandex Search API, image search, Hermes integration."},
    ]
    assert is_coverage_sufficient(pages, "yandex search integration into Hermes") is True


def test_low_coverage_triggers_expand():
    pages = [
        {"title": "Hermes guide", "text": "How to run Hermes locally."},
        {"title": "Hermes plugins", "text": "Plugin system in Hermes."},
    ]
    assert is_coverage_sufficient(pages, "yandex search integration into Hermes") is False


def test_empty_pages_insufficient():
    # Empty evidence must trigger Level-2 expansion → coverage is NOT sufficient
    assert is_coverage_sufficient([], "anything") is False


def test_narrow_query_coverage():
    # 3-letter terms (api) must count — previously dropped by the len > 3 filter
    pages = [
        {"title": "Yandex API", "text": "Yandex API."},
    ]
    assert is_coverage_sufficient(pages, "Yandex API") is True


def test_none_pages_insufficient():
    assert is_coverage_sufficient(None, "yandex api") is False


# ── Aspect coverage (Level-2 targeting) ──────────────────────────────────────

_ASPECTS = [
    ("core", "crawl4ai overview"),
    ("overview how it works", "crawl4ai overview how it works"),
    ("benchmarks comparison", "crawl4ai benchmarks comparison"),
    ("troubleshooting", "crawl4ai troubleshooting"),
]


def test_aspect_coverage_counts_labels():
    pages = [
        {"title": "a", "text": "x", "aspect": "core"},
        {"title": "b", "text": "y", "aspect": "core"},
        {"title": "c", "text": "z", "aspect": "benchmarks comparison"},
    ]
    counts = aspect_coverage(pages, _ASPECTS)
    assert counts["core"] == 2
    assert counts["benchmarks comparison"] == 1
    assert counts["overview how it works"] == 0
    assert counts["troubleshooting"] == 0


def test_uncovered_aspects_preserve_order():
    pages = [{"title": "a", "text": "x", "aspect": "core"}]
    uncovered = uncovered_aspects(pages, _ASPECTS)
    # Everything except core is uncovered, in aspect declaration order.
    assert uncovered == [
        "overview how it works", "benchmarks comparison", "troubleshooting",
    ]


def test_uncovered_aspects_empty_when_all_covered():
    pages = [
        {"aspect": "core", "title": "a", "text": "x"},
        {"aspect": "overview how it works", "title": "b", "text": "y"},
        {"aspect": "benchmarks comparison", "title": "c", "text": "z"},
        {"aspect": "troubleshooting", "title": "d", "text": "w"},
    ]
    assert uncovered_aspects(pages, _ASPECTS) == []


def test_uncovered_aspects_no_aspects_noop():
    # No aspect pairs (plain variants) -> feature off, never blocks anything.
    assert uncovered_aspects([{"title": "a"}], []) == []
    assert uncovered_aspects([], None) == []


def test_aspect_boost_terms_only_uncovered():
    terms = aspect_boost_terms(_ASPECTS, ["troubleshooting", "benchmarks comparison"])
    # Words from the uncovered variants, >=4 chars, no query-core duplicates.
    assert "troubleshooting" in terms
    assert "benchmarks" in terms
    assert "comparison" in terms
    assert "overview" not in terms  # covered aspect's words excluded


def test_aspect_boost_terms_fail_open():
    assert aspect_boost_terms(None, ["x"]) == []
    assert aspect_boost_terms(_ASPECTS, []) == []


def test_unlabeled_pages_count_as_core():
    # Pages without an aspect label (e.g. sitemap-seeded Level-2 items) count
    # toward "core" — the base-query facet.
    pages = [{"title": "a", "text": "x"}]
    counts = aspect_coverage(pages, _ASPECTS)
    assert counts["core"] == 1
    assert uncovered_aspects(pages, _ASPECTS) == [
        "overview how it works", "benchmarks comparison", "troubleshooting",
    ]
