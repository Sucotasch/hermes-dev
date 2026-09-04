#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for standalone no-LLM mode: user-set query_type, LLM probe,
graceful synthesis skip. The full pipeline is NOT run here — LLM/network
seams are mocked; pipeline behavior is covered by the comparison runs.
"""

import os
import sys
import types

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, os.path.join(_REPO, "standalone"))
sys.path.insert(0, os.path.join(_REPO, "plugins", "web-tools", "ddg"))


import orchestrator as orch
import llm_client


# ── _llm_probe ──────────────────────────────────────────────────────────────

def test_llm_probe_dead_server(monkeypatch):
    # Connection refused on localhost — instant False, never raises.
    assert orch._llm_probe("http://127.0.0.1:1") is False


def test_llm_probe_hanging_server(monkeypatch):
    # A hanging server must be bounded by the probe's OWN short timeout —
    # not the 120s chat timeout. Real urlopen raises on socket timeout; the
    # fake asserts the bound that was actually requested (1s, not 120s).
    import urllib.request as _ur

    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["timeout"] = timeout
        # What a real urlopen does when the server never replies:
        raise OSError("timed out")

    monkeypatch.setattr(_ur, "urlopen", fake_urlopen)
    ok = orch._llm_probe("http://localhost:8888", timeout=1)
    assert ok is False
    # The bound actually passed down is the probe's 1s, never 120s.
    assert captured.get("timeout") == 1


def test_llm_probe_live_server(monkeypatch):
    import urllib.request as _ur

    class _OK:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=-1):
            return b'{"choices": [{"message": {"content": "pong"}}]}'

    monkeypatch.setattr(_ur, "urlopen", lambda req, timeout=None: _OK())
    assert orch._llm_probe("http://localhost:8888") is True


# ── Step-1 classification branch (unit level, no pipeline) ────────────────────

def test_valid_query_types_constant():
    assert set(orch._VALID_QUERY_TYPES) == {
        "person", "visual", "technical", "news", "historical", "comparison",
        "fact", "art", "education", "science", "video", "general",
    }
    # Must match llm_client's valid list (single source of truth check).
    assert set(orch._VALID_QUERY_TYPES) == set(
        ["person", "visual", "technical", "news", "historical", "comparison",
         "fact", "art", "education", "science", "video", "general"]
    )


def test_run_deep_research_accepts_query_type_kwarg():
    # Signature check without executing the pipeline: inspect only.
    import inspect
    sig = inspect.signature(orch.run_deep_research)
    assert "query_type" in sig.parameters
    assert sig.parameters["query_type"].default is None


def test_classify_fallback_is_general(monkeypatch):
    # The pre-existing fail-open behavior this feature builds on: dead
    # LLM -> classify returns "general" (never raises).
    monkeypatch.setattr(
        llm_client, "chat_completion", lambda *a, **k: None
    )
    assert llm_client.classify_query_type("anything") == "general"


def test_build_report_synthesis_note():
    # _build_report must render the no-LLM synthesis note verbatim (it is
    # passed as the synthesis string by Step 10).
    report = orch._build_report(
        "test query", "general",
        [{"url": "https://x.example", "title": "T", "relevance": 0.5, "content": "c" * 300}],
        [],
        "_LLM synthesis skipped — no LLM server reachable at http://x._\n",
        {"total": 10.0},
    )
    assert "no LLM server reachable" in report
    assert "test query" in report


# ── End-to-end: full run_deep_research with dead LLM + mocked network ────────

def test_full_run_no_llm_user_qtype(monkeypatch):
    """run_deep_research with a user-set query_type and NO LLM server must:
    skip classification, probe the LLM once (fast fail), run the whole
    pipeline, and return a report with an honest no-synthesis note.
    Network (search + validation + deep read) is mocked at the ddg_search
    and vwe seams."""
    import ddg_search
    import visit_website_enhanced as vwe

    # Dead LLM: probe must fail fast (nothing listens on this port).
    dead_server = "http://127.0.0.1:59999"

    # ── Mock search: one variant-shaped result set, small but alive ──
    # NOTE: domains must survive the backend blocklist (is_blocked_domain
    # blocks reserved TLDs like example.com — by design); .dev passes.
    def fake_web_search(q, count=100, region="wt-wt", safe="auto"):
        return {"results": [
            {"url": "https://alpha-source.dev/alpha-deep-dive",
             "title": "Alpha deep dive on the topic",
             "snippet": "alpha topic facts details overview " * 5},
            {"url": "https://beta-source.dev/beta-guide",
             "title": "Beta guide",
             "snippet": "beta topic guide tutorial intro " * 5},
        ]}

    monkeypatch.setattr(ddg_search, "web_search", fake_web_search)

    # ── Mock URL validation: both alive with real-ish bodies ──
    def fake_check_url(url, timeout=5):
        body = ("<html><body><p>" + ("alpha topic facts details numbers "
                + "alpha beta comparison analysis ") * 40 + "</p></body></html>")
        return {"alive": True, "status": 200, "body": body, "text_length": 6000}

    monkeypatch.setattr(ddg_search, "_check_url_live", fake_check_url)

    # ── Mock deep read: keep it small, return content + no images ──
    def fake_visit(url, **kw):
        return {"title": f"Page {url}", "text": "alpha topic facts " * 200,
                "links": [], "images": [], "headings": {}}

    monkeypatch.setattr(vwe, "visit_website", fake_visit)

    logs = []
    result = orch.run_deep_research(
        "alpha topic deep dive",
        server_url=dead_server,
        max_validate=10,
        verbose=False,
        log=logs.append,
        top_n=5, images_count=0, llm_sources=5, max_variants=2,
        query_type="general",
    )

    stats = result["stats"]
    # User-set type honored, no LLM available, pipeline did not crash.
    assert stats["query_type"] == "general"
    assert stats["llm"] is False
    # Evidence reached the report.
    assert stats["evidence_pages"] >= 1
    # Honest note, not a fake "_No synthesis available_".
    assert "no LLM server reachable" in result["report"]
    # Log shows the skip decision.
    assert any("user-set" in line for line in logs)
    assert any("Synthesis skipped" in line for line in logs)
    # No LLM call was made for classify (fast path) — probe only.
    assert any("query_type: general (user-set)" in line for line in logs)
