"""Unit tests for pure scoring/filtering helpers — no network involved."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ddg_search


def test_relevant_page_scores_positive():
    # "james free" / "free image" phrases from the query appear in the text → gate passes
    s = ddg_search.content_relevance_score(
        "Sara St James free image gallery",
        "Sara St James free image gallery and biography",
    )
    assert s > 0.0


def test_irrelevant_page_scores_zero():
    s = ddg_search.content_relevance_score(
        "Sara St James free image gallery",
        "weather forecast in london tomorrow rain",
    )
    assert s == 0.0


def test_phrase_gate_blocks_name_overlap():
    # "st" is filtered as a short token, so the 2-word phrase "sara james" is absent
    # in a page that only ever writes "Sara St James" without other query phrases.
    s = ddg_search.content_relevance_score("Sara St James", "Sara St James biography")
    assert s == 0.0  # documented gate behavior (namesake prevention); known weakness


def test_short_text_penalty_is_applied():
    full = ddg_search.content_relevance_score(
        "python programming", "python programming tutorial " * 40
    )
    short = ddg_search.content_relevance_score(
        "python programming", "python programming"
    )
    assert short < full


def test_blocked_domain():
    assert ddg_search.is_blocked_domain("https://www.bing.com/search?q=x") is True
    assert ddg_search.is_blocked_domain("https://duckduckgo.com/") is True


def test_allowlist_subdomain_rule():
    assert ddg_search.is_blocked_domain("https://github.com/project") is False
    assert ddg_search.is_blocked_domain("https://example.org") is True


def test_should_filter_url():
    assert ddg_search._should_filter_url("https://example.com/") is True          # homepage
    assert ddg_search._should_filter_url("https://example.com/search?q=x") is True  # search page
    assert ddg_search._should_filter_url("https://youtube.com/watch?v=abc") is True  # video
    assert ddg_search._should_filter_url("https://youtube.com/watch?v=abc", "video") is False  # video query keeps it
    assert ddg_search._should_filter_url("https://example.com/article") is False
