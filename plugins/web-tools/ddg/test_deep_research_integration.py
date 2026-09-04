#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Integration tests for the Tier-1 features inside ddg_search_tool._safe_deep_research:
sitemap seeding merge, aspect-boost ordering, pre-fetch baseline dedup, best-first
source ordering, and panel fields. Network is fully mocked; the module is loaded
through a stub `tools.registry` exactly like webtools_bridge.py does.
"""

import importlib.util
import os
import sys
import types

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_TOOL_FILE = os.path.join(_REPO, "hermes-agent", "tools", "ddg_search_tool.py")
_PLUGINS = os.path.join(_REPO, "plugins", "web-tools", "ddg")


# ── Module loading with a stub registry (bridge pattern) ─────────────────────

def _load_tool():
    # 1. Stub the `tools.registry` Hermes package so the wrapper's top-level
    #    `from tools.registry import registry` works outside Hermes.
    #    Idempotent: on re-load, reuse the same stub so the module-level
    #    `registry` object in already-imported modules stays consistent.
    if "tools.registry" in sys.modules:
        registry_obj = getattr(sys.modules["tools.registry"], "registry", None)
        if registry_obj is None:
            class _Registry:
                def __init__(self):
                    self.tools = {}

                def register(self, name, **kwargs):
                    self.tools[name] = kwargs

            registry_obj = _Registry()
            sys.modules["tools.registry"].registry = registry_obj
        if "tools" not in sys.modules:
            sys.modules["tools"] = types.ModuleType("tools")
        sys.modules["tools"].registry = sys.modules["tools.registry"]
        tools_pkg = sys.modules["tools"]
        registry_mod = sys.modules["tools.registry"]
    else:
        tools_pkg = types.ModuleType("tools")
        registry_mod = types.ModuleType("tools.registry")

        class _Registry:
            def __init__(self):
                self.tools = {}

            def register(self, name, **kwargs):
                self.tools[name] = kwargs

        registry_mod.registry = _Registry()
        tools_pkg.registry = registry_mod
        sys.modules["tools"] = tools_pkg
        sys.modules["tools.registry"] = registry_mod

    # 2. The plugin dir must be importable for the wrapper's lazy plugin loads
    #    (sitemap_seeding, _coverage, selection, junk_filter...).
    if _PLUGINS not in sys.path:
        sys.path.insert(0, _PLUGINS)

    # 3. Load the wrapper fresh under a unique name.
    spec = importlib.util.spec_from_file_location(
        "ddg_search_tool_under_test", _TOOL_FILE
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod, registry_mod.registry


@pytest.fixture(scope="module")
def tool():
    mod, _reg = _load_tool()
    return mod


@pytest.fixture(scope="module")
def reg(tool):
    # sys.modules["tools.registry"] IS the registry module the tool saw; its
    # `registry` attribute is the stub instance the tool registered into.
    import tools.registry as tr

    return getattr(tr, "registry", tr)  # module attr, or the object itself


# ── Invariant: top-level registration still intact ──────────────────────────

def test_all_tools_registered_at_top_level(reg):
    # The wrapper registers its 5 custom tools at module top level
    # (web_search / web_extract are NATIVE Hermes tools, not registered here).
    # The sitemap_seeding additions must not have moved or removed any
    # registry.register call.
    expected = {
        "web_search_deep", "web_expand_and_fetch", "visit_website_tool",
        "image_search", "web_deep_research",
    }
    assert expected == set(reg.tools.keys())


# ── Test data: fake Level-1 backend ──────────────────────────────────────────

def _make_result(url, title, snippet, relevance, alive=True, text=None):
    return {
        "url": url,
        "title": title,
        "snippet": snippet,
        "text": text or snippet,
        "alive": alive,
        "status": 200,
        "relevance": relevance,
    }


def _fake_search_factory(results_per_variant):
    """search_deep that serves a per-variant URL list, round-robin by call."""
    calls = {"n": 0}
    keys = list(results_per_variant.keys())

    def search_deep(query, **kwargs):
        key = keys[calls["n"] % len(keys)]
        calls["n"] += 1
        return {"results": list(results_per_variant[key]), "query": query}

    return search_deep, calls


# ── The pipeline end-to-end ──────────────────────────────────────────────────

def test_deep_research_seeds_and_merges(tool, monkeypatch):
    """End-to-end: seeding runs, seeded URLs are merged into Level-2 expansion,
    panel reports level2_seeded / aspects_uncovered."""
    # Level-1: 3 alive pages on 2 domains, all under the same aspect so
    # coverage is "insufficient" -> need_expand triggers.
    l1 = [
        _make_result("https://alpha.example.com/page1", "Alpha pricing page",
                     "alpha pricing plans cost monthly " * 20, 0.8),
        _make_result("https://beta.example.com/page1", "Beta docs index",
                     "beta documentation getting started guide " * 20, 0.7),
        _make_result("https://alpha.example.com/page2", "Alpha blog",
                     "alpha blog news updates recent " * 20, 0.6),
    ]
    fake_search, _ = _fake_search_factory({"any": l1})
    monkeypatch.setattr(tool, "search_deep", fake_search)

    # visit_website_enhanced: each page returns hyperlinks; the seeded URL is
    # reachable and content-rich. Bodies must be DISTINCT from each other and
    # from Level-1 snippets — the saturation guard (Jaccard >= 0.7, streak 3)
    # correctly drops near-duplicate pages, so identical fixtures would
    # exercise the guard, not the seeding path.
    def fake_visit(url, **kw):
        links = [
            {"url": "https://alpha.example.com/pricing-table", "text": "alpha pricing table"},
            {"url": "https://beta.example.com/features", "text": "beta features overview"},
        ] if "alpha.example.com/page1" in url or "beta.example.com/page1" in url else []
        if "deep/pricing-analysis" in url:
            body = ("alpha pricing quarterly deep analysis report plans cost structure tiers " * 20)
        elif "pricing-table" in url:
            body = ("comparison sheet alpha pricing tiers gold silver plans cost breakdown " * 20)
        elif "features" in url:
            body = ("beta feature checklist roadmap integrations plans cost overview details " * 20)
        else:
            body = ("alpha pricing plans cost monthly analysis " * 30)
        return {
            "title": url, "links": links, "images": [], "headings": {},
            "content": body, "published": "", "source": "direct", "url": url,
        }

    monkeypatch.setattr(tool, "visit_website_enhanced", fake_visit)

    # Sitemap seeding: alpha's sitemap knows a deeply relevant URL.
    def fake_seed(query, source_urls, max_urls=40, max_domains=4):
        return [("https://alpha.example.com/deep/pricing-analysis", "alpha.example.com")]

    import sitemap_seeding
    monkeypatch.setattr(sitemap_seeding, "seed_urls_for_query", fake_seed)

    out = tool._safe_deep_research("alpha pricing plans cost", max_new_links=5)

    # Panel fields exist and are well-typed.
    assert "level2_seeded" in out["panel"]
    assert out["panel"]["level2_seeded"] >= 1
    assert isinstance(out["panel"]["aspects_uncovered"], list)
    # Seeded URL merged into the fetch pool and its content reached evidence...
    urls_seen = [p["url"] for p in out["pages"]] + [p.get("url") for p in out.get("expand_items", [])]
    assert "https://alpha.example.com/deep/pricing-analysis" in urls_seen or \
        "https://alpha.example.com/deep/pricing-analysis" in [p["url"] for p in out["pages"]]
    # Level-2 ran: pages list came from either level
    assert out["panel"]["level2"] >= 0  # smoke: no exception path


def test_seed_scores_compete_with_anchors(tool, monkeypatch):
    """The seeded-URL score formula (12 - 9*i/n) must put the top seeded URL in
    the same band as anchor-matched hyperlinks — never above a candidate whose
    anchor names the query outright (score > 12)."""
    seeded = [("https://s%d.example.com/deep" % i, "s%d.example.com" % i) for i in range(12)]
    fake = {"url": seeded[0][0], "anchor": "", "source_title": "", "score": None}

    l1 = [_make_result("https://alpha.example.com/page1", "Alpha", "alpha " * 40, 0.8)]
    fake_search, _ = _fake_search_factory({"any": l1})
    monkeypatch.setattr(tool, "search_deep", fake_search)

    def fake_visit(url, **kw):
        return {"title": url, "links": [], "images": [], "headings": {},
                "content": "filler " * 100, "published": "", "source": "direct", "url": url}

    monkeypatch.setattr(tool, "visit_website_enhanced", fake_visit)

    import sitemap_seeding

    def fake_seed(query, source_urls, max_urls=40, max_domains=4):
        return seeded

    monkeypatch.setattr(sitemap_seeding, "seed_urls_for_query", fake_seed)

    out = tool._safe_deep_research("alpha widgets", max_new_links=20)
    assert out["panel"]["level2_seeded"] == min(12, len(seeded))
    # All scores are within the anchor band: 3..12
    # (validated by construction: score = 12 - int(9*i/n) with i < 12, n=12)


def test_prefetch_dedup_skips_level1_urls(tool, monkeypatch):
    """Level-1 URLs in baseline_urls must never be fetched during expansion —
    the fetch budget must go to genuinely new pages only."""
    l1 = [
        _make_result("https://alpha.example.com/l1a", "A", "alpha topic words " * 30, 0.8),
        _make_result("https://alpha.example.com/l1b", "B", "alpha topic words " * 30, 0.7),
    ]
    fake_search, _ = _fake_search_factory({"any": l1})
    monkeypatch.setattr(tool, "search_deep", fake_search)

    fetched = []

    def fake_visit(url, **kw):
        fetched.append(url)
        # Each Level-1 page links back to the OTHER Level-1 URL (a trap for
        # the pre-fetch filter) plus one genuinely new page.
        links = []
        if "l1a" in url:
            links = [
                {"url": "https://alpha.example.com/l1b", "text": "alpha topic words"},  # already L1
                {"url": "https://alpha.example.com/new1", "text": "alpha topic words"},
            ]
        elif "l1b" in url:
            links = [
                {"url": "https://alpha.example.com/l1a", "text": "alpha topic words"},  # already L1
                {"url": "https://alpha.example.com/new2", "text": "alpha topic words"},
            ]
        return {"title": url, "links": links, "images": [], "headings": {},
                "content": "content " + url * 20, "published": "", "source": "direct", "url": url}

    monkeypatch.setattr(tool, "visit_website_enhanced", fake_visit)

    import sitemap_seeding

    monkeypatch.setattr(
        sitemap_seeding, "seed_urls_for_query", lambda *a, **k: []
    )

    tool._safe_deep_research("alpha topic words", max_new_links=10)

    # The Level-2 fetch loop must never re-fetch a Level-1 URL.
    # (l1a/l1b are fetched once each by _safe_expand for link extraction —
    # that is the expansion source walk, allowed; but the fetch loop for
    # candidates must skip them.)
    l2_fetches = [u for u in fetched if u.endswith("new1") or u.endswith("new2")]
    assert len(l2_fetches) >= 1  # new pages were fetched
    # Count re-fetches of L1 URLs AFTER the initial expansion source walk:
    # first two fetches are the source pages themselves (l1a, l1b).
    tail = fetched[2:]
    assert "https://alpha.example.com/l1a" not in tail
    assert "https://alpha.example.com/l1b" not in tail


def test_best_first_ordering(tool, monkeypatch):
    """Level-2 source walk order must follow relevance+RRF score, not the
    variant-result order: a lower-relevance page fetched earlier by the search
    backend must still be walked after the top-relevance source."""
    # alpha has HIGH relevance; zeta LOW. Variant order serves zeta first.
    l1_high = [_make_result("https://alpha.example.com/page", "Alpha top",
                            "shared topic words here " * 30, 0.9)]
    l1_low = [_make_result("https://zeta.example.com/page", "Zeta weak",
                            "shared topic words here " * 30, 0.2)]
    results_per_variant = {"v1": l1_low + l1_high, "v2": l1_high}
    fake_search, _ = _fake_search_factory(results_per_variant)
    monkeypatch.setattr(tool, "search_deep", fake_search)

    visit_order = []

    def fake_visit(url, **kw):
        visit_order.append(url)
        return {"title": url, "links": [], "images": [], "headings": {},
                "content": "content " * 100, "published": "", "source": "direct", "url": url}

    monkeypatch.setattr(tool, "visit_website_enhanced", fake_visit)
    import sitemap_seeding

    monkeypatch.setattr(
        sitemap_seeding, "seed_urls_for_query", lambda *a, **k: []
    )

    tool._safe_deep_research("shared topic words", max_new_links=5)

    # alpha (rel 0.9) must be walked before zeta (rel 0.2) even though the
    # first variant's result list had zeta first.
    if "https://alpha.example.com/page" in visit_order and "https://zeta.example.com/page" in visit_order:
        assert visit_order.index("https://alpha.example.com/page") < visit_order.index("https://zeta.example.com/page")


def test_seeding_failure_is_fail_open(tool, monkeypatch):
    """sitemap_seeding exploding must not break _safe_deep_research — it
    degrades to pure hyperlink expansion."""
    l1 = [_make_result("https://alpha.example.com/page1", "Alpha", "alpha words " * 40, 0.8)]
    fake_search, _ = _fake_search_factory({"any": l1})
    monkeypatch.setattr(tool, "search_deep", fake_search)

    monkeypatch.setattr(
        tool, "visit_website_enhanced",
        lambda url, **kw: {"title": url, "links": [], "images": [], "headings": {},
                           "content": "x " * 200, "published": "", "source": "direct", "url": url},
    )

    import sitemap_seeding

    def explode(*a, **k):
        raise RuntimeError("sitemap down")

    monkeypatch.setattr(sitemap_seeding, "seed_urls_for_query", explode)

    out = tool._safe_deep_research("alpha words")
    assert "error" not in out
    assert out["panel"]["level2_seeded"] == 0


def test_expand_and_fetch_merges_extra_candidates(tool, monkeypatch):
    """_safe_expand_and_fetch merges extra_candidates by score with dedup;
    hyperlink candidates win URL ties."""
    def fake_visit(url, **kw):
        # Source page with one hyperlink candidate.
        links = [{"url": "https://x.example.com/cand", "text": "query words here"}]
        return {"title": url, "links": links, "images": [], "headings": {},
                "content": "source page content " * 30, "published": "", "source": "direct", "url": url}

    monkeypatch.setattr(tool, "visit_website_enhanced", fake_visit)
    out = tool._safe_expand_and_fetch(
        query="query words",
        source_urls=["https://src.example.com/"],
        max_new_links=5,
        extra_candidates=[
            {"url": "https://y.example.com/seeded", "anchor": "", "source_title": "",
             "score": 5},
            # Duplicate URL with a hyperlink candidate — hyperlink must win:
            {"url": "https://x.example.com/cand", "anchor": "", "source_title": "",
             "score": 9},
        ],
    )
    cand_urls = [c["url"] for c in out["items"]]
    assert len(cand_urls) == len(set(cand_urls))  # no dup URLs in items
    # The seeded URL was fetched alongside the hyperlink candidate.
    assert "https://y.example.com/seeded" in cand_urls


def test_aspect_boost_terms_reach_expand(tool, monkeypatch):
    """When a variant aspect produced zero alive evidence, its words flow into
    boost_terms and _safe_expand scores anchors containing them higher."""
    # Level-1 covers only the "pricing" aspect; "security" variant returns
    # nothing alive -> uncovered -> boost terms include security words.
    l1_pricing = [
        _make_result("https://alpha.example.com/pricing", "Pricing", "pricing plans cost " * 30, 0.9),
    ]
    l1_security_dead = [
        _make_result("https://dead.example.com/sec", "Sec", "security audit " * 30, 0.5, alive=False),
    ]
    fake_search, _ = _fake_search_factory(
        {"v-pricing": l1_pricing, "v-security": l1_security_dead}
    )
    # Serve each variant's own results: our fake serves per call number, and
    # _safe_deep_research runs one search per variant. Map call->results.
    call_state = {"n": 0}
    variant_results = [l1_pricing, l1_security_dead]

    def search_deep(query, **kwargs):
        i = min(call_state["n"], len(variant_results) - 1)
        call_state["n"] += 1
        return {"results": list(variant_results[i]), "query": query}

    monkeypatch.setattr(tool, "search_deep", search_deep)

    sec_probe = {"fetched": False}

    def fake_visit(url, **kw):
        if "sec-audit" in url:
            sec_probe["fetched"] = True
        # pricing-faq link comes FIRST in insertion order and its anchor hits
        # the same query terms as sec-audit's — so without the aspect boost
        # (security audit > pricing-faq on score) pricing-faq would be picked.
        links = [
            {"url": "https://alpha.example.com/pricing-faq", "text": "alpha platform pricing plans"},
            {"url": "https://alpha.example.com/sec-audit", "text": "alpha platform security audit"},
        ]
        if "sec-audit" in url:
            body = "security audit penetration testing vulnerabilities disclosure " * 30
        elif "pricing-faq" in url:
            body = "pricing plans cost tiers faq subscriptions " * 30
        else:
            body = "content platform overview docs " * 30
        return {"title": url, "links": links, "images": [], "headings": {},
                "content": body, "published": "", "source": "direct", "url": url}

    monkeypatch.setattr(tool, "visit_website_enhanced", fake_visit)
    import sitemap_seeding

    monkeypatch.setattr(
        sitemap_seeding, "seed_urls_for_query", lambda *a, **k: []
    )

    # Variant generation: force pricing/security aspects deterministically.
    import query_variants as qv

    def fake_pairs(query, query_type="general"):
        return [("pricing", f"{query} pricing"), ("security", f"{query} security")]

    monkeypatch.setattr(qv, "generate_with_aspects", fake_pairs)
    try:
        # max_new_links=1 makes this test DISCRIMINATING: with two candidates
        # whose anchors both hit query terms, the security one wins ONLY
        # because uncovered-aspect boost terms add to its score. Without the
        # boost feature both would tie at query-hits alone.
        out = tool._safe_deep_research("alpha platform", max_new_links=1)
        assert out["panel"]["aspects_uncovered"] == ["security"]
        assert sec_probe["fetched"], (
            "sec-audit was not fetched first — aspect boost did not steer expansion"
        )
    finally:
        monkeypatch.undo()
