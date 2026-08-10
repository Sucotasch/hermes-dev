"""Unit tests for the topic-coverage gate in deep research.

Tests the real shared implementation (plugins/web-tools/ddg/_coverage.py),
not a duplicated copy, so the test cannot drift from production behavior.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "web-tools" / "ddg"))

from _coverage import is_coverage_sufficient


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
