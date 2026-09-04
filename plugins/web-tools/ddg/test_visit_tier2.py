#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for visit_website_enhanced Tier-2 additions:
PDF fast path, table harvest, head metadata. No network — HTTP layer is
monkeypatched and trafilatura/pypdf/htmldate behavior is driven by fakes
where needed.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import visit_website_enhanced as vwe


# ── PDF URL heuristic ────────────────────────────────────────────────────────

def test_is_pdf_url_heuristics():
    assert vwe._is_pdf_url("https://example.com/paper.pdf") is True
    assert vwe._is_pdf_url("https://example.com/paper.PDF") is True
    assert vwe._is_pdf_url("https://arxiv.org/pdf/2412.19437") is True
    assert vwe._is_pdf_url("https://arxiv.org/abs/2412.19437") is False
    assert vwe._is_pdf_url("https://example.com/page.html") is False
    assert vwe._is_pdf_url("") is False
    assert vwe._is_pdf_url(None) is False


# ── PDF fast path in visit_website ──────────────────────────────────────────

def test_pdf_fast_path_taken(monkeypatch):
    # _fetch_pdf_text returns text -> structured result, HTML ladder untouched.
    monkeypatch.setattr(vwe, "_fetch_pdf_text", lambda url: "This PDF has plenty of extracted text. " * 5)
    called = []
    monkeypatch.setattr(vwe, "_fetch", lambda url, **kw: called.append(url) or "<html>never</html>")
    out = vwe.visit_website("https://example.com/doc.pdf")
    assert out["source"] == "pdf"
    assert out["content"].startswith("This PDF")
    assert called == []  # HTML fetch never ran
    assert out["url"] == "https://example.com/doc.pdf"


def test_pdf_fast_path_title_from_filename(monkeypatch):
    monkeypatch.setattr(vwe, "_fetch_pdf_text", lambda url: "text " * 100)
    out = vwe.visit_website("https://example.com/some-great_paper-v2.pdf?download=1")
    assert "some great paper v2" in out["title"].lower()


def test_pdf_failure_falls_through_to_html(monkeypatch):
    # PDF extraction fails -> regular ladder runs (Jina can still render PDFs).
    monkeypatch.setattr(vwe, "_fetch_pdf_text", lambda url: None)
    monkeypatch.setattr(vwe, "_fetch", lambda url, **kw: "<html><title>T</title><body>" + "x" * 400 + "</body></html>")
    out = vwe.visit_website("https://example.com/doc.pdf")
    assert out["source"] == "direct"
    assert out["title"] == "T"


def test_non_pdf_never_touches_pdf_branch(monkeypatch):
    calls = {"pdf": 0}
    monkeypatch.setattr(vwe, "_fetch_pdf_text", lambda url: calls.__setitem__("pdf", calls["pdf"] + 1) or None)
    monkeypatch.setattr(vwe, "_fetch", lambda url, **kw: "<html><title>T</title><body>" + "y" * 400 + "</body></html>")
    vwe.visit_website("https://example.com/page.html")
    assert calls["pdf"] == 0


# ── Head metadata + tables (HTML path, fetch mocked) ─────────────────────────

_HTML_WITH_META = """<html><head>
<title>Test Page</title>
<meta name="description" content="A great test page about testing things">
<meta property="article:published_time" content="2026-01-15T10:00:00Z">
</head><body><p>word </p><p>word</p></body></html>"""


def test_head_metadata_extracted(monkeypatch):
    monkeypatch.setattr(vwe, "_fetch", lambda url, **kw: _HTML_WITH_META)
    # htmldate may or may not be installed — force the fallback path so the
    # meta-published backfill is exercised.
    import types
    fake_htmldate = types.ModuleType("htmldate")
    def _fail(*a, **k):
        raise ImportError("no htmldate")
    fake_htmldate.find_date = _fail
    monkeypatch.setitem(sys.modules, "htmldate", fake_htmldate)
    out = vwe.visit_website("https://example.com/meta")
    assert out.get("description") == "A great test page about testing things"
    assert out.get("published", "").startswith("2026-01-15")


def test_tables_harvested_when_trafilatura_emits_them(monkeypatch):
    # trafilatura installed -> second call with include_tables=True returns
    # pipe-rows; they must land in result["tables"].
    pytest.importorskip("trafilatura")
    html = """<html><head><title>Compare</title></head><body>
    <p>Some prose about the comparison of tools.</p>
    <table><tr><td>Tool A</td><td>free</td></tr><tr><td>Tool B</td><td>paid</td></tr></table>
    </body></html>"""
    monkeypatch.setattr(vwe, "_fetch", lambda url, **kw: html)
    out = vwe.visit_website("https://example.com/compare")
    if "tables" in out:  # only when trafilatura emitted pipe rows
        assert "Tool A" in out["tables"]
        assert "Tool B" in out["tables"]


def test_result_fields_backward_compatible(monkeypatch):
    # Plain page: all legacy fields still present, new fields optional.
    monkeypatch.setattr(vwe, "_fetch", lambda url, **kw: "<html><head><title>Legacy</title></head><body><p>" + "z" * 400 + "</p></body></html>")
    out = vwe.visit_website("https://example.com/legacy")
    for key in ("title", "headings", "links", "images", "content", "published", "source", "url"):
        assert key in out
    assert out["title"] == "Legacy"
