#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for standalone no-LLM mode (explicit opt-in) and Level-2
sitemap seeding. The full pipeline is mocked at the network/LLM seams.

Semantics under test (after the 2026-09-04 redesign):
- no_llm=True  → zero LLM requests anywhere (no probe, no classify,
  no enrich, no synthesis), honest note in the report.
- query_type set (no no_llm) → classification skipped, but enrich and
  synthesis are STILL attempted with the patient 120s timeouts — a
  user-set type is not a no-LLM request.
- Auto (no query_type, no no_llm) → LLM classifies; failure fails open
  to "general" with the full patience (local servers need 30-90s to
  load the model on the first request — no quick early probe may veto
  synthesis).
"""

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, os.path.join(_REPO, "standalone"))
sys.path.insert(0, os.path.join(_REPO, "plugins", "web-tools", "ddg"))


import orchestrator as orch
import llm_client


# ── Signature / constants ────────────────────────────────────────────────────

def test_valid_query_types_constant():
    assert set(orch._VALID_QUERY_TYPES) == {
        "person", "visual", "technical", "news", "historical", "comparison",
        "fact", "art", "education", "science", "video", "general",
    }


def test_run_deep_research_signature():
    import inspect
    sig = inspect.signature(orch.run_deep_research)
    # query_type default None (auto), no_llm default False (opt-in only).
    assert sig.parameters["query_type"].default is None
    assert sig.parameters["no_llm"].default is False


def test_llm_probe_is_gone():
    # The early t=0 probe was REMOVED: it vetoed synthesis for local
    # servers that were still loading the model (30-90s startup).
    assert not hasattr(orch, "_llm_probe")


def test_classify_fallback_is_general(monkeypatch):
    # Pre-existing fail-open: dead LLM → classify returns "general".
    monkeypatch.setattr(
        llm_client, "chat_completion", lambda *a, **k: None
    )
    assert llm_client.classify_query_type("anything") == "general"


# ── Visual budget override (Step 1 quirk fix) ─────────────────────────────────

def test_visual_budget_override_quirk_fixed():
    """The old code raised max_imgs_per_page ONLY from the untouched default
    (==5), so preset values fought the visual intent: Balanced (5→30) but
    Visual preset (10 → stayed 10!), Minimal (3 → stayed 3). Now ANY cap
    in (0, 30) is raised to 30; 0 (=user explicitly wants all... wait, 0
    here means 'no per-page cap' only for images_count; for per-page the
    0 means default from old GUI) — kept as explicit user choice."""
    # We test through the log line the pipeline emits: run Step-1 logic
    # via a minimal invocation is heavy; instead assert the code path by
    # extracting the constants involved. The behavioral check lives in
    # the e2e test (visual + preset caps → log line about the override).
    import inspect
    src = inspect.getsource(orch.run_deep_research)
    assert "0 < max_imgs_per_page < 30" in src
    assert "images_count != 0" in src
    # The old quirk (only the untouched default was raised) must be gone.
    assert "max_imgs_per_page == 5" not in src


# ── End-to-end (mocked network): no_llm mode ─────────────────────────────────

def _mock_network(monkeypatch, seed_urls=None):
    """Common network mocks: two alive sources, no images, tiny bodies.
    Returns the log collector list. seed_urls: if given, sitemap_seeding
    is monkeypatched to return [(url, domain), ...] pairs.
    vwe._fetch/_fetch_wayback are mocked too — deep-read uses _fetch
    directly (not visit_website), and real .dev lookups cost ~30s each."""
    import ddg_search
    import visit_website_enhanced as vwe

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

    def fake_check_url(url, timeout=5):
        body = ("<html><body><p>" + ("alpha topic facts details numbers "
                + "alpha beta comparison analysis ") * 40 + "</p></body></html>")
        return {"alive": True, "status": 200, "body": body, "text_length": 6000}

    monkeypatch.setattr(ddg_search, "_check_url_live", fake_check_url)

    _body = ("<html><body><p>" + "alpha topic facts details " * 60
             + "</p></body></html>")

    def fake_visit(url, **kw):
        return {"title": f"Page {url}", "text": "alpha topic facts " * 200,
                "links": [], "images": [], "headings": {}}

    monkeypatch.setattr(vwe, "visit_website", fake_visit)
    # Deep-read fetch ladder (direct -> Jina -> Wayback): hermetic mocks.
    monkeypatch.setattr(vwe, "_fetch", lambda url, **kw: _body)
    monkeypatch.setattr(vwe, "_fetch_wayback", lambda url, **kw: None)

    if seed_urls is not None:
        import sitemap_seeding
        monkeypatch.setattr(
            sitemap_seeding, "seed_urls_for_query",
            lambda query, source_urls, max_urls=20: seed_urls,
        )

    logs = []
    return logs


def test_e2e_no_llm_mode_skips_all_llm(monkeypatch):
    """no_llm=True: zero LLM requests; report has the honest note;
    stats['llm'] reflects 'synthesis actually happened' = False."""
    logs = _mock_network(monkeypatch)

    # Orchestrator holds a BOUND name (from-import) — mock on orch.
    llm_calls = []

    def counting_chat(*a, **k):
        llm_calls.append(True)
        return None

    monkeypatch.setattr(orch, "chat_completion", counting_chat)

    result = orch.run_deep_research(
        "alpha topic deep dive",
        server_url="http://127.0.0.1:59999",   # nothing listens there
        max_validate=10,
        verbose=False,
        log=logs.append,
        top_n=5, images_count=0, llm_sources=5, max_variants=2,
        query_type="general",
        no_llm=True,
    )

    assert llm_calls == []                      # zero LLM requests
    assert result["stats"]["llm"] is False      # no synthesis happened
    assert result["stats"]["query_type"] == "general"
    assert result["stats"]["evidence_pages"] >= 1
    assert "no-LLM mode" in result["report"]
    assert any("no-LLM mode" in line for line in logs)


def test_e2e_no_llm_defaults_to_general_without_qtype(monkeypatch):
    """No-LLM + 'Auto' (no type set) → type defaults to 'general' with an
    explicit warning line — never a silent visual→general degradation."""
    logs = _mock_network(monkeypatch)

    result = orch.run_deep_research(
        "some visual-ish query about photos",
        server_url="http://127.0.0.1:59999",
        max_validate=10,
        verbose=False,
        log=logs.append,
        top_n=5, images_count=0, llm_sources=5, max_variants=2,
        no_llm=True,
    )

    assert result["stats"]["query_type"] == "general"
    assert any("defaulting to 'general'" in line for line in logs)


def test_e2e_user_qtype_with_dead_llm_still_tries_synthesis(monkeypatch):
    """User-set type + NO no_llm flag: synthesis is attempted patiently —
    the pipeline must NOT decide at t=0 that the LLM is dead. The mock
    stands in for a dead server (chat_completion → None after the full
    patient timeout path): the attempt was made, the report notes the
    failure honestly, stats['llm'] is False."""
    logs = _mock_network(monkeypatch)

    attempts = []

    def patient_chat(messages, server_url=None, **k):
        attempts.append(server_url)
        return None   # server dead → chat_completion returns None

    # Mock the bound name in orchestrator (from-import semantics).
    monkeypatch.setattr(orch, "chat_completion", patient_chat)

    result = orch.run_deep_research(
        "alpha topic deep dive",
        server_url="http://127.0.0.1:59999",
        max_validate=10,
        verbose=False,
        log=logs.append,
        top_n=5, images_count=0, llm_sources=5, max_variants=2,
        query_type="technical",           # set, but no no_llm flag
    )

    # Classification was skipped (user-set), synthesis was ATTEMPTED.
    assert attempts, "synthesis must still be attempted with user-set type"
    assert result["stats"]["llm"] is False
    assert "no-LLM mode" not in result["report"]
    assert "LLM synthesis failed" in result["report"]
    assert any("user-set" in line for line in logs)


def test_e2e_level2_sitemap_seeding(monkeypatch):
    """Level-2 sitemap seeding: seeds are added to the candidate pool,
    logged, and validated (dedup-capped at 2 per registrable domain).
    alpha-source.dev already has 1 Level-1 URL (key_counts=1), so from
    its 3 seeds only 1 passes the cap (1+1=2); gamma passes fresh.
    Expected survivors: 1 alpha + 1 gamma = 2."""
    logs = _mock_network(
        monkeypatch,
        seed_urls=[
            ("https://alpha-source.dev/sitemap-page-1", "alpha-source.dev"),
            ("https://alpha-source.dev/sitemap-page-2", "alpha-source.dev"),
            ("https://alpha-source.dev/sitemap-page-3", "alpha-source.dev"),  # over cap → dropped
            ("https://gamma-source.dev/from-sitemap", "gamma-source.dev"),
        ],
    )

    result = orch.run_deep_research(
        "alpha topic deep dive",
        server_url="http://127.0.0.1:59999",
        max_validate=10,
        verbose=False,
        log=logs.append,
        top_n=5, images_count=0, llm_sources=5, max_variants=2,
        query_type="general",
        no_llm=True,
    )

    # Seeding happened, was logged, and the per-domain cap held (2 survivors:
    # alpha had 1 Level-1 URL already → 1 seed more allowed; gamma fresh → 1).
    assert any("sitemap seeding: 2 candidate URLs" in line for line in logs), (
        "expected exactly 2 seeds after the 2-per-domain cap "
        "(alpha-source.dev: 1 L1 + 1 seed; gamma-source.dev: 1 seed)"
    )
    # Pipeline survived and produced evidence.
    assert result["stats"]["evidence_pages"] >= 1


def test_e2e_level2_seeding_failopen(monkeypatch):
    """Seeding that raises must not break the pipeline (fail-open)."""
    import sitemap_seeding

    def exploding_seeds(query, source_urls, max_urls=20):
        raise RuntimeError("sitemap fetch exploded")

    monkeypatch.setattr(
        sitemap_seeding, "seed_urls_for_query", exploding_seeds
    )
    logs = _mock_network(monkeypatch)

    result = orch.run_deep_research(
        "alpha topic deep dive",
        server_url="http://127.0.0.1:59999",
        max_validate=10,
        verbose=False,
        log=logs.append,
        top_n=5, images_count=0, llm_sources=5, max_variants=2,
        query_type="general",
        no_llm=True,
    )

    assert result["stats"]["evidence_pages"] >= 1   # pipeline survived
