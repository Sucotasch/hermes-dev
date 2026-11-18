"""Tests for sieve_update (fetch / validate / cache / load order).

No network: fetch() is monkeypatched. Run from plugins/web-tools/ddg/::

    python -m pytest test_sieve_update.py -q
"""

import json
import shutil
import sys
import uuid
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import sieve_update as su  # noqa: E402


def _ruleset(n=30):
    return {f"site{i}.example": {"img": rf"^cdn{i}\.example/(\w+)\.jpg", "to": r"$1.png"}
            for i in range(n)}


@pytest.fixture()
def cache_dir(monkeypatch):
    # Hermetic dir inside the repo: pytest's tmp_path is not listable under the
    # DSH sandbox (WinError 5) вЂ” see AGENTS.md gotchas.
    d = HERE / ".wall_test_state" / uuid.uuid4().hex
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(su, "_CACHE_DIR", str(d))
    monkeypatch.setattr(su, "CACHE_PATH", str(d / "imagus_sieve.json"))
    monkeypatch.setattr(su, "META_PATH", str(d / "imagus_sieve.meta.json"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


# в”Ђв”Ђ validation в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

def test_looks_like_sieve_accepts_real_shape():
    assert su._looks_like_sieve(_ruleset()) is True


def test_looks_like_sieve_rejects_garbage():
    assert su._looks_like_sieve({}) is False
    assert su._looks_like_sieve([]) is False
    assert su._looks_like_sieve("{}") is False
    assert su._looks_like_sieve({f"k{i}": "not-a-rule" for i in range(50)}) is False
    assert su._looks_like_sieve(None) is False


# в”Ђв”Ђ update() writes cache + meta в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

def test_update_writes_cache_and_meta(cache_dir, monkeypatch):
    payload = json.dumps(_ruleset(200))
    monkeypatch.setattr(su, "fetch", lambda url=su.SIEVE_URL, timeout=su.TIMEOUT: payload)
    res = su.update()
    assert res["ok"] is True
    assert res['rules'] == 200
    assert Path(su.CACHE_PATH).exists()
    meta = json.loads(Path(su.META_PATH).read_text(encoding="utf-8"))
    assert meta['rules'] == 200 and meta["source"] == su.SIEVE_URL
    assert len(meta["sha256"]) == 64
    assert Path(su.CACHE_PATH).read_text(encoding="utf-8") == payload


def test_update_rejects_non_sieve_payload(cache_dir, monkeypatch):
    monkeypatch.setattr(su, "fetch",
                        lambda url=su.SIEVE_URL, timeout=su.TIMEOUT: "<html>404</html>")
    res = su.update()
    assert res["ok"] is False
    assert not Path(su.CACHE_PATH).exists()


def test_update_rejects_tiny_payload(cache_dir, monkeypatch):
    tiny = json.dumps(_ruleset(200))
    monkeypatch.setattr(su, "MIN_BYTES", len(tiny) + 10)
    monkeypatch.setattr(su, "fetch",
                        lambda url=su.SIEVE_URL, timeout=su.TIMEOUT: tiny)
    assert su.update()["ok"] is False


def test_update_fail_open_on_network_error(cache_dir, monkeypatch):
    def boom(*a, **k):
        raise OSError("network unreachable")
    monkeypatch.setattr(su, "fetch", boom)
    res = su.update()
    assert res["ok"] is False
    assert "network unreachable" in res["error"]


def test_update_does_not_clobber_good_cache_on_failure(cache_dir, monkeypatch):
    good = json.dumps(_ruleset(200))
    monkeypatch.setattr(su, "fetch", lambda url=su.SIEVE_URL, timeout=su.TIMEOUT: good)
    assert su.update()["ok"] is True
    before = Path(su.CACHE_PATH).read_text(encoding="utf-8")

    def boom(*a, **k):
        raise OSError("network down")
    monkeypatch.setattr(su, "fetch", boom)
    assert su.update()["ok"] is False
    assert Path(su.CACHE_PATH).read_text(encoding="utf-8") == before


# в”Ђв”Ђ load order в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

def test_latest_path_prefers_cache(cache_dir, monkeypatch):
    Path(su.CACHE_PATH).write_text(json.dumps(_ruleset(200)), encoding="utf-8")
    monkeypatch.setattr(su, "_bundled_rules_path", lambda: "/bundled/old.json")
    assert su.latest_rules_path() == su.CACHE_PATH


def test_latest_path_ignores_corrupt_cache(cache_dir, monkeypatch):
    Path(su.CACHE_PATH).write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(su, "_bundled_rules_path", lambda: "/bundled/old.json")
    assert su.latest_rules_path() == "/bundled/old.json"


def test_latest_path_none_when_nothing(cache_dir, monkeypatch):
    monkeypatch.setattr(su, "_bundled_rules_path", lambda: None)
    assert su.latest_rules_path() is None


def test_cache_age_days(cache_dir):
    assert su.cache_age_days() is None
    Path(su.CACHE_PATH).write_text(json.dumps(_ruleset(200)), encoding="utf-8")
    age = su.cache_age_days()
    assert age is not None and 0 <= age < 1


# в”Ђв”Ђ sieve.py integration в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

def test_sieve_uses_cache_over_bundled(cache_dir, monkeypatch):
    import sieve
    Path(su.CACHE_PATH).write_text(json.dumps(_ruleset(200)), encoding="utf-8")
    assert sieve._default_rules_path() == su.CACHE_PATH


def test_sieve_falls_back_when_update_module_missing(monkeypatch):
    import sieve
    monkeypatch.setitem(sys.modules, "sieve_update", None)   # import raises
    path = sieve._default_rules_path()
    assert path.endswith(sieve._SIEVE_FILENAME)


def test_cli_reports_failure(monkeypatch, capsys):
    monkeypatch.setattr(su, "fetch", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    assert su._main() == 1
    assert "FAILED" in capsys.readouterr().out

