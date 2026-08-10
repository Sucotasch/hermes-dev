"""Unit tests for _check_url_live with a fake session — no network involved.

Locks in the retry-path fixes: hard 403/429/451/503 are marked blocked, the
503 direct-GET retry is reachable, and DNS/timeout errors are reported as dead.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ddg_search

_LONG_HTML = "<html><body>" + ("<p>word</p>" * 200) + "</body></html>"


class _FakeResp:
    def __init__(self, status_code=200, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {
            "content-type": "text/html",
            "content-length": str(len(text)),
        }


class _FakeSession:
    def __init__(self, head=200, get=200, get_text=_LONG_HTML):
        self._head = head
        self._get = get
        self._get_text = get_text

    def head(self, url, **kw):
        return _FakeResp(status_code=self._head)

    def get(self, url, **kw):
        return _FakeResp(status_code=self._get, text=self._get_text)


class _FakeSessionDnsFail:
    def head(self, url, **kw):
        raise Exception("getaddrinfo failed for example.com")

    def get(self, url, **kw):
        raise Exception("getaddrinfo failed for example.com")


def _setup(monkeypatch, head=200, get=200, dns_fail=False, no_sleep=False):
    monkeypatch.setattr(
        ddg_search, "_get_session",
        lambda domain=None: _FakeSessionDnsFail() if dns_fail else _FakeSession(head=head, get=get),
    )
    monkeypatch.setattr(ddg_search, "USE_PROXY", False)
    monkeypatch.setattr(ddg_search, "PROXY_URL", None)
    if no_sleep:
        monkeypatch.setattr(time, "sleep", lambda s: None)  # avoid the real 2s 503 delay


def test_200_alive(monkeypatch):
    _setup(monkeypatch, 200, 200)
    r = ddg_search._check_url_live("https://example.com/page", timeout=3)
    assert r["alive"] is True
    assert r["status"] == 200
    assert r["text_length"] >= 500


def test_404_dead(monkeypatch):
    _setup(monkeypatch, 404)
    r = ddg_search._check_url_live("https://example.com/page", timeout=3)
    assert r["alive"] is False
    assert r["blocked"] is False
    assert "404" in (r["error"] or "")


def test_403_blocked_flag(monkeypatch):
    # Hard 403 without proxy must be marked blocked (previously blocked=False)
    _setup(monkeypatch, 403)
    r = ddg_search._check_url_live("https://example.com/page", timeout=3)
    assert r["alive"] is False
    assert r["blocked"] is True
    assert "403" in (r["error"] or "")


def test_503_reaches_direct_retry(monkeypatch):
    # 503 without proxy now attempts one direct GET retry (previously dead branch)
    _setup(monkeypatch, 503, get=503, no_sleep=True)
    r = ddg_search._check_url_live("https://example.com/page", timeout=3)
    assert r["alive"] is False
    assert r["blocked"] is True


def test_503_recovers_on_retry(monkeypatch):
    # 503 on HEAD, but the direct GET retry succeeds → alive
    _setup(monkeypatch, 503, get=200, no_sleep=True)
    r = ddg_search._check_url_live("https://example.com/page", timeout=3)
    assert r["alive"] is True
    assert r["blocked"] is False


def test_dns_error_dead(monkeypatch):
    _setup(monkeypatch, 200, dns_fail=True)
    r = ddg_search._check_url_live("https://example.com/page", timeout=3)
    assert r["alive"] is False
    assert "getaddrinfo" in (r["error"] or "").lower()
