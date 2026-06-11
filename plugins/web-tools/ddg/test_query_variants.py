import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from query_variants import generate


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
