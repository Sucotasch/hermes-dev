"""Phase-2 tests: wall memory in the ddg_search validation pool.

Covers the two module helpers (`_wall_gate_check`, `_wall_note_check`) that
wire persistent wall state into `_validate_one` — the single `_check_url_live`
call site. The hot fetcher itself is untouched, so these tests run with no
network at all. Run from plugins/web-tools/ddg/ with the system Python::

    python -m pytest test_wall_pipeline.py -q
"""

import shutil
import sys
import uuid
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
REPO_ROOT = HERE.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import ddg_search  # noqa: E402
import wall_memory as wm  # noqa: E402

T0 = 1_000_000.0


@pytest.fixture()
def state_dir():
    """Hermetic per-test dir (workspace-based; the DSH sandbox breaks pytest
    tmp_path with WinError 5 — see AGENTS.md gotchas)."""
    d = HERE / ".wall_test_state" / uuid.uuid4().hex
    d.mkdir(parents=True, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def wall(state_dir, monkeypatch):
    wm._reset_for_tests(str(state_dir / "walls.json"))
    monkeypatch.setattr(wm, "_now", lambda: T0)
    yield wm
    wm._reset_for_tests()


def _check(**over):
    """A `_check_url_live`-shaped dict with test overrides."""
    base = {"alive": False, "status": None, "content_type": "",
            "content_length": 0, "text_length": 0, "text_words": 0,
            "blocked": False, "proxy_used": False, "error": None, "body": None}
    base.update(over)
    return base


# ── _wall_gate_check ────────────────────────────────────────────────────────

def test_gate_check_boxed_host_returns_synthetic_result(wall):
    wm.note_wall("box.example", "cloudflare")
    res = ddg_search._wall_gate_check("http://box.example/page")
    assert res is not None
    assert res["alive"] is False
    assert res["blocked"] is True
    assert res["status"] is None
    assert res["body"] is None
    assert "wall.cooldown" in res["error"]
    assert str(int(wm.WALL_BOX_BASE)) in res["error"]


def test_gate_check_fresh_host_returns_none(wall):
    assert ddg_search._wall_gate_check("http://fresh.example/page") is None


def test_gate_check_throttled_host_sleeps_ladder(wall, monkeypatch):
    wm.note_throttled("slow.example")
    wm.note_throttled("slow.example")          # rung 4 → 0.2 × 16 = 3.2 s
    slept = []
    monkeypatch.setattr(ddg_search.time, "sleep", slept.append)
    assert ddg_search._wall_gate_check("http://slow.example/x") is None
    assert len(slept) == 1
    lo, hi = 0.75 * 0.2 * 16, 1.25 * 0.2 * 16
    assert lo <= slept[0] <= min(hi, wm.MAX_LADDER_WAIT)


def test_gate_check_no_module_fails_open(wall, monkeypatch):
    monkeypatch.setattr(ddg_search, "_WALL", None)
    assert ddg_search._wall_gate_check("http://any.example/") is None
    ddg_search._wall_note_check("http://any.example/", _check(alive=True))
    assert wm._load() == {}


def test_gate_check_synthetic_flows_like_a_live_wall(wall):
    """The synthetic result must classify exactly like a real blocked check:
    not deferred, blocked → the pool's quarantine path (see the consumer loop)."""
    wm.note_wall("box.example", "cloudflare")
    res = ddg_search._wall_gate_check("http://box.example/page")
    reason = (res.get("error") or "").lower()
    is_deferred = ((res.get("status") in (503, 504)) or
                   ("timeout" in reason) or ("getaddrinfo" in reason))
    assert not is_deferred and res.get("blocked")


# ── _wall_note_check ────────────────────────────────────────────────────────

def test_note_alive_decays_and_clears(wall):
    wm.note_wall("ok.example", "cloudflare")
    ddg_search._wall_note_check("http://ok.example/x", _check(alive=True))
    e = wm._load()["ok.example"]
    assert e["boxed_until"] == 0.0 and e["wall_streak"] == 0


def test_note_403_boxes(wall):
    ddg_search._wall_note_check(
        "http://denied.example/x", _check(status=403, blocked=True,
                                         error="HTTP 403"))
    e = wm._load()["denied.example"]
    assert e["wall_streak"] == 1
    assert e["boxed_until"] == pytest.approx(T0 + wm.WALL_BOX_BASE)


def test_note_429_throttles_but_does_not_box(wall):
    ddg_search._wall_note_check(
        "http://busy.example/x", _check(status=429, blocked=True,
                                       error="HTTP 429"))
    e = wm._load()["busy.example"]
    assert e["rung"] == 2
    assert not e.get("boxed_until")          # throttled, never boxed
    assert wm.gate("busy.example")[0] is True   # throttled, not boxed


def test_note_503_throttles(wall):
    ddg_search._wall_note_check(
        "http://down.example/x", _check(status=503, blocked=True,
                                       error="HTTP 503"))
    assert wm._load()["down.example"]["rung"] == 2


def test_note_challenge_shell_on_200_boxes(wall):
    ddg_search._wall_note_check(
        "http://sneaky.example/x",
        _check(alive=False, status=200, blocked=True,
               error="blocked (captcha/cloudflare/etc)"))
    assert wm._load()["sneaky.example"]["wall_streak"] == 1


def test_note_404_never_creates_state(wall):
    ddg_search._wall_note_check(
        "http://gone.example/x", _check(status=404, error="HTTP 404"))
    assert "gone.example" not in wm._load()


def test_note_dns_error_never_creates_state(wall):
    ddg_search._wall_note_check(
        "http://dead.example/x", _check(error="getaddrinfo failed"))
    assert "dead.example" not in wm._load()


def test_note_timeout_never_creates_state(wall):
    ddg_search._wall_note_check(
        "http://slowly.example/x", _check(error="connection timeout"))
    assert "slowly.example" not in wm._load()


def test_note_gate_cycle_full_round_trip(wall):
    """Wall → box → gate fast-fail → recovery clears."""
    ddg_search._wall_note_check(
        "http://cycle.example/x", _check(status=403, blocked=True,
                                         error="HTTP 403"))
    gated = ddg_search._wall_gate_check("http://cycle.example/y")
    assert gated is not None and gated["blocked"]
    ddg_search._wall_note_check("http://cycle.example/y", _check(alive=True))
    assert ddg_search._wall_gate_check("http://cycle.example/z") is None
