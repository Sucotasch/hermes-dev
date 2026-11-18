"""Refresh the Imagus sieve ruleset — the full-size image upgrade rules.

Why this module exists
----------------------
Two problems with shipping a snapshot inside the repo:

1. **Staleness.** The upstream ruleset changes often (kuzn123/Imagus-Sieve-RuBoard
   was pushed 3 days before this file was written). A copy committed in July was
   four months old by November.
2. **Licensing.** That upstream repo declares **no license**. Redistributing a
   snapshot inside our repository is therefore not something we can justify, so
   the rules are fetched on demand into the local cache instead of being
   vendored. Our own code stays ours; the data stays on the upstream host.

Design
------
* No network on import — every call is explicit.
* Cache lives beside the bridge cache: ``%TEMP%/hermes_web_cache/``.
* ``latest_rules_path()`` is what the loader calls: cache if it parses, else a
  bundled file if one exists (backwards compatible), else None → the sieve
  disables itself (fail-open; image extraction still works, just without the
  full-size upgrade rules).
* Refresh explicitly::

      python plugins/web-tools/ddg/sieve_update.py

  Exit code 0 on success, 1 on failure; ASCII-only output (cp1251-safe).
"""

import hashlib
import json
import os
import tempfile
import time
import urllib.request

SIEVE_URL = ("https://raw.githubusercontent.com/kuzn123/"
             "Imagus-Sieve-RuBoard/master/update.txt")
TIMEOUT = 20  # seconds, explicit command only

_HERE = os.path.dirname(os.path.abspath(__file__))
_RESOURCES_DIR = os.path.join(_HERE, "resources")
_CACHE_DIR = os.path.join(tempfile.gettempdir(), "hermes_web_cache")
CACHE_PATH = os.path.join(_CACHE_DIR, "imagus_sieve.json")
META_PATH = os.path.join(_CACHE_DIR, "imagus_sieve.meta.json")

MIN_RULES = 20          # a real ruleset has hundreds; this rejects garbage
MIN_BYTES = 10_000

# Auto-fetch, used only when NO rules are available at all (fresh clone).
# Bounded on purpose: a short timeout, one attempt per ATTEMPT_TTL, and a
# negative-cache marker so an offline machine does not pay the timeout on
# every run. Kill switch: HERMES_SIEVE_AUTOFETCH=0.
AUTOFETCH_TIMEOUT = 6   # seconds
AUTOFETCH_TTL = 24 * 3600
ATTEMPT_MARKER_NAME = "imagus_sieve.attempt"


def _attempt_marker():
    return os.path.join(_CACHE_DIR, ATTEMPT_MARKER_NAME)


def _looks_like_sieve(data):
    """True for a plausible Imagus ruleset: dict of rule objects."""
    try:
        if not isinstance(data, dict) or len(data) < MIN_RULES:
            return False
        hits = 0
        for value in data.values():
            if isinstance(value, dict) and any(
                    k in value for k in ("img", "link", "res", "to", "url")):
                hits += 1
                if hits >= MIN_RULES:
                    return True
        return False
    except Exception:
        return False


def fetch(url=SIEVE_URL, timeout=TIMEOUT):
    """Download the ruleset text. Raises on network/HTTP errors."""
    req = urllib.request.Request(url, headers={"User-Agent": "hermes-dev-sieve-update/1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return raw.decode("utf-8", errors="replace")


def update(url=SIEVE_URL, timeout=TIMEOUT):
    """Fetch, validate and cache the ruleset. Fail-open: returns a dict."""
    try:
        text = fetch(url, timeout)
        data = json.loads(text)
        if not _looks_like_sieve(data):
            return {"ok": False, "error": "downloaded payload is not a sieve ruleset"}
        if len(text) < MIN_BYTES:
            return {"ok": False, "error": "downloaded payload is suspiciously small"}
        os.makedirs(_CACHE_DIR, exist_ok=True)
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, CACHE_PATH)
        meta = {
            "source": url,
            "fetched_at": time.time(),
            "bytes": len(text.encode("utf-8")),
            "rules": len(data),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        tmp_meta = META_PATH + ".tmp"
        with open(tmp_meta, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2)
        os.replace(tmp_meta, META_PATH)
        return {"ok": True, **meta, "path": CACHE_PATH}
    except Exception as exc:                        # noqa: BLE001 - fail-open
        return {"ok": False, "error": str(exc)[:200]}


def _cached_rules_path():
    """The cache file if it parses as a sieve, else None."""
    try:
        if not os.path.exists(CACHE_PATH):
            return None
        with open(CACHE_PATH, "r", encoding="utf-8") as fh:
            if _looks_like_sieve(json.load(fh)):
                return CACHE_PATH
    except Exception:
        pass
    return None


def _bundled_rules_path():
    """Newest bundled snapshot, if any (backwards compatible)."""
    try:
        if not os.path.isdir(_RESOURCES_DIR):
            return None
        names = sorted(n for n in os.listdir(_RESOURCES_DIR)
                       if n.startswith("Imagus_sieve_") and n.endswith(".json"))
        for name in reversed(names):
            path = os.path.join(_RESOURCES_DIR, name)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    if _looks_like_sieve(json.load(fh)):
                        return path
            except Exception:
                continue
    except Exception:
        pass
    return None


def latest_rules_path():
    """Cache first, then a bundled snapshot, else None (sieve disables)."""
    return _cached_rules_path() or _bundled_rules_path()


def _autofetch_enabled():
    return os.environ.get("HERMES_SIEVE_AUTOFETCH", "1") != "0"


def _recent_attempt():
    """True when a recent auto-fetch attempt already failed (negative cache)."""
    try:
        return (time.time() - os.path.getmtime(_attempt_marker())) < AUTOFETCH_TTL
    except Exception:
        return False


def _mark_attempt():
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(_attempt_marker(), "w", encoding="utf-8") as fh:
            fh.write("auto-fetch attempt\n")
    except Exception:
        pass


def ensure_rules(timeout=AUTOFETCH_TIMEOUT):
    """Fetch the ruleset only when none exist; bounded and rate-limited.

    Called lazily from the sieve loader, so a run that never needs image
    upgrades never touches the network. Returns the update() dict when it
    tried, else None. Never raises.
    """
    try:
        if _cached_rules_path() or _bundled_rules_path():
            return None                     # nothing to do
        if not _autofetch_enabled() or _recent_attempt():
            return None                     # opted out, or recently failed
        result = update(timeout=timeout)
        _mark_attempt()                     # throttle retries either way
        return result
    except Exception as exc:                # noqa: BLE001 - fail-open
        return {"ok": False, "error": str(exc)[:200]}


def cache_age_days():
    """Age of the cached ruleset in days, or None when there is no cache."""
    try:
        return (time.time() - os.path.getmtime(CACHE_PATH)) / 86400.0
    except Exception:
        return None


def _main():
    print("sieve-update: fetching %s" % SIEVE_URL)
    result = update()
    if not result.get("ok"):
        print("sieve-update: FAILED - %s" % result.get("error"))
        print("sieve-update: the sieve stays disabled until a refresh succeeds")
        return 1
    print("sieve-update: OK rules=%s bytes=%s sha256=%s"
          % (result.get("rules"), result.get("bytes"),
             str(result.get("sha256"))[:12]))
    print("sieve-update: cached at %s" % result.get("path"))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
