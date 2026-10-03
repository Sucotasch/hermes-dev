"""Unit + integration tests for wall_memory (persistent wall memory + backoff).

No network: the state file is redirected to a throwaway dir inside the repo
(``.wall_test_state/``, gitignored — pytest's %TEMP% tree is not reliably
listable inside the DSH sandbox), the clock is injected, and the bridge cases
fake the fetcher / Moli / Wayback. Run from plugins/web-tools/ddg/ with the
system Python (the venv has no pytest)::

    python -m pytest test_wall_memory.py -q
"""

import json
import os
import shutil
import sys
import threading
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

import wall_memory as wm  # noqa: E402

T0 = 1_000_000.0


@pytest.fixture()
def state_dir():
    """Hermetic per-test dir (workspace-based; sandbox-safe, no %TEMP% ACLs)."""
    d = HERE / ".wall_test_state" / uuid.uuid4().hex
    d.mkdir(parents=True, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def fresh(state_dir, monkeypatch):
    """Isolated state file + fixed clock."""
    wm._reset_for_tests(str(state_dir / "walls.json"))
    monkeypatch.setattr(wm, "_now", lambda: T0)
    yield wm
    wm._reset_for_tests()


# ── module unit tests ───────────────────────────────────────────────────────

def test_gate_unknown_host_allows(fresh):
    assert wm.gate("example.com") == (True, 0.0, None)


def test_gate_empty_host_allows(fresh):
    assert wm.gate("") == (True, 0.0, None)


def test_host_key_strips_www(fresh):
    wm.note_wall("WWW.Example.com", "cloudflare")
    st = wm._load()
    assert "example.com" in st
    assert "www.example.com" not in st


def test_throttle_ladder_escalates_caps_and_decays(fresh):
    for _ in range(5):
        wm.note_throttled("t.example")
    assert wm._load()["t.example"]["rung"] == 6      # 0→2→4→6, then capped
    allowed, wait, reason = wm.gate("t.example")
    assert allowed and reason == "throttled"
    expected = min(wm.BASE_DELAY * (2 ** 6) * wm._jitter("t.example"),
                   wm.MAX_LADDER_WAIT)
    assert wait == pytest.approx(expected)
    assert wait <= wm.MAX_LADDER_WAIT
    wm.note_ok("t.example")
    assert wm._load()["t.example"]["rung"] == 5      # success decays by one


def test_wall_box_ladder_gate_and_clear(fresh, monkeypatch):
    boxes = []
    for _ in range(6):
        wm.note_wall("w.example", "cloudflare")
        boxes.append(wm._load()["w.example"]["boxed_until"] - T0)
    assert boxes[0] == pytest.approx(wm.WALL_BOX_BASE)          # 15 m
    assert boxes[1] == pytest.approx(2 * wm.WALL_BOX_BASE)      # 30 m
    assert boxes[2] == pytest.approx(4 * wm.WALL_BOX_BASE)      # 1 h
    assert boxes[3] == pytest.approx(wm.WALL_BOX_CAP)           # capped 2 h
    assert boxes[4] == pytest.approx(wm.WALL_BOX_CAP)
    assert boxes[5] == pytest.approx(wm.WALL_BOX_CAP)

    allowed, wait, reason = wm.gate("w.example")
    assert not allowed and reason == "wall.cooldown"
    assert wait == pytest.approx(wm.WALL_BOX_CAP)

    # As the clock advances the remaining box time shrinks…
    monkeypatch.setattr(wm, "_now", lambda: T0 + wm.WALL_BOX_CAP / 2)
    allowed, wait, reason = wm.gate("w.example")
    assert not allowed
    assert wait == pytest.approx(wm.WALL_BOX_CAP / 2)

    # …and expiry re-allows the host.
    monkeypatch.setattr(wm, "_now", lambda: T0 + wm.WALL_BOX_CAP + 1)
    assert wm.gate("w.example") == (True, 0.0, None)

    # A real page clears everything.
    wm.note_ok("w.example")
    e = wm._load()["w.example"]
    assert e["boxed_until"] == 0.0 and e["wall_streak"] == 0


def test_corrupt_state_fails_open(state_dir, monkeypatch):
    state_file = state_dir / "walls.json"
    state_file.write_text("{ not json", encoding="utf-8")
    wm._reset_for_tests(str(state_file))
    monkeypatch.setattr(wm, "_now", lambda: T0)
    assert wm.gate("anything.example") == (True, 0.0, None)
    wm.note_wall("fresh.example", "cloudflare")     # rewrites a valid file
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert "fresh.example" in data["hosts"]
    wm._reset_for_tests()


def test_ttl_drops_old_entries(state_dir, monkeypatch):
    state_file = state_dir / "walls.json"
    wm._reset_for_tests(str(state_file))
    monkeypatch.setattr(wm, "_now", lambda: T0)
    with wm._LOCK:
        st = wm._load()
        st["old.example"] = {"verdict": "cloudflare", "rung": 0,
                             "wall_streak": 1, "boxed_until": 0.0,
                             "last_seen": T0 - wm.STATE_TTL - 10}
        for i in range(wm.HOST_CAP - 1):
            st[f"h{i:05d}.example"] = {"verdict": "generic", "rung": 0,
                                       "wall_streak": 1, "boxed_until": 0.0,
                                       "last_seen": T0 - i}
    wm._save()
    wm._reset_for_tests(str(state_file))           # force reload from disk
    st = wm._load()
    assert "old.example" not in st                 # dropped by TTL
    assert len(st) == wm.HOST_CAP - 1
    wm._reset_for_tests()


def test_host_cap_prunes_oldest(state_dir, monkeypatch):
    state_file = state_dir / "walls.json"
    wm._reset_for_tests(str(state_file))
    monkeypatch.setattr(wm, "_now", lambda: T0)
    with wm._LOCK:
        st = wm._load()
        for i in range(wm.HOST_CAP + 50):
            st[f"h{i:05d}.example"] = {"verdict": "generic", "rung": 0,
                                       "wall_streak": 1, "boxed_until": 0.0,
                                       "last_seen": T0 - i}
    wm._save()
    wm._reset_for_tests(str(state_file))
    st = wm._load()
    assert len(st) == wm.HOST_CAP
    assert "h00000.example" in st                  # newest survived
    assert "h02049.example" not in st             # oldest pruned
    wm._reset_for_tests()


def test_kill_switch_disables_everything(state_dir, monkeypatch):
    wm._reset_for_tests(str(state_dir / "walls.json"))
    monkeypatch.setattr(wm, "_now", lambda: T0)
    monkeypatch.setenv("HERMES_WALL_MEMORY", "0")
    wm.note_wall("k.example", "cloudflare")
    wm.note_throttled("k.example")
    assert wm.gate("k.example") == (True, 0.0, None)
    assert wm._load() == {}
    assert not (state_dir / "walls.json").exists()
    wm._reset_for_tests()


def test_thread_smoke(fresh):
    threads = [threading.Thread(target=wm.note_wall,
                                args=(f"th{i:02d}.example", "generic"))
               for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    st = wm._load()
    for i in range(8):
        assert st[f"th{i:02d}.example"]["wall_streak"] == 1


# ── bridge integration tests (mocked network / Moli / Wayback) ─────────────

class _FakeMod:
    """Minimal wrapper stand-in: records live fetch attempts."""

    def __init__(self, source="ok", text="sample " * 200):
        self.source = source
        self.text = text
        self.calls = []

    def visit_website_enhanced(self, url, max_chars=8000, cookies=None):
        self.calls.append(url)
        return {"url": url, "title": "t", "text": self.text,
                "source": self.source}


def _args(url, no_wayback=True):
    return types.SimpleNamespace(url=url, max_chars=8000, no_cache=True,
                                 no_wayback=no_wayback, cookies="")


def test_bridge_read_boxed_host_skips_network(state_dir, monkeypatch):
    wm._reset_for_tests(str(state_dir / "walls.json"))
    monkeypatch.setattr(wm, "_now", lambda: T0)
    wm.note_wall("boxed.example", "cloudflare")
    import webtools_bridge as bridge
    mod = _FakeMod()
    res = bridge.cmd_read(mod, _args("http://boxed.example/page"))
    assert mod.calls == []                         # zero network attempts
    assert res["source"] == "failed"
    assert res["error"] == "wall.cooldown"
    assert res["retry_after"] == pytest.approx(wm.WALL_BOX_BASE)
    assert "retry" in res["next_action"]
    wm._reset_for_tests()


def test_bridge_read_boxed_host_still_serves_wayback(state_dir, monkeypatch):
    wm._reset_for_tests(str(state_dir / "walls.json"))
    monkeypatch.setattr(wm, "_now", lambda: T0)
    wm.note_wall("boxed.example", "cloudflare")
    import webtools_bridge as bridge

    def fake_wayback(url):
        return ("http://web.archive.org/web/20240101000000/http://boxed.example/page",
                "20240101000000", "http://boxed.example/page")

    monkeypatch.setattr(bridge, "_wayback_latest", fake_wayback)
    mod = _FakeMod()
    res = bridge.cmd_read(mod, _args("http://boxed.example/page",
                                     no_wayback=False))
    assert len(mod.calls) == 1                     # only the archived URL
    assert mod.calls[0].startswith("http://web.archive.org/")
    assert res["source"] == "wayback"
    assert "error" not in res                      # box ≠ "no answer"
    wm._reset_for_tests()


def test_bridge_read_challenge_creates_box(state_dir, monkeypatch):
    wm._reset_for_tests(str(state_dir / "walls.json"))
    monkeypatch.setattr(wm, "_now", lambda: T0)
    import webtools_bridge as bridge
    mod = _FakeMod(source="curl_cffi", text="Just a moment... " * 40)
    res = bridge.cmd_read(mod, _args("http://chal.example/page"))
    assert res["challenge"] is True
    assert res["blocked"] == "cloudflare"
    e = wm._load()["chal.example"]
    assert e["verdict"] == "cloudflare"
    assert e["wall_streak"] == 1
    assert e["boxed_until"] == pytest.approx(T0 + wm.WALL_BOX_BASE)
    wm._reset_for_tests()


def test_bridge_read_real_content_clears_expired_box(state_dir, monkeypatch):
    wm._reset_for_tests(str(state_dir / "walls.json"))
    monkeypatch.setattr(wm, "_now", lambda: T0)
    wm.note_wall("ok.example", "cloudflare")
    # Let the box lapse, then fetch real content → note_ok clears the entry.
    monkeypatch.setattr(wm, "_now", lambda: T0 + wm.WALL_BOX_BASE + 1)
    import webtools_bridge as bridge
    mod = _FakeMod(source="curl_cffi", text="real content " * 100)
    res = bridge.cmd_read(mod, _args("http://ok.example/page"))
    assert res["chars"] >= 500
    assert "error" not in res
    e = wm._load()["ok.example"]
    assert e["boxed_until"] == 0.0 and e["wall_streak"] == 0
    assert wm.gate("ok.example") == (True, 0.0, None)
    wm._reset_for_tests()


def test_bridge_mfetch_challenge_notes_wall(state_dir, monkeypatch):
    wm._reset_for_tests(str(state_dir / "walls.json"))
    monkeypatch.setattr(wm, "_now", lambda: T0)
    import webtools_bridge as bridge
    monkeypatch.setattr(bridge, "_moli_available", lambda: True)
    monkeypatch.setattr(
        bridge, "_moli_fetch_call",
        lambda url, proxy=None, timeout=None: {"title": "t", "chars": 300,
                                               "text": "Just a moment..."})
    args = types.SimpleNamespace(url="http://mf.example/page", max_chars=8000,
                                 no_cache=True, moli_timeout=30.0, proxy=False,
                                 proxy_url="http://127.0.0.1:2080", cookies="")
    res = bridge.cmd_mfetch(None, args)
    assert res["challenge"] is True
    assert res["blocked"] == "cloudflare"
    e = wm._load()["mf.example"]
    assert e["verdict"] == "cloudflare" and e["wall_streak"] == 1
    wm._reset_for_tests()
