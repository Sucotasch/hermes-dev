"""Unit tests for the Hermes wrapper `hermes-agent/tools/ddg_search_tool.py`.

The wrapper is the SOURCE synced by restore.ps1 into the real Hermes project,
where `tools.registry` exists. Here we stub `tools.registry` in memory so the
module can be imported and exercised without the Hermes app.

No network: `search_deep` and `visit_website_enhanced` are faked per test, and
`_is_coverage_sufficient` is stubbed so Level-2 expansion never fires unless a
test explicitly wants it.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parents[1]
PLUGINS_DIR = REPO_ROOT / "plugins" / "web-tools" / "ddg"

# ── stub tools.registry (lives in the real Hermes project, not here) ──
_tools_pkg = types.ModuleType("tools")
_reg_mod = types.ModuleType("tools.registry")


class _Registry:
    def register(self, *args, **kwargs):
        return None


_reg_mod.registry = _Registry()
_tools_pkg.registry = _reg_mod
sys.modules.setdefault("tools", _tools_pkg)
sys.modules.setdefault("tools.registry", _reg_mod)

for _p in (str(REPO_ROOT / "hermes-agent"), str(TOOLS_DIR), str(PLUGINS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_spec = importlib.util.spec_from_file_location(
    "ddg_search_tool", str(TOOLS_DIR / "ddg_search_tool.py"))
ddg = importlib.util.module_from_spec(_spec)
sys.modules["ddg_search_tool"] = ddg
_spec.loader.exec_module(ddg)

assert ddg.search_deep is not None, "backend must load from plugins/web-tools/ddg"


# ── helpers ──────────────────────────────────────────────────────────────

def _fake_result(i, relevance, text_len=2000, alive=True, domain=None):
    """One fake backend result: unique filler tokens per page so Jaccard
    dedup does not collapse them, `relevance` as given by the backend."""
    # registrable-domain unique: base_domain() would collapse all site{i}.example.com
    # into example.com and the per-domain quota would kick in — keep domains distinct
    d = domain or f"site{i}.com"
    filler = " ".join(f"tok{i}{j}" for j in range(40))
    text = (f"Qwen3.6-27B llama.cpp best settings for 16GB VRAM context 64k. {filler} "
            * (text_len // 60))[:text_len]
    return {
        "url": f"https://{d}/page{i}",
        "title": f"Qwen 27B page {i}",
        "snippet": "Qwen3.6-27B llama.cpp settings " * 4,
        "text": text,
        "alive": alive,
        "status": 200,
        "relevance": relevance,
    }


def _make_fake_search(pages):
    def fake_search_deep(q, validate=True, classify=False, max_validate=200,
                         query_variants=None, compose=False, query_type=None):
        return {"results": [dict(p) for p in pages]}
    return fake_search_deep


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    # Level-2 expansion off by default; backend fetcher never touched.
    monkeypatch.setattr(ddg, "_is_coverage_sufficient", lambda *a, **k: True)

    def safe_visit(url, **kw):
        return {"url": url, "title": "", "text": "", "links": []}

    monkeypatch.setattr(ddg, "visit_website_enhanced", safe_visit)
    yield


# ── quality filter: relevance must flow from backend ─────────────────────

def test_relevance_flows_from_backend_and_filter_applies(monkeypatch):
    pages = [_fake_result(i, 0.9) for i in range(15)] + [_fake_result(i, 0.05) for i in range(15, 20)]
    monkeypatch.setattr(ddg, "search_deep", _make_fake_search(pages))
    out = ddg._safe_deep_research(
        "Qwen3.6-27B llama.cpp best settings 16Gb VRAM", query_type="technical")
    assert len(out["pages"]) == 15            # low-relevance dropped
    assert all(p["relevance"] >= 0.15 for p in out["pages"])


def test_page_dicts_carry_relevance_key(monkeypatch):
    pages = [_fake_result(i, 0.9, text_len=800) for i in range(15)]
    monkeypatch.setattr(ddg, "search_deep", _make_fake_search(pages))
    out = ddg._safe_deep_research(
        "Qwen3.6-27B llama.cpp best settings 16Gb VRAM", query_type="technical")
    assert len(out["pages"]) == 15
    for p in out["pages"]:
        assert "relevance" in p
        assert p["relevance"] == pytest.approx(0.9)


def test_all_irrelevant_dropped(monkeypatch):
    pages = [_fake_result(i, 0.0, text_len=2000) for i in range(10)]
    monkeypatch.setattr(ddg, "search_deep", _make_fake_search(pages))
    out = ddg._safe_deep_research("Qwen3.6-27B llama.cpp", query_type="technical")
    assert len(out["pages"]) == 0


# ── visual topics get softer gates (mirrors standalone 0.05 / 50 chars) ──

def test_visual_threshold_is_softer(monkeypatch):
    pages = [_fake_result(i, 0.1, text_len=200) for i in range(15)]
    monkeypatch.setattr(ddg, "search_deep", _make_fake_search(pages))

    out_v = ddg._safe_deep_research("Sara St James gallery", query_type="visual")
    assert len(out_v["pages"]) == 15          # 0.1 >= 0.05 and 200 >= 50

    out_t = ddg._safe_deep_research("Sara St James gallery", query_type="technical")
    assert len(out_t["pages"]) == 0           # 0.1 < 0.15


# ── Level-2 expand items are scored with content_relevance_score ─────────

def test_expand_items_get_relevance_scored(monkeypatch):
    monkeypatch.setattr(ddg, "_is_coverage_sufficient", lambda *a, **k: False)  # force expand

    def visit(url, **kw):
        if "candidate" in url:
            text = ("Qwen3.6-27B llama.cpp best settings for 16GB VRAM context 64k "
                    "quantization GGUF benchmark throughput tokens/sec ") * 30
            return {"url": url, "title": "Expand page", "text": text, "links": []}
        return {
            "url": url,
            "title": "Source",
            "text": "Qwen3.6-27B llama.cpp settings " * 20,
            "links": [
                {"url": f"https://candidate{i}.example.com/{i}",
                 "text": "Qwen3.6-27B llama.cpp settings 16GB VRAM"}
                for i in range(3)
            ],
        }

    monkeypatch.setattr(ddg, "visit_website_enhanced", visit)

    pages = [_fake_result(i, 0.9, text_len=800) for i in range(5)]  # alive=5 < 15 → expand
    monkeypatch.setattr(ddg, "search_deep", _make_fake_search(pages))

    out = ddg._safe_deep_research(
        "Qwen3.6-27B llama.cpp best settings 16Gb VRAM", query_type="technical")
    assert len(out["pages"]) > 5              # level-1 + expand items survived
    assert any(p["url"].startswith("https://candidate") for p in out["pages"])
    assert any(p["relevance"] >= 0.15 for p in out["pages"] if p["url"].startswith("https://candidate"))


# ── budget caps pages and measures compacted (LLM-facing) size ───────────

def test_budget_caps_pages_and_compacted_chars(monkeypatch):
    pages = [_fake_result(i, 0.9, text_len=5000) for i in range(25)]
    monkeypatch.setattr(ddg, "search_deep", _make_fake_search(pages))
    out = ddg._safe_deep_research(
        "Qwen3.6-27B llama.cpp best settings 16Gb VRAM", query_type="technical")
    assert len(out["pages"]) <= ddg.MAX_EVIDENCE_PAGES
    total = sum(len(p["summary"]) for p in out["pages"])
    assert total <= ddg.MAX_EVIDENCE_CHARS
    assert total > 24000                      # budget actually filled
