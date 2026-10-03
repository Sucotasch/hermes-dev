"""Unit + integration tests for Netscape cookies.txt support (user cookies).

Covers _common.parse_netscape_cookies / cookie_header_for (RFC 6265 domain /
path / secure matching, donsetch jar caps), the vwe visit_website(cookies=...)
pass-through, and the bridge --cookies wiring — cookies go ONLY to the direct
fetch of the target host, never to Jina or Wayback. Run from
plugins/web-tools/ddg/ with the system Python::

    python -m pytest test_cookie_file.py -q
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

import _common  # noqa: E402

T0 = 1_000_000.0


@pytest.fixture()
def state_dir():
    """Hermetic per-test dir (workspace-based; sandbox-safe)."""
    d = HERE / ".wall_test_state" / uuid.uuid4().hex
    d.mkdir(parents=True, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def wall_state(state_dir):
    """Keep wall-memory state out of the real %TEMP% file during bridge tests."""
    import wall_memory as wm
    wm._reset_for_tests(str(state_dir / "walls.json"))
    yield
    wm._reset_for_tests()


# ── parser ──────────────────────────────────────────────────────────────────

def test_parse_standard_tabs():
    jar = _common.parse_netscape_cookies(
        ".example.com\tTRUE\t/\tTRUE\t1799999999\tsid\tabc123")
    assert len(jar) == 1
    c = jar[0]
    assert c["domain"] == ".example.com"
    assert c["path"] == "/"
    assert c["secure"] is True
    assert c["expires"] == 1799999999
    assert c["name"] == "sid"
    assert c["value"] == "abc123"


def test_parse_httponly_prefix_honored():
    jar = _common.parse_netscape_cookies(
        "#HttpOnly_.example.com\tTRUE\t/\tFALSE\t0\tcf_clearance\tX1")
    assert jar and jar[0]["domain"] == ".example.com"
    assert jar[0]["name"] == "cf_clearance"


def test_parse_comments_and_blank_lines_skipped():
    text = ("# Netscape HTTP Cookie File\n\nshort line\n"
            "httpbin.org\tFALSE\t/\tFALSE\t0\ta\t1\n")
    jar = _common.parse_netscape_cookies(text)
    assert len(jar) == 1 and jar[0]["name"] == "a"


def test_parse_lenient_whitespace_value_keeps_spaces():
    jar = _common.parse_netscape_cookies(
        "example.com FALSE / FALSE 0 theme light mode")
    assert jar and jar[0]["name"] == "theme"
    assert jar[0]["value"] == "light mode"


def test_parse_malformed_fail_open():
    assert _common.parse_netscape_cookies("") == []
    assert _common.parse_netscape_cookies("garbage") == []
    assert _common.parse_netscape_cookies(None) == []


# ── header builder ──────────────────────────────────────────────────────────

def _jar(*specs):
    return [dict(domain=d, path=p, secure=s, expires=e, name=n, value=v)
            for d, p, s, e, n, v in specs]


def test_header_domain_matching():
    jar = _jar((".example.com", "/", False, 0, "dom", "1"),
               ("other.com", "/", False, 0, "other", "2"))
    h = _common.cookie_header_for(jar, "http://a.example.com/x", now=T0)
    assert "dom=1" in h and "other" not in h
    # apex host matches the dot-domain too
    assert "dom=1" in _common.cookie_header_for(jar, "http://example.com/", now=T0)
    # no substring confusion
    assert _common.cookie_header_for(jar, "http://notexample.com/", now=T0) == ""


def test_header_exact_host_only():
    jar = _jar(("www.example.com", "/", False, 0, "w", "1"))
    got = _common.cookie_header_for(jar, "http://www.example.com/", now=T0)
    assert "w=1" in got
    assert _common.cookie_header_for(jar, "http://example.com/", now=T0) == ""
    assert _common.cookie_header_for(jar, "http://a.example.com/", now=T0) == ""


def test_header_path_boundary():
    jar = _jar(("example.com", "/a", False, 0, "p", "1"))
    assert "p=1" in _common.cookie_header_for(jar, "http://example.com/a", now=T0)
    assert "p=1" in _common.cookie_header_for(jar, "http://example.com/a/b", now=T0)
    assert _common.cookie_header_for(jar, "http://example.com/ab", now=T0) == ""


def test_header_secure_never_over_http():
    jar = _jar(("example.com", "/", True, 0, "s", "1"),
               ("example.com", "/", False, 0, "p", "2"))
    assert _common.cookie_header_for(jar, "http://example.com/", now=T0) == "p=2"
    h = _common.cookie_header_for(jar, "https://example.com/", now=T0)
    assert "s=1" in h and "p=2" in h


def test_header_expiry():
    jar = _jar(("example.com", "/", False, T0 - 10, "old", "1"),
               ("example.com", "/", False, T0 + 100, "new", "2"),
               ("example.com", "/", False, 0, "sess", "3"))
    h = _common.cookie_header_for(jar, "http://example.com/", now=T0)
    assert "old" not in h and "new=2" in h and "sess=3" in h


def test_header_longest_path_first_and_caps():
    jar = _jar(("example.com", "/", False, 0, "root", "0"),
               ("example.com", "/deep/path", False, 0, "deep", "1"))
    h = _common.cookie_header_for(jar, "http://example.com/deep/path", now=T0)
    assert h.index("deep=1") < h.index("root=0")
    # pair cap
    big = _jar(*[("example.com", "/", False, 0, f"c{i}", "v") for i in range(60)])
    h2 = _common.cookie_header_for(big, "http://example.com/", now=T0)
    assert h2.count(";") == _common.MAX_COOKIE_HEADER_PAIRS - 1
    # byte cap
    wide = _jar(*[("example.com", "/", False, 0, f"k{i}", "x" * 600)
                  for i in range(20)])
    h3 = _common.cookie_header_for(wide, "http://example.com/", now=T0)
    assert len(h3) <= _common.MAX_COOKIE_HEADER_BYTES


def test_header_fail_open():
    assert _common.cookie_header_for([], "http://x/") == ""
    assert _common.cookie_header_for(_jar(("x.com", "/", False, 0, "a", "1")), "") == ""
    assert _common.cookie_header_for(None, "http://x/") == ""


# ── vwe pass-through ────────────────────────────────────────────────────────

def test_vwe_forwards_cookies_to_direct_fetch(monkeypatch):
    import visit_website_enhanced as vwe
    seen = {}

    def fake_fetch(url, referrer=None, cookies=None):
        seen["cookies"] = cookies
        return "<html><body><p>" + "word " * 200 + "</p></body></html>"

    monkeypatch.setattr(vwe, "_fetch", fake_fetch)
    vwe.visit_website("http://pass.example/", cookies="a=1; b=2")
    assert seen["cookies"] == "a=1; b=2"
    vwe.visit_website("http://pass.example/")
    assert seen["cookies"] is None


# ── bridge integration (mocked fetcher / Wayback) ───────────────────────────

class _FakeMod:
    def __init__(self, source="ok", text="sample " * 200):
        self.source = source
        self.text = text
        self.calls = []

    def visit_website_enhanced(self, url, max_chars=8000, cookies=None):
        self.calls.append({"url": url, "cookies": cookies})
        return {"url": url, "title": "t", "text": self.text, "source": self.source}


def _args(url, cookies="", no_wayback=True):
    return types.SimpleNamespace(url=url, max_chars=8000, no_cache=True,
                                 no_wayback=no_wayback, cookies=cookies)


def test_bridge_sends_cookies_on_direct_read(state_dir, wall_state):
    import webtools_bridge as bridge
    ck = state_dir / "cookies.txt"
    ck.write_text("cookied.example\tFALSE\t/\tFALSE\t0\tsess\tabc\n",
                 encoding="utf-8")
    mod = _FakeMod()
    res = bridge.cmd_read(mod, _args("http://cookied.example/page",
                                     cookies=str(ck)))
    assert mod.calls and mod.calls[0]["cookies"] == "sess=abc"
    assert "error" not in res


def test_bridge_never_sends_cookies_to_wayback(state_dir, wall_state,
                                              monkeypatch):
    import webtools_bridge as bridge
    ck = state_dir / "cookies.txt"
    ck.write_text("cookied.example\tFALSE\t/\tFALSE\t0\tsess\tabc\n",
                 encoding="utf-8")

    def fake_wayback(url):
        return ("http://web.archive.org/web/20240101000000/"
                "http://cookied.example/page",
                "20240101000000", "http://cookied.example/page")

    monkeypatch.setattr(bridge, "_wayback_latest", fake_wayback)
    # Direct fetch returns a challenge shell → the wayback ladder must run.
    mod = _FakeMod(source="curl_cffi", text="Just a moment... " * 40)
    res = bridge.cmd_read(mod, _args("http://cookied.example/page",
                                     cookies=str(ck), no_wayback=False))
    assert len(mod.calls) == 2
    assert mod.calls[0]["cookies"] == "sess=abc"    # direct fetch: cookies
    assert mod.calls[1]["cookies"] is None           # wayback host: never
    assert res["source"] == "wayback"


def test_bridge_missing_cookie_file_fails_open(state_dir, wall_state):
    import webtools_bridge as bridge
    mod = _FakeMod()
    bridge.cmd_read(mod, _args("http://plain.example/page",
                               cookies=str(state_dir / "nope.txt")))
    assert mod.calls[0]["cookies"] is None
