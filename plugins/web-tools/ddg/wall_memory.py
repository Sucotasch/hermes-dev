"""wall_memory.py — persistent per-domain wall memory with honest backoff.

Phase 1 consumer: ``webtools_bridge.py`` (``read`` gate + ``mfetch`` notes).
Phase 2 (separate decision, not wired yet): the deep-research pipeline — the
module is importable from the same plugins dir as ``ddg_search``.

Why it exists
-------------
40-46% of candidate URLs are walled (AGENTS.md). Today every bridge invocation
and every pipeline run re-pays the same doomed fetches: a full HTTP round-trip
plus proxy retry plus Jina just to be told "just a moment" again. This module
remembers, per host, that a site keeps serving anti-bot shells, and lets the
caller answer honestly in milliseconds instead of burning the round-trip.
Archive recovery (Wayback) and every other fallback stay available to the
caller: a box means "stop hammering the live host", not "no answer".

Design (approved 2026-10, see .plans/2026-10-02_wall_memory_design.md):

* Constants marked ``[donsetch]`` were verified in donsetch sources
  (EVALUATED_TOOLS.md §5b — AGPL project, ideas only, no code copied).
  Constants marked ``[ours]`` are our own tuning: donsetch's verified code
  covers only the short throttle ladder; the long "15m→2h" cooldown appears
  in their README but was NOT found in the accessible sources.
* Fail-open everywhere: a missing/corrupt state file, a read-only temp dir or
  an import problem in the caller degrades to "allow the fetch". The only
  network path (search → read → expand) can never be blocked by this module's
  own failures.
* wall != dead: only challenge/blocked/login verdicts and HTTP throttling
  create state. 404s, DNS errors, timeouts and connection resets never do —
  a refused request is never evidence that a page is gone.
* No policy here: no topic/query branching, only transport-level per-host
  state. The backend stays monotone (AGENTS.md invariants).
* Kill switch: ``HERMES_WALL_MEMORY=0`` turns every call into a no-op
  (also the A/B lever for before/after comparisons).

State file: ``%TEMP%/hermes_web_cache/walls.json`` — same directory as the
bridge disk cache. Schema::

    {"version": 1, "hosts": {host: {"verdict": str, "rung": int,
     "wall_streak": int, "boxed_until": float_epoch, "last_seen": float_epoch}}}
"""

import hashlib
import json
import os
import tempfile
import threading
import time

VERSION = 1
_CACHE_DIRNAME = "hermes_web_cache"
_STATE_FILENAME = "walls.json"

# [donsetch] verified constants (src/crawl/governor.rs; EVALUATED_TOOLS §5b)
BASE_DELAY = 0.2            # s — throttle ladder base
MAX_BACKOFF_RUNG = 6        # ladder top: 0.2 s × 2^6
MAX_LADDER_WAIT = 7.0       # s — cap on self-inferred waits ("truth, not time")
JITTER_LO, JITTER_HI = 0.75, 1.25   # deterministic, host-seeded
STATE_TTL = 7 * 86_400      # s — entries older than this drop on load
HOST_CAP = 2000             # max hosts kept on disk

# [ours] wall-box ladder: 15m → 30m → 1h → 2h (cap). Tunable.
WALL_BOX_BASE = 15 * 60
WALL_BOX_CAP = 2 * 3600

_STATE_PATH = os.path.join(tempfile.gettempdir(), _CACHE_DIRNAME, _STATE_FILENAME)
_LOCK = threading.RLock()
_STATE = {}
_LOADED = False


def _now():
    return time.time()


def _enabled():
    return os.environ.get("HERMES_WALL_MEMORY", "1") != "0"


def _host_key(host):
    h = (host or "").strip().lower()
    return h[4:] if h.startswith("www.") else h


def _jitter(host):
    """Deterministic jitter in [0.75, 1.25) seeded from the host name
    (no RNG state — reproducible, mirrors donsetch's xorshift idea)."""
    frac = int.from_bytes(hashlib.sha256(host.encode("utf-8")).digest()[:4],
                          "big") / 0xFFFFFFFF
    return JITTER_LO + (JITTER_HI - JITTER_LO) * frac


def _load():
    global _STATE, _LOADED
    if _LOADED:
        return _STATE
    state = {}
    try:
        with open(_STATE_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if (isinstance(raw, dict) and raw.get("version") == VERSION
                and isinstance(raw.get("hosts"), dict)):
            cutoff = _now() - STATE_TTL
            live = [(k, v) for k, v in raw["hosts"].items()
                    if isinstance(v, dict)
                    and float(v.get("last_seen") or 0) >= cutoff]
            live.sort(key=lambda kv: float(kv[1].get("last_seen") or 0),
                      reverse=True)
            state = dict(live[:HOST_CAP])
    except Exception:
        state = {}  # missing/corrupt file → empty state (fail-open)
    _STATE = state
    _LOADED = True
    return _STATE


def _save():
    try:
        os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
        ordered = sorted(_STATE.items(),
                         key=lambda kv: float(kv[1].get("last_seen") or 0),
                         reverse=True)[:HOST_CAP]
        tmp = _STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": VERSION, "hosts": dict(ordered)}, f)
        os.replace(tmp, _STATE_PATH)
    except Exception:
        pass  # read-only temp dir / busy disk: keep working, skip persisting


def _entry(key):
    st = _load()
    e = st.get(key)
    if not isinstance(e, dict):
        e = {}
        st[key] = e
    return e


def gate(host):
    """→ ``(allowed, wait_s, reason)`` for a host.

    ``allowed=False, reason="wall.cooldown"`` — the host is boxed after
    repeated walls; the caller should skip the live fetch and answer honestly
    (fast), keeping archive fallbacks to itself.

    ``allowed=True, wait_s>0, reason="throttled"`` — the host throttled us
    recently; sleep ``wait_s`` (≤ MAX_LADDER_WAIT) before touching it again.

    Fail-open: any internal error allows the fetch.
    """
    try:
        if not _enabled():
            return True, 0.0, None
        key = _host_key(host)
        if not key:
            return True, 0.0, None
        with _LOCK:
            e = _load().get(key)
            if not isinstance(e, dict):
                return True, 0.0, None
            now = _now()
            boxed_until = float(e.get("boxed_until") or 0)
            if boxed_until > now:
                return False, boxed_until - now, "wall.cooldown"
            rung = int(e.get("rung") or 0)
            if rung > 0:
                wait = min(BASE_DELAY * (2 ** rung) * _jitter(key),
                           MAX_LADDER_WAIT)
                return True, wait, "throttled"
            return True, 0.0, None
    except Exception:
        return True, 0.0, None


def note_ok(host):
    """A real page came back: clear the box, decay the throttle rung.

    No-op for unknown hosts — good behaviour needs no memory, which keeps the
    state file small (only misbehaving hosts are ever recorded).
    """
    try:
        if not _enabled():
            return
        key = _host_key(host)
        if not key:
            return
        with _LOCK:
            e = _load().get(key)
            if not isinstance(e, dict):
                return
            e["wall_streak"] = 0
            e["boxed_until"] = 0.0
            e["rung"] = max(0, int(e.get("rung") or 0) - 1)
            e["verdict"] = "ok"
            e["last_seen"] = _now()
            _save()
    except Exception:
        pass


def note_throttled(host):
    """HTTP 429/503 seen: raise the throttle rung (+2, cap 6). [donsetch]

    Phase 1 note: the bridge cannot see the HTTP status in
    ``visit_website_enhanced`` output, so in production this is driven by
    phase-2 pipeline wiring only (``ddg_search._check_url_live`` has status);
    the ladder is fully exercised by tests meanwhile.
    """
    try:
        if not _enabled():
            return
        key = _host_key(host)
        if not key:
            return
        with _LOCK:
            e = _entry(key)
            e["rung"] = min(MAX_BACKOFF_RUNG, int(e.get("rung") or 0) + 2)
            e["verdict"] = "throttled"
            e["last_seen"] = _now()
            _save()
    except Exception:
        pass


def note_wall(host, verdict=None):
    """Anti-bot shell / login gate seen: box the host. [ours]

    Box ladder: 15m, 30m, 1h, 2h (cap). ``verdict`` is the stable blocked
    code ("cloudflare" | "aws_waf" | "recaptcha" | "login" | "generic").
    """
    try:
        if not _enabled():
            return
        key = _host_key(host)
        if not key:
            return
        with _LOCK:
            e = _entry(key)
            streak = int(e.get("wall_streak") or 0) + 1
            box = min(WALL_BOX_BASE * (2 ** (streak - 1)), WALL_BOX_CAP)
            e["wall_streak"] = streak
            e["boxed_until"] = _now() + box
            e["verdict"] = verdict or "generic"
            e["last_seen"] = _now()
            _save()
    except Exception:
        pass


def reset(host):
    """Manual un-box for a single host (operational escape hatch)."""
    try:
        key = _host_key(host)
        with _LOCK:
            if _load().pop(key, None) is not None:
                _save()
    except Exception:
        pass


def _reset_for_tests(state_path=None):
    """Test hook: point the state at a fresh path and drop in-memory caches."""
    global _STATE_PATH, _STATE, _LOADED
    with _LOCK:
        _STATE_PATH = state_path or os.path.join(
            tempfile.gettempdir(), _CACHE_DIRNAME, _STATE_FILENAME)
        _STATE = {}
        _LOADED = False
