"""Unit tests for selection.py — RRF, MMR, title dedup, novelty/saturation."""

from __future__ import annotations

import pytest

from selection import (
    combine_score,
    cross_encoder_scores,
    dedupe_by_normalized_title,
    is_redundant,
    mmr_select,
    novelty,
    reciprocal_rank_fusion,
    title_normalize,
    token_set,
)


# ── RRF ────────────────────────────────────────────────────────────────────


def test_rrf_boosts_cross_query_hits():
    rankings = [
        ["a.com", "b.com", "c.com"],   # a at rank 1, b at rank 2
        ["b.com", "a.com", "d.com"],   # b at rank 1, a at rank 2
    ]
    scores = reciprocal_rank_fusion(rankings, k=60)
    # a: 1/61 + 1/62 = 0.0325;  b: 1/62 + 1/61 = 0.0325 (symmetric)
    assert scores["a.com"] == pytest.approx(1 / 61 + 1 / 62)
    assert scores["b.com"] == pytest.approx(1 / 62 + 1 / 61)
    # single-hit pages score less than any double-hit page
    assert scores["c.com"] == pytest.approx(1 / 63)
    assert scores["a.com"] > scores["c.com"]
    assert scores["d.com"] == pytest.approx(1 / 63)


def test_rrf_ignores_empty_rankings():
    assert reciprocal_rank_fusion([[], [], []]) == {}
    assert reciprocal_rank_fusion([["x.com"]])["x.com"] == pytest.approx(1 / 61)


def test_combine_score_blend():
    assert combine_score(0.5, 0.5) == pytest.approx(0.5)
    assert combine_score(0.8, 0.0) == pytest.approx(0.6 * 0.8)


# ── title normalization / dedup ────────────────────────────────────────────


def test_title_normalize_strips_suffixes_and_punct():
    assert title_normalize("Sara St. James — Official Site") == "sara st james"
    assert title_normalize("Qwen 27B | News") == "qwen 27b"
    assert title_normalize("llama.cpp docs") == "llama cpp docs"
    assert title_normalize("Nothing to strip") == "nothing to strip"


def test_dedupe_keeps_first():
    items = [
        {"url": "https://a.com/1", "title": "Article | Brand News"},
        {"url": "https://b.com/2", "title": "Article — Official Site"},
        {"url": "https://c.com/3", "title": "Different Article"},
    ]
    out = dedupe_by_normalized_title(items)
    assert [i["url"] for i in out] == ["https://a.com/1", "https://c.com/3"]


# ── MMR ────────────────────────────────────────────────────────────────────


def _page(url, text, rel, title=None):
    # NOTE: title must NOT default to the URL — distinct titles would lower the
    # token-Jaccard between near-duplicate pages below 1.0 and hide redundancy.
    return {"url": url, "title": title or "", "text": text, "relevance": rel}


def test_mmr_prefers_relevance_then_diversity():
    text_a = "Qwen 27B settings guide benchmark " * 20
    text_b = "Qwen 27B settings guide benchmark " * 20   # near-duplicate of A
    text_c = "completely different unrelated topic content " * 20
    cands = [
        _page("a.com", text_a, 0.9, title="Qwen Guide"),
        _page("b.com", text_b, 0.85, title="Qwen Guide"),
        _page("c.com", text_c, 0.5, title="Unrelated"),
    ]
    # k=2: picks A (highest rel), then C (diverse) over B (redundant)
    out = mmr_select(cands, k=2)
    assert [p["url"] for p in out] == ["a.com", "c.com"]


def test_mmr_keeps_distinct_pages_from_same_domain():
    # Two genuinely different threads (low Jaccard) both survive — the
    # "multiple Reddit threads" case a hard domain quota would kill.
    t1 = "first thread about quantization gguf " * 20
    t2 = "second thread about context length vram " * 20
    cands = [
        _page("reddit.com/1", t1, 0.8),
        _page("reddit.com/2", t2, 0.75),
        _page("reddit.com/3", "first thread about quantization gguf " * 20, 0.7),
    ]
    out = mmr_select(cands, k=2)
    urls = [p["url"] for p in out]
    assert "reddit.com/1" in urls and "reddit.com/2" in urls  # distinct threads
    assert "reddit.com/3" not in urls                          # copy dropped


def test_mmr_aspect_bonus_balances_facets():
    # Second setup-page is only slightly more relevant than the benchmarks page,
    # so the aspect bonus must tip the selection toward the uncovered facet.
    cands = [
        _page("a.com", "alpha content one " * 20, 0.9),
        _page("b.com", "beta content two " * 20, 0.5),
        _page("c.com", "gamma content three " * 20, 0.8),
    ]
    for p, asp in zip(cands, ["setup", "setup", "benchmarks"]):
        p["aspect"] = asp
    out = mmr_select(cands, k=2, aspect_key="aspect")
    urls = [p["url"] for p in out]
    # benchmark facet must be represented even though its relevance is lower
    assert "c.com" in urls


def test_mmr_empty_and_k():
    assert mmr_select([], k=5) == []
    assert mmr_select([_page("a.com", "x " * 10, 0.9)], k=0) == []


# ── novelty / saturation ───────────────────────────────────────────────────


# ── optional cross-encoder rerank ─────────────────────────────────────────

class _FakeModel:
    def predict(self, pairs):
        return [float(len(p[1])) for p in pairs]  # longer text = "better"


def test_cross_encoder_noop_without_flag(monkeypatch):
    monkeypatch.delenv("DDG_RERANK", raising=False)
    monkeypatch.setattr("selection._CROSS_ENCODER", None)  # force probe
    scores = cross_encoder_scores("q", [_page("a.com", "x " * 10, 0.9)])
    assert scores == {}


def test_cross_encoder_scores_normalized(monkeypatch):
    monkeypatch.setattr("selection._CROSS_ENCODER", _FakeModel())
    cands = [
        _page("short.com", "short", 0.5),
        _page("long.com", "much longer text here " * 20, 0.5),
    ]
    scores = cross_encoder_scores("q", cands)
    assert set(scores) == {"short.com", "long.com"}
    assert 0.0 <= scores["short.com"] <= scores["long.com"] <= 1.0
    assert scores["short.com"] == 0.0 and scores["long.com"] == 1.0


def test_novelty_and_redundancy():
    text = "Qwen 27B llama.cpp settings " * 10
    dup = "Qwen 27B llama.cpp settings " * 10
    other = "totally unrelated words here " * 10
    accepted = [token_set(text)]
    assert novelty(other, accepted) > 0.5
    assert is_redundant(dup, accepted, threshold=0.7) is True
    assert is_redundant(other, accepted, threshold=0.7) is False
    # empty text is always redundant (no information to add)
    assert is_redundant("", accepted) is True
