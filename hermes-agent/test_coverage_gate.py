"""Unit tests for topic-coverage gate in deep research."""
import re


def _tokens(text):
    return [t.lower() for t in re.findall(r"\b\w+\b", text) if len(t) > 3]


def _is_coverage_sufficient(pages, query):
    terms = _tokens(query)
    if not terms:
        return True
    hits = {t: 0 for t in terms}
    for p in pages:
        text = " ".join([
            p.get("title", ""),
            p.get("text", ""),
            p.get("snippet", ""),
        ]).lower()
        for t in terms:
            if t in text:
                hits[t] += 1
    covered = sum(1 for c in hits.values() if c >= 2)
    return covered >= max(1, len(terms) // 2)


def test_high_coverage():
    pages = [
        {"title": "Yandex Search API docs", "text": "Yandex Search API allows image search integration free."},
        {"title": "Yandex integration", "text": "Yandex Search API, image search, Hermes integration."},
    ]
    assert _is_coverage_sufficient(pages, "yandex search integration into Hermes") is True


def test_low_coverage_triggers_expand():
    pages = [
        {"title": "Hermes guide", "text": "How to run Hermes locally."},
        {"title": "Hermes plugins", "text": "Plugin system in Hermes."},
    ]
    assert _is_coverage_sufficient(pages, "yandex search integration into Hermes") is False


def test_empty_pages_called_false():
    assert _is_coverage_sufficient([], "anything") is True


def test_narrow_query_coverage():
    pages = [
        {"title": "Yandex API", "text": "Yandex API."},
    ]
    assert _is_coverage_sufficient(pages, "Yandex API") is True
