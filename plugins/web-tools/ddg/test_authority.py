"""Tests for authority/coverage placement factors (sort-only ranking help).

Pure-function tests plus the deterministic before/after ordering comparison
the honest-reporting discipline requires: the same fixed candidate list
ranked with the factor ON vs OFF must keep identical relevance VALUES and
change only the order. Run from plugins/web-tools/ddg/::

    python -m pytest test_authority.py -q
"""

import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import ddg_search as ds  # noqa: E402


def f(query, title, url, snippet="", text=""):
    return ds._authority_coverage_factor(query, title, url, snippet, text)


# ── boosts ──────────────────────────────────────────────────────────────────

def test_official_host_boost():
    # official only — the query carries no docs-seeking word
    assert f("python asyncio", "Asyncio docs",
             "https://docs.python.org/3/library/asyncio.html") == 1.6
    # official × docs-shape stacks by design ("tutorial" is docs-seeking,
    # and the docs.python.org subdomain is a docs shape)
    assert f("python asyncio tutorial", "Asyncio docs",
             "https://docs.python.org/3/library/asyncio.html") == pytest.approx(1.6 * 1.15)
    assert f("python asyncio", "Some blog",
             "https://blog.example.com/asyncio") == 1.0


def test_official_boost_fires_once_and_on_subdomains():
    assert f("python packaging", "PyPI", "https://pypi.org/") == 1.6
    # subdomain of an official host counts; second match must not stack
    assert f("python packaging", "Blog", "https://blog.python.org/x") == 1.6


def test_go_token_does_not_fire_inside_django():
    # token match, not substring: "django" must not trigger the "go" entry
    assert f("django tutorial", "Django", "https://www.djangoproject.com/") == 1.0
    # official boost only — "go web server" carries no docs-seeking word,
    # so the /doc/ path alone does not stack
    assert f("go web server", "Go",
             "https://go.dev/doc/") == 1.6


def test_docs_shaping_requires_docs_seeking_query():
    # "tutorial" IS a docs-seeking word — /docs URLs boost for it (by design)
    assert f("python tutorial", "Guide",
             "https://example.com/docs/python") == 1.15
    # a non-docs query gets no boost even on a /docs URL
    assert f("python release notes", "Guide",
             "https://example.com/docs/python") == 1.0


def test_paper_host_boost():
    assert f("attention is all you need paper", "Attention paper",
             "https://arxiv.org/abs/1706.03762") == 1.35
    assert f("attention is all you need", "Attention",
             "https://arxiv.org/abs/1706.03762") == 1.0  # no paper word


def test_phrase_in_title_boost():
    assert f("best settings for 16gb", "Best settings for 16GB cards",
             "https://example.com/x") == 1.15
    assert f("best settings for 16gb", "Unrelated title",
             "https://example.com/x") == 1.0


# ── coverage penalties ──────────────────────────────────────────────────────

def test_anchor_miss_penalized_but_variants_cover():
    q = "b-tree explained"
    assert f(q, "Some article", "https://example.com/a") == 0.3
    assert f(q, "About b-tree", "https://example.com/a") == 1.0
    assert f(q, "About b tree", "https://example.com/a") == 1.0   # spaced
    assert f(q, "Some article", "https://example.com/a",
             text="the btree structure") == 1.0                    # concatenated, from page text


def test_version_major_mismatch_penalized():
    q = "python 3.11 best settings"
    assert f(q, "Python 2.7 guide", "https://example.com/old") == 0.3
    assert f(q, "Python 3.11 settings", "https://example.com/new") == 1.0
    # result mentions no version at all → no penalty (fail-open)
    assert f(q, "Python settings guide", "https://example.com/x") == 1.0


def test_year_mismatch_penalized():
    q = "llamacpp 2026 report"
    assert f(q, "Report", "https://example.com/a",
             snippet="published 2024") == 0.3
    assert f(q, "Report", "https://example.com/a",
             snippet="updated 2026") == 1.0
    assert f(q, "Report", "https://example.com/a") == 1.0  # no year → no penalty


def test_kill_switch_disables_everything(monkeypatch):
    monkeypatch.setenv("HERMES_AUTHORITY", "0")
    assert f("python docs", "PyPI", "https://pypi.org/") == 1.0
    assert f("b-tree explained", "None", "https://x.com/a") == 1.0


def test_fail_open_on_garbage():
    assert f(None, None, None) == 1.0
    assert f("", "", "") == 1.0
    assert f("query", "t", "not a url") == 1.0


# ── the deterministic before/after ordering comparison ─────────────────────

def test_ordering_changes_but_relevance_values_do_not():
    """The honest A/B: identical candidates, factor ON vs OFF. The relevance
    VALUES must be identical in both orderings; only the order may change."""
    candidates = [
        {"title": "Random blog post about asyncio", "url": "https://blog.example.com/asyncio",
         "snippet": "asyncio basics", "text": "asyncio event loop basics", "relevance": 0.9},
        {"title": "asyncio — Python documentation", "url": "https://docs.python.org/3/library/asyncio.html",
         "snippet": "official asyncio docs", "text": "asyncio reference", "relevance": 0.5},
        {"title": "Forum thread", "url": "https://forum.example.com/t/asyncio/1",
         "snippet": "asyncio help", "text": "asyncio question", "relevance": 0.7},
    ]
    for c in candidates:
        c["authority_factor"] = f("python asyncio documentation",
                                   c["title"], c["url"], c["snippet"], c["text"])
    base_order = sorted(candidates,
                        key=lambda x: x["relevance"], reverse=True)
    auth_order = sorted(candidates,
                        key=lambda x: x["relevance"] * x["authority_factor"],
                        reverse=True)
    # relevance VALUES identical in both orderings
    assert [c["relevance"] for c in base_order] == [0.9, 0.7, 0.5]
    assert sorted(c["relevance"] for c in auth_order) == [0.5, 0.7, 0.9]
    # and the order changed: official docs (0.5) now outranks the forum (0.7)
    top_auth_url = auth_order[0]["url"]
    assert top_auth_url == candidates[1]["url"]
    # sanity: the blog (0.9) still leads base order
    assert base_order[0]["url"] == candidates[0]["url"]
