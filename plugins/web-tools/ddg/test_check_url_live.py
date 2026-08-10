"""Unit tests for _check_url_live with a fake session — no network involved.

Locks in the retry-path fixes: hard 403/429/451/503 are marked blocked, the
503 direct-GET retry is reachable, and DNS/timeout errors are reported as dead.
"""
import sys
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


def _run(head, get=200, get_text=_LONG_HTML, dns_fail=False, no_sleep=False):
    old_session, old_proxy, old_url = ddg_search._get_session, ddg_search.USE_PROXY, ddg_search.PROXY_URL
    old_sleep = None
    try:
        ddg_search._get_session = (
            lambda domain=None: _FakeSessionDnsFail() if dns_fail else _FakeSession(head=head, get=get, get_text=get_text)
        )
        ddg_search.USE_PROXY = False
        ddg_search.PROXY_URL = None
        if no_sleep:
            old_sleep = ddg_search.time.sleep
            ddg_search.time.sleep = lambda s: None  # patched for the 2s 503 retry delay
        return ddg_search._check_url_live("https://example.com/page", timeout=3)
    finally:
        ddg_search._get_session = old_session
        ddg_search.USE_PROXY = old_proxy
        ddg_search.PROXY_URL = old_url
        if old_sleep is not None:
            ddg_search.time.sleep = old_sleep


def test_200_alive():
    r = _run(200, 200)
    assert r["alive"] is True
    assert r["status"] == 200
    assert r["text_length"] >= 500


def test_404_dead():
    r = _run(404)
    assert r["alive"] is False
    assert r["blocked"] is False
    assert "404" in (r["error"] or "")


def test_403_blocked_flag():
    # Hard 403 without proxy must be marked blocked (previously blocked=False)
    r = _run(403)
    assert r["alive"] is False
    assert r["blocked"] is True
    assert "403" in (r["error"] or "")


def test_503_reaches_direct_retry():
    # 503 without proxy now attempts one direct GET retry (previously dead branch)
    r = _run(503, get=503, no_sleep=True)
    assert r["alive"] is False
    assert r["blocked"] is True


def test_503_recovers_on_retry():
    # 503 on HEAD, but the direct GET retry succeeds → alive
    r = _run(503, get=200, no_sleep=True)
    assert r["alive"] is True
    assert r["blocked"] is False


def test_dns_error_dead():
    r = _run(200, dns_fail=True)
    assert r["alive"] is False
    assert "getaddrinfo" in (r["error"] or "").lower()
