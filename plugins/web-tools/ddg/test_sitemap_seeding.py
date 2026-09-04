#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for sitemap_seeding (Crawl4AI-inspired URL seeding port).

All network access is injected/mocked — no live requests. Tests cover:
robots.txt parsing, sitemap index fan-out, bounds, junk filtering,
BM25 ranking, and the top-level seed_urls_for_query fail-open behavior.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sitemap_seeding


# --- helpers ---------------------------------------------------------------

def _robots(*sitemap_lines):
    lines = ["User-agent: *", "Disallow: /private/"]
    lines += [f"Sitemap: {u}" for u in sitemap_lines]
    return "\n".join(lines) + "\n"


def _urlset(*urls):
    items = "".join(
        f"<url><loc>{u}</loc><lastmod>2026-01-01</lastmod></url>" for u in urls
    )
    return f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{items}</urlset>'


def _index(*urls):
    items = "".join(f"<sitemap><loc>{u}</loc></sitemap>" for u in urls)
    return f'<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{items}</sitemapindex>'


class FakeResponses:
    """url -> response text. Unknown URLs -> None (like a network failure)."""

    def __init__(self, mapping):
        self.mapping = dict(mapping)
        self.calls = []

    def __call__(self, url, timeout=sitemap_seeding._REQUEST_TIMEOUT):
        self.calls.append(url)
        return self.mapping.get(url)


# --- robots.txt sitemap discovery --------------------------------------------

def test_robots_sitemap_lines(monkeypatch):
    resp = FakeResponses({
        "https://example.com/robots.txt": _robots("https://example.com/s1.xml", "https://example.com/s2.xml"),
    })
    monkeypatch.setattr(sitemap_seeding, "_http_get", resp)
    urls = sitemap_seeding._sitemap_urls_from_robots("example.com")
    assert urls == ["https://example.com/s1.xml", "https://example.com/s2.xml"]


def test_robots_absent_falls_back_to_conventions(monkeypatch):
    resp = FakeResponses({})  # robots.txt unfetchable
    monkeypatch.setattr(sitemap_seeding, "_http_get", resp)
    urls = sitemap_seeding._sitemap_urls_from_robots("example.com")
    assert urls == []  # caller falls back to /sitemap.xml + /sitemap_index.xml


def test_default_sitemap_candidates():
    cands = sitemap_seeding._default_sitemap_candidates("example.com")
    assert "https://example.com/sitemap.xml" in cands
    assert sitemap_seeding._default_sitemap_candidates("") == []


# --- sitemap collection ------------------------------------------------------

def test_collect_simple_urlset(monkeypatch):
    resp = FakeResponses({
        "https://example.com/sitemap.xml": _urlset(
            "https://example.com/a", "https://example.com/b/c", "https://example.com/d.html",
        ),
    })
    monkeypatch.setattr(sitemap_seeding, "_http_get", resp)
    out = []
    sitemap_seeding._collect_sitemap_locs(
        "https://example.com/sitemap.xml", 0, [0], out, deadline=1e18
    )
    assert out == ["https://example.com/a", "https://example.com/b/c", "https://example.com/d.html"]


def test_collect_sitemap_index_fanout(monkeypatch):
    resp = FakeResponses({
        "https://example.com/index.xml": _index(
            "https://example.com/s1.xml", "https://example.com/s2.xml",
        ),
        "https://example.com/s1.xml": _urlset("https://example.com/x"),
        "https://example.com/s2.xml": _urlset("https://example.com/y"),
    })
    monkeypatch.setattr(sitemap_seeding, "_http_get", resp)
    out = []
    sitemap_seeding._collect_sitemap_locs(
        "https://example.com/index.xml", 0, [0], out, deadline=1e18
    )
    assert sorted(out) == ["https://example.com/x", "https://example.com/y"]


def test_collect_respects_max_sitemaps(monkeypatch):
    # A sitemap index pointing at 10 sub-sitemaps: only _MAX_SITEMAPS fetched.
    resp = FakeResponses({
        "https://example.com/index.xml": _index(
            *[f"https://example.com/s{i}.xml" for i in range(10)]
        ),
        **{f"https://example.com/s{i}.xml": _urlset(f"https://example.com/p{i}")
           for i in range(10)},
    })
    monkeypatch.setattr(sitemap_seeding, "_http_get", resp)
    out = []
    fetched = [0]
    sitemap_seeding._collect_sitemap_locs(
        "https://example.com/index.xml", 0, fetched, out, deadline=1e18
    )
    assert fetched[0] <= sitemap_seeding._MAX_SITEMAPS + 1  # index + children
    assert len(out) <= sitemap_seeding._MAX_SITEMAPS


def test_collect_depth_bounded(monkeypatch):
    # index -> index -> urlset must stop at depth 1 (no double-index nesting).
    resp = FakeResponses({
        "https://example.com/i1.xml": _index("https://example.com/i2.xml"),
        "https://example.com/i2.xml": _index("https://example.com/i3.xml"),
        "https://example.com/i3.xml": _urlset("https://example.com/deep"),
    })
    monkeypatch.setattr(sitemap_seeding, "_http_get", resp)
    out = []
    sitemap_seeding._collect_sitemap_locs(
        "https://example.com/i1.xml", 0, [0], out, deadline=1e18
    )
    assert out == []  # i2 is fetched at depth 1; its index children not followed


def test_collect_stops_at_max_urls(monkeypatch):
    many = [f"https://example.com/p/{i}" for i in range(1000)]
    resp = FakeResponses({"https://example.com/sitemap.xml": _urlset(*many)})
    monkeypatch.setattr(sitemap_seeding, "_http_get", resp)
    out = []
    sitemap_seeding._collect_sitemap_locs(
        "https://example.com/sitemap.xml", 0, [0], out, deadline=1e18
    )
    assert len(out) == sitemap_seeding._MAX_URLS


# --- domain collection (robots + junk filter + page filter) -------------------

def test_collect_domain_urls_filters_non_pages(monkeypatch):
    resp = FakeResponses({
        "https://example.com/robots.txt": _robots("https://example.com/sitemap.xml"),
        "https://example.com/sitemap.xml": _urlset(
            "https://example.com/good-page",
            "https://example.com/img/photo.jpg",      # non-page suffix
            "https://example.com/style.css",          # non-page suffix
        ),
    })
    monkeypatch.setattr(sitemap_seeding, "_http_get", resp)
    urls = sitemap_seeding.collect_domain_urls("example.com")
    assert urls == ["https://example.com/good-page"]


def test_collect_domain_urls_skips_junk(monkeypatch):
    resp = FakeResponses({
        "https://example.com/robots.txt": _robots("https://example.com/sitemap.xml"),
        "https://example.com/sitemap.xml": _urlset(
            "https://example.com/legal/privacy",      # junk_filter skip segment
            "https://example.com/login",
            "https://example.com/blog/post-1",        # keep
        ),
    })
    monkeypatch.setattr(sitemap_seeding, "_http_get", resp)
    urls = sitemap_seeding.collect_domain_urls("example.com")
    assert urls == ["https://example.com/blog/post-1"]


def test_collect_domain_urls_network_failure(monkeypatch):
    monkeypatch.setattr(sitemap_seeding, "_http_get", FakeResponses({}))
    assert sitemap_seeding.collect_domain_urls("nonexistent.invalid") == []


# --- BM25 ranking --------------------------------------------------------------

def test_rank_urls_bm25_prefers_matching_paths():
    urls = [
        "https://example.com/blog/cooking-recipes",
        "https://example.com/about/team",
        "https://example.com/blog/recipes-for-beginners",
        "https://example.com/contact",
    ]
    ranked = sitemap_seeding._rank_urls_bm25("recipes for beginners", urls, top_n=2)
    # Both recipe URLs must outrank /about/team and /contact (exact order
    # between the two recipe URLs is BM25's length-normalization call).
    assert set(ranked) == {
        "https://example.com/blog/cooking-recipes",
        "https://example.com/blog/recipes-for-beginners",
    }


def test_rank_urls_bm25_empty_inputs():
    assert sitemap_seeding._rank_urls_bm25("", ["https://x/y"]) == []
    assert sitemap_seeding._rank_urls_bm25("q", []) == []


def test_rank_urls_bm25_fallback_on_missing_module(monkeypatch):
    # evidence_rank import failure must degrade to the substring fallback.
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "evidence_rank":
            raise ImportError("no evidence_rank")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    urls = ["https://example.com/a/python-tutorial", "https://example.com/b/nothing"]
    ranked = sitemap_seeding._rank_urls_bm25("python tutorial", urls, top_n=2)
    assert ranked[0] == "https://example.com/a/python-tutorial"


# --- seed_urls_for_query (top-level) ------------------------------------------

def test_seed_urls_for_query_end_to_end(monkeypatch):
    resp = FakeResponses({
        "https://blog.example.com/robots.txt": _robots("https://blog.example.com/sitemap.xml"),
        "https://blog.example.com/sitemap.xml": _urlset(
            "https://blog.example.com/async-context-managers-guide",
            "https://blog.example.com/unrelated-post",
            "https://blog.example.com/python-async-patterns",
        ),
    })
    monkeypatch.setattr(sitemap_seeding, "_http_get", resp)
    out = sitemap_seeding.seed_urls_for_query(
        "python async context managers",
        ["https://blog.example.com/some-l1-page"],
    )
    urls = [u for u, _ in out]
    assert len(urls) >= 1
    assert urls[0] in (
        "https://blog.example.com/async-context-managers-guide",
        "https://blog.example.com/python-async-patterns",
    )
    assert all(isinstance(d, str) for _, d in out)


def test_seed_urls_www_normalized(monkeypatch):
    resp = FakeResponses({
        "https://example.com/robots.txt": _robots("https://example.com/sitemap.xml"),
        "https://example.com/sitemap.xml": _urlset("https://example.com/a"),
    })
    monkeypatch.setattr(sitemap_seeding, "_http_get", resp)
    out = sitemap_seeding.seed_urls_for_query(
        "anything", ["https://www.example.com/page"]
    )
    assert [u for u, _ in out] == ["https://example.com/a"]


def test_seed_urls_dedupes_across_domains(monkeypatch):
    resp = FakeResponses({
        "https://a.example.com/robots.txt": _robots("https://a.example.com/sitemap.xml"),
        "https://a.example.com/sitemap.xml": _urlset("https://shared.example.com/doc"),
        "https://b.example.com/robots.txt": _robots("https://b.example.com/sitemap.xml"),
        "https://b.example.com/sitemap.xml": _urlset("https://shared.example.com/doc"),
    })
    monkeypatch.setattr(sitemap_seeding, "_http_get", resp)
    out = sitemap_seeding.seed_urls_for_query(
        "shared doc", ["https://a.example.com/x", "https://b.example.com/y"]
    )
    urls = [u for u, _ in out]
    assert urls.count("https://shared.example.com/doc") == 1


def test_seed_urls_fail_open(monkeypatch):
    # Total failure anywhere -> [] (never raises, hyperlink fallback continues).
    def explode(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(sitemap_seeding, "_http_get", explode)
    out = sitemap_seeding.seed_urls_for_query("q", ["https://x.example.com/"])
    assert out == []


def test_seed_urls_empty_inputs():
    assert sitemap_seeding.seed_urls_for_query("", ["https://a.com/"]) == []
    assert sitemap_seeding.seed_urls_for_query("q", []) == []
    assert sitemap_seeding.seed_urls_for_query("q", None) == []


def test_seed_urls_respects_max_domains(monkeypatch):
    resp = FakeResponses({
        **{
            f"https://d{i}.example.com/robots.txt": _robots(f"https://d{i}.example.com/sitemap.xml")
            for i in range(8)
        },
        **{
            f"https://d{i}.example.com/sitemap.xml": _urlset(f"https://d{i}.example.com/p")
            for i in range(8)
        },
    })
    monkeypatch.setattr(sitemap_seeding, "_http_get", resp)
    out = sitemap_seeding.seed_urls_for_query(
        "test query",
        [f"https://d{i}.example.com/page" for i in range(8)],
        max_domains=2,
    )
    domains_seen = {d for _, d in out}
    assert len(domains_seen) <= 2


# --- gzip sitemaps + robots.txt passthrough (post-review fixes) ---------------

def test_http_get_plain_text_robots_passthrough(monkeypatch):
    # robots.txt has no <loc>/<urlset> markers — _http_get must NOT sniff
    # content structure and reject it (the original bug: robots was always
    # None, so sitemap discovery silently fell back to conventions only).
    import urllib.request

    class FakeResp:
        def __init__(self, body):
            self._body = body

        def read(self, limit=-1):
            return self._body[:limit] if limit and limit > 0 else self._body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        assert "robots.txt" in req.full_url
        return FakeResp(b"User-agent: *\nDisallow: /private\nSitemap: https://example.com/sitemap.xml\n")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    urls = sitemap_seeding._sitemap_urls_from_robots("example.com")
    assert urls == ["https://example.com/sitemap.xml"]


def test_http_get_gzip_sitemap(monkeypatch):
    import gzip
    import urllib.request

    xml = _urlset("https://example.com/good-page")
    gz_body = gzip.compress(xml.encode("utf-8"))

    class FakeResp:
        def read(self, limit=-1):
            return gz_body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: FakeResp())
    text = sitemap_seeding._http_get("https://example.com/sitemap.xml.gz")
    assert text is not None
    assert "<loc>https://example.com/good-page</loc>" in text


def test_http_get_gzip_bomb_guard(monkeypatch):
    # A gzip stream that decompresses past the cap must be rejected, not OOM.
    import gzip
    import urllib.request

    bomb = gzip.compress(b"<urlset>" + b"<url><loc>https://example.com/p</loc></url>" * 500000 + b"</urlset>")

    class FakeResp:
        def read(self, limit=-1):
            return bomb[:limit] if limit and limit > 0 else bomb

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: FakeResp())
    assert sitemap_seeding._http_get("https://example.com/sitemap.xml.gz") is None


def test_http_get_rejects_oversized_body(monkeypatch):
    import urllib.request

    class FakeResp:
        def read(self, limit=-1):
            return b"x" * (limit + 1)  # always one byte over any cap

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: FakeResp())
    assert sitemap_seeding._http_get("https://example.com/sitemap.xml") is None


def test_domain_budget_split_across_domains(monkeypatch):
    # The overall deadline caps per-domain budgets: the first domain gets the
    # _DOMAIN_TIME_BUDGET cap, and once the overall clock runs out the next
    # domain is skipped. (Deadline = max(6, 4*max_domains) by design, so the
    # fake clock must jump past THAT, not past 2 domains' worth.)
    import time as _time

    real_monotonic = _time.monotonic
    state = {"base": real_monotonic(), "n": 0.0}

    def fake_clock():
        return state["base"] + state["n"]

    monkeypatch.setattr(_time, "monotonic", fake_clock)

    budgets = []

    def fake_collect(domain, time_budget=6.0):
        budgets.append((domain, time_budget))
        if domain == "a.example.com":
            # first domain "consumes" the whole overall deadline (4*max_domains)
            state["n"] += 17.0
            return ["https://a.example.com/a"]
        return []

    monkeypatch.setattr(sitemap_seeding, "collect_domain_urls", fake_collect)
    out = sitemap_seeding.seed_urls_for_query(
        "page", ["https://a.example.com/x", "https://b.example.com/y"]
    )
    urls = [u for u, _ in out]
    assert "https://a.example.com/a" in urls
    # First domain got the capped per-domain budget...
    assert budgets[0] == ("a.example.com", sitemap_seeding._DOMAIN_TIME_BUDGET)
    # ...and the second domain was skipped once the overall budget ran out.
    assert len(budgets) == 1
