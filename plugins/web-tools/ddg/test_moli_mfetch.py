"""Tests for the Moli settle delay and the thin-shell gate in mfetch.

Evidence-driven (see the 2026-11 challenge experiment): without `--delay-ms`
Moli dumps challenged pages empty, and a CF 429 dumped to markdown measured
~496 chars with no marker from our list — so the marker check alone is not
enough. These tests pin both behaviours without touching the network.

Run from plugins/web-tools/ddg/::

    python -m pytest test_moli_mfetch.py -q
"""

import shutil
import sys
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


@pytest.fixture()
def state_dir():
    d = HERE / ".wall_test_state" / uuid.uuid4().hex
    d.mkdir(parents=True, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def wall(state_dir):
    import wall_memory as wm
    wm._reset_for_tests(str(state_dir / "walls.json"))
    yield wm
    wm._reset_for_tests()


class _FakeProc:
    def __init__(self, out="", rc=0):
        self._out = out
        self.returncode = rc

    def communicate(self, timeout=None):
        return self._out, None


def _capture_cmd(monkeypatch, out="# Title\n\n" + "body " * 200):
    """Patch Popen to record the argv and return canned markdown."""
    seen = {}

    def fake_popen(cmd, **kwargs):
        seen["cmd"] = list(cmd)
        return _FakeProc(out)

    monkeypatch.setattr(bridge.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(bridge, "_find_moli_bin", lambda: "moli.exe")
    return seen


# ── argv: the settle delay is actually passed ───────────────────────────────

def test_settle_delay_passed_to_moli(monkeypatch):
    seen = _capture_cmd(monkeypatch)
    res = bridge._moli_fetch_call("https://example.com/x", proxy_url=None)
    assert res and res["chars"] > 0
    assert "--delay-ms" in seen["cmd"]
    assert seen["cmd"][seen["cmd"].index("--delay-ms") + 1] == str(bridge.MOLI_SETTLE_MS)


def test_settle_zero_omits_flag(monkeypatch):
    seen = _capture_cmd(monkeypatch)
    bridge._moli_fetch_call("https://example.com/x", proxy_url=None, settle_ms=0)
    assert "--delay-ms" not in seen["cmd"]


def test_custom_settle_value(monkeypatch):
    seen = _capture_cmd(monkeypatch)
    bridge._moli_fetch_call("https://example.com/x", proxy_url=None, settle_ms=2500)
    assert seen["cmd"][seen["cmd"].index("--delay-ms") + 1] == "2500"


# ── mfetch behaviour ────────────────────────────────────────────────────────

def _args(url, settle=8000):
    return types.SimpleNamespace(url=url, max_chars=8000, no_cache=True,
                                 moli_timeout=30.0, moli_settle=settle,
                                 proxy=False, proxy_url="http://127.0.0.1:2080",
                                 cookies="", no_wayback=True)


def _rendered(text):
    return {"text": text, "title": "T", "chars": len(text)}


def test_thin_shell_falls_back_to_read_ladder(wall, monkeypatch):
    """A short shell with NO marker from our list must NOT be served as
    content — the measured CF 429 dump was ~496 chars of neutral boilerplate.
    The read ladder gets the call instead."""
    thin = "Content is loading. " * 8          # 160 chars, no challenge marker
    assert len(thin) < bridge._MOLI_MIN_CONTENT
    monkeypatch.setattr(bridge, "_moli_fetch_call",
                        lambda *a, **k: _rendered(thin))
    sentinel = {"url": "u", "source": "direct", "chars": 1234, "text": "real"}
    monkeypatch.setattr(bridge, "cmd_read", lambda mod, args: sentinel)
    out = bridge.cmd_mfetch(None, _args("https://thin.example/"))
    assert out is sentinel
    assert out["source"] == "direct"


def test_challenge_markup_is_reported_as_blocked(wall, monkeypatch):
    text = "Just a moment... " * 60            # long enough, marker present
    monkeypatch.setattr(bridge, "_moli_fetch_call", lambda *a, **k: _rendered(text))
    out = bridge.cmd_mfetch(None, _args("https://chal.example/"))
    assert out["challenge"] is True
    assert out["blocked"] == "cloudflare"
    assert wall._load()["chal.example"]["wall_streak"] == 1


def test_real_content_returned_from_moli(wall, monkeypatch):
    text = "# Article\n\n" + "real sentence about the topic. " * 40
    monkeypatch.setattr(bridge, "_moli_fetch_call", lambda *a, **k: _rendered(text))
    out = bridge.cmd_mfetch(None, _args("https://good.example/"))
    assert out["source"] == "moli"
    assert out["chars"] == len(text)


def test_settle_from_args_reaches_call(wall, monkeypatch):
    seen = {}

    def fake(url, proxy_url=None, timeout=None, settle_ms=None):
        seen["settle"] = settle_ms
        return _rendered("# T\n\n" + "words " * 200)

    monkeypatch.setattr(bridge, "_moli_fetch_call", fake)
    bridge.cmd_mfetch(None, _args("https://settle.example/", settle=1234))
    assert seen["settle"] == 1234


def test_empty_render_falls_back(wall, monkeypatch):
    monkeypatch.setattr(bridge, "_moli_fetch_call", lambda *a, **k: None)
    sentinel = {"url": "u", "source": "wayback", "chars": 900, "text": "arch"}
    monkeypatch.setattr(bridge, "cmd_read", lambda mod, args: sentinel)
    assert bridge.cmd_mfetch(None, _args("https://none.example/")) is sentinel
