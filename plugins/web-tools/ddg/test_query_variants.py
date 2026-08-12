import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import query_variants
from query_variants import generate


def test_suggest_refines_thin_pool_with_frequent_terms():
    # Pool heavily skewed toward one facet ("gallery"): refinement should
    # propose re-searching the under-represented facets found in snippets.
    raw = [
        {"title": "Vargas gallery photos", "url": "https://a/1", "snippet": "classic pinup artwork gallery"},
        {"title": "Vargas gallery photos 2", "url": "https://a/2", "snippet": "more pinup artwork gallery"},
        {"title": "Vargas history article", "url": "https://b/1", "snippet": "career retrospective"},
    ]
    suggested = query_variants._suggest_query_variants("Vargas pinup artist", raw, max_variants=3)
    assert suggested, "thin pool must yield refinement queries"
    # Every suggestion stays anchored to the core query and is distinct
    assert all("vargas" in s.lower() for s in suggested)
    assert len(suggested) == len(set(s.lower() for s in suggested))
    # Single-occurrence terms must not leak into suggestions ("history" and
    # "career" each appear once across the whole pool)
    assert not any("history" in s.lower() for s in suggested)
    assert not any("career" in s.lower() for s in suggested)


def test_suggest_empty_and_covered_pool():
    assert query_variants._suggest_query_variants("", [{"title": "x", "url": "u", "snippet": "y"}]) == []
    # Query terms already present in every item -> no novel facet to re-search
    raw = [{"title": "vargas pinup artist a", "url": "u1", "snippet": ""},
           {"title": "vargas pinup artist b", "url": "u2", "snippet": ""}]
    assert query_variants._suggest_query_variants("vargas pinup artist", raw) == []


def test_narrow_query_returns_intent_variants():
    q = "yandex search integration into Hermes agent free without external services"
    variants = generate(q)
    assert any("yandex" in v.lower() and "integration" in v.lower() for v in variants), variants
    assert not any(v.endswith(" history") or v.endswith(" trends") for v in variants), variants
    assert len(variants) >= 2


def test_broad_query_allows_generic():
    q = "python"
    variants = generate(q)
    assert len(variants) >= 1
    assert q in variants


def test_empty_query():
    assert generate("") == []


def test_returns_list_of_strings():
    variants = generate("test query")
    assert isinstance(variants, list)
    assert all(isinstance(v, str) and v for v in variants)
