"""Tests: bridge SSRF guard (agent-supplied URL safety) + Jina concurrency cap.

The guard must (a) block private/internal/metadata targets BEFORE any
network attempt, (b) fail OPEN on any internal error (unresolvable hosts,
garbage input) so the only network path can never break, (c) yield to the
HERMES_SSRF_GUARD=0 kill switch for deliberate local testing. The Jina cap
must never let more than 2 concurrent requests through (anonymous rate
limit). Run from plugins/web-tools/ddg/::

    python -m pytest test_ssrf_guard.py -q
"""

import shutil
import sys
import threading
import time
import types
import uuid
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
REPO_ROOT = HERE.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import webtools_bridge as bridge  # noqa: E402
import visit_website_enhanced as vwe  # noqa: E402


@pytest.fixture()
def state_dir():
    d = HERE / ".wall_test_state" / uuid.uuid4().hex
    d.mkdir(parents=True, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def wall(state_dir, monkeypatch):
    import wall_memory as wm
    wm._reset_for_tests(str(state_dir / "walls.json"))
    yield wm
    wm._reset_for_tests()


# ── _ssrf_gate: blocked targets ─────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "http://192.168.1.1/x",                      # private
    "http://10.0.0.5/",                         # private
    "http://172.16.20.1/",                      # private
    "http://127.0.0.1:8080/",                   # loopback
    "http://169.254.169.254/latest/meta-data/", # cloud metadata
    "http://100.64.3.4/",                       # CGNAT (not is_private!)
    "http://[::1]/",                            # IPv6 loopback
    "http://[fe80::1]/",                        # IPv6 link-local
    "http://[fc00::1]/",                        # IPv6 ULA
    "file:///etc/passwd",                       # non-http scheme
])
def test_blocked_targets(url):
    ok, reason = bridge._ssrf_gate(url)
    assert not ok, url
    assert reason


# ── _ssrf_gate: allowed / fail-open ─────────────────────────────────────────

def test_public_literal_ip_allowed():
    assert bridge._ssrf_gate("http://93.184.216.34/")[0]


def test_public_hostname_allowed():
    ok, reason = bridge._ssrf_gate("https://example.com/")
    assert ok and reason is None


def test_unresolvable_host_fails_open():
    # DNS failure is the fetcher's business, not the guard's.
    url = "http://definitely-not-a-real-host-9x7q.invalid/"
    assert bridge._ssrf_gate(url)[0]


def test_kill_switch_allows_private(monkeypatch):
    monkeypatch.setenv("HERMES_SSRF_GUARD", "0")
    assert bridge._ssrf_gate("http://192.168.0.1/")[0]
    assert bridge._ssrf_gate("http://127.0.0.1:9/")[0]


def test_garbage_fails_open():
    assert bridge._ssrf_gate(None)[0]
    assert bridge._ssrf_gate("")[0]
    assert bridge._ssrf_gate("not a url")[0]


# ── bridge integration: gate fires before any fetch ─────────────────────────

class _FakeMod:
    def __init__(self, source="ok", text="sample " * 200):
        self.source = source
        self.text = text
        self.calls = []

    def visit_website_enhanced(self, url, max_chars=8000, cookies=None):
        self.calls.append(url)
        return {"url": url, "title": "t", "text": self.text,
                "source": self.source}


def _args(url):
    return types.SimpleNamespace(url=url, max_chars=8000, no_cache=True,
                                 no_wayback=True, cookies="")


def test_read_blocks_private_before_network(wall):
    mod = _FakeMod()
    res = bridge.cmd_read(mod, _args("http://192.168.0.1/x"))
    assert mod.calls == []                      # zero fetch attempts
    assert res["error"] == "guard.ssrf"
    assert res["reason"]
    assert "HERMES_SSRF_GUARD=0" in res["next_action"]


def test_read_kill_switch_reaches_fetcher(wall, monkeypatch):
    monkeypatch.setenv("HERMES_SSRF_GUARD", "0")
    mod = _FakeMod()
    res = bridge.cmd_read(mod, _args("http://127.0.0.1:9/x"))
    assert mod.calls == ["http://127.0.0.1:9/x"]   # fetch attempted
    assert "error" not in res                      # fake fetcher succeeded


def test_mfetch_blocks_private_before_moli(wall):
    args = types.SimpleNamespace(url="http://169.254.169.254/meta",
                                 max_chars=8000, no_cache=True,
                                 moli_timeout=5.0, proxy=False,
                                 proxy_url="http://127.0.0.1:2080",
                                 cookies="")
    res = bridge.cmd_mfetch(None, args)          # mod unused: guard fires first
    assert res["error"] == "guard.ssrf"


def test_render_blocks_private_before_deno(wall):
    args = types.SimpleNamespace(url="http://10.1.2.3/x", max_chars=8000,
                                  no_cache=True, wait_ms=100,
                                  render_timeout=2.0, cookies="")
    res = bridge.cmd_render(None, args)
    assert res["error"] == "guard.ssrf"


# ── Jina anonymous concurrency cap ──────────────────────────────────────────

def test_jina_concurrency_capped_at_two(monkeypatch):
    active, peak = [], [0]
    lock = threading.Lock()

    def fake_fetch(url, referrer=None, cookies=None):
        with lock:
            active.append(1)
            peak[0] = max(peak[0], len(active))
        time.sleep(0.15)                     # let the other threads pile up
        with lock:
            active.pop()
        return "<html><body>" + "x" * 200 + "</body></html>"

    monkeypatch.setattr(vwe, "_fetch", fake_fetch)
    threads = [threading.Thread(target=vwe._fetch_jina,
                                args=(f"http://cap{i}.example/",))
               for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] <= 2                       # safety property, not a race
