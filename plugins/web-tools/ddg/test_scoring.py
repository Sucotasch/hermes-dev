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


def test_phrase_gate_passes_short_middle_word():
    # "St" (2-letter) participates in phrases: "sara st" matches "Sara St.
    # James" — this was a documented weakness (0.0) before the fix.
    s = ddg_search.content_relevance_score("Sara St James", "Sara St. James biography")
    assert s > 0.0


def test_phrase_gate_blocks_name_overlap():
    # Namesake prevention still holds: "James St. John" ≠ "Sara St James"
    # (the "st james" phrase is absent in reversed word order).
    s = ddg_search.content_relevance_score(
        "Sara St James", "James St. John is a photographer from St. Louis"
    )
    assert s == 0.0


def test_short_words_never_score_alone():
    # 2-letter words (st/in/of) only join phrases — they never earn points on
    # their own, so a page full of them but without long query words scores 0.
    s = ddg_search.content_relevance_score(
        "Sara St James",
        "The St. Regis is in the heart of the city. In summer it is best to stay in the old town.",
    )
    assert s == 0.0


def test_punctuation_normalized_technical_tokens():
    # Dotted/hyphenated tokens match after punctuation normalization:
    # "llama.cpp" → "llama cpp", "Qwen3.6-27B" → "qwen3 6 27b".
    s = ddg_search.content_relevance_score(
        "Qwen3.6-27B-Q4.GGUF64k context llama.cpp best settings for",
        "Running Qwen 3.6 27B locally with llama.cpp. Best settings: context 64k, Q4 GGUF.",
    )
    assert s > 0.0


def test_unicode_text_not_broken_by_normalization():
    # Punctuation normalization must be Unicode-safe: Cyrillic words survive.
    s = ddg_search.content_relevance_score(
        "Супермодель биография", "Супермодель, биография и карьера на русской странице."
    )
    assert s > 0.0


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
