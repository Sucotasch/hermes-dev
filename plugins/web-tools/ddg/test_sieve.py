#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for plugins/web-tools/ddg/sieve.py (Imagus static engine)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sieve


# --- JS→Python converter -------------------------------------------------

def test_js_expr_simple_group():
    assert sieve._js_expr_to_python_single("$[1]") == "g(1)"


def test_js_expr_string_literal():
    assert sieve._js_expr_to_python_single("'abc'") == "'abc'"


def test_js_concat_with_groups():
    # "https://" + $[1] + ".jpg" → three-part concatenation
    py = sieve._js_expr_to_python("'https://'+$[1]+'.jpg'")
    assert py is not None and "g(1)" in py


def test_js_ternary():
    py = sieve._js_expr_to_python("$[2] ? 'a' : 'b'")
    assert py is not None and "if" in py


def test_js_replace_conversion():
    py = sieve._js_expr_to_python_single("$[1].replace(/thumb/, 'full')")
    assert py is not None and "_re.sub" in py


def test_js_dom_rule_rejected():
    assert sieve._try_parse_imagus_js(":return this.node.closest('a').href") is None


def test_build_callable_runs():
    fn = sieve._build_js_callable("'x' + $[1]")
    assert fn is not None
    import re as _re
    m = _re.search(r"a(b)", "ab")  # group 1 = "b"
    assert fn(m) == "xb"


def test_ext_variants_expansion_in_callable():
    # #ext# expansion happens in _try_rule via _expand_variants, not in the
    # callable itself; verify both pieces together.
    fn = sieve._build_js_callable("'http://x/#jpg png#' + $[1]")
    assert fn is not None
    import re as _re
    m = _re.search(r"(a)", "a")
    res = fn(m)
    assert res == "http://x/#jpg png#a"
    variants = sieve._expand_variants(res)
    assert any(v.endswith("jpga") for v in variants)
    assert any(v.endswith("pnga") for v in variants)


# --- helpers --------------------------------------------------------------

def test_expand_variants():
    assert sieve._expand_variants("a#jpg png#b") == ["ajpgb", "apngb"]
    assert sieve._expand_variants("plain") == ["plain"]


def test_sanitize_target():
    assert sieve._sanitize_imagus_target("$1") == "\\g<1>"
    assert sieve._sanitize_imagus_target("pre$2.jpg") == "pre\\g<2>.jpg"


def test_extract_domain():
    assert sieve._extract_domain_from_regex(r"^(media\.admagazine\.ru/") == "media.admagazine.ru"
    assert sieve._extract_domain_from_regex(r"(^flickr\.com/.+)") == "flickr.com"
    assert sieve._extract_domain_from_regex(None) is None


# --- apply() with the real sieve file ------------------------------------

def test_wordpress_strip():
    assert sieve.apply("https://example.com/images/photo-300x200.jpg") == \
        "https://example.com/images/photo.jpg"


def test_fail_open_no_rules():
    # Non-image URL: unchanged, no exception
    assert sieve.apply("https://example.com/page") == "https://example.com/page"


def test_apply_empty():
    assert sieve.apply("") == ""


def test_loaded_rules_present():
    # The shipped sieve file must load > 0 rules (file present in resources/)
    assert sieve.loaded_count() > 0


def test_123rf_rule():
    # Known regex rule: us.123rf.com/450wm/... → previews.123rf.com/images/...
    out = sieve.apply("https://us.123rf.com/450wm/a/b/c.jpg", "https://www.123rf.com/photo/1")
    assert "123rf" in out
    assert "previews" in out


def test_2gis_rule():
    # i2.photo.2gis.ru/..._800x600.jpg → same without size suffix
    out = sieve.apply("https://i2.photo.2gis.ru/images/x_800x600.jpg", "https://2gis.ru/map")
    assert out.endswith(".jpg")
    assert "_800x600" not in out


# --- INT-3: link->url->res chain (get_link_rule / apply_link_url_transform /
#          extract_res_urls) — synthetic rules, no network ------------------

def _rule(link, url, res):
    import re as _re
    rule = {"link": link, "url": url, "res": res}
    try:
        rule["_link_re"] = _re.compile(link, _re.IGNORECASE)
    except _re.error:
        pass
    return rule


class _FakeSieve:
    """Minimal stand-in using the real methods via the real _Sieve class."""

    def __init__(self, rules):
        import sieve as _sieve
        inst = _sieve._Sieve(path="__none__")
        inst._link_rules = rules
        self._inst = inst

    def get_link_rule(self, url):
        return self._inst.get_link_rule(url)

    def apply_link_url_transform(self, rule, match, page_url=""):
        return self._inst.apply_link_url_transform(rule, match, page_url)

    def extract_res_urls(self, rule, html, **kw):
        return self._inst.extract_res_urls(rule, html, **kw)


def test_get_link_rule_matches_stripped_and_full():
    fs = _FakeSieve([_rule(r"^imx\.to/([A-Za-z0-9]+)", "https://imx.to/$1", r"img src" )])
    # matches scheme-stripped variant
    found = fs.get_link_rule("https://imx.to/abc123")
    assert found is not None
    rule, match = found
    assert match.group(1) == "abc123"
    # non-matching host → None
    assert fs.get_link_rule("https://other.com/x") is None
    assert fs.get_link_rule("") is None


def test_apply_link_url_transform_substitutes_and_prefixes():
    rule = _rule(r"^ag\.ru/screenshots/(\w+/\d+)", "http://www.ag.ru/screenshots/$1", "")
    fs = _FakeSieve([rule])
    found = fs.get_link_rule("https://ag.ru/screenshots/game/42")
    rule, match = found
    out = fs.apply_link_url_transform(rule, match, "https://ag.ru/x")
    assert out == ("http://www.ag.ru/screenshots/game/42", None)


def test_apply_link_url_transform_descending_groups():
    fs = _FakeSieve([])
    # $10 is a REAL group here — descending replacement must apply $10 before
    # $1, otherwise '$10' becomes 'aaa0'.
    pattern = (r"^h/([a-z])/([a-z])/([a-z])/([a-z])/([a-z])/"
               r"([a-z])/([a-z])/([a-z])/([a-z])/([a-z]+)$")
    rule = _rule(pattern, "https://h/$10/$1", "")
    m = __import__("re").search(pattern, "h/a/b/c/d/e/f/g/h/i/jk")
    out = fs.apply_link_url_transform(rule, m)
    assert out == ("https://h/jk/a", None)


def test_apply_link_url_transform_post_data():
    # Content after " :" is POSTed form data (source semantics: r"\s+:(.+)$").
    rule = _rule(r"^imx\.to/(\w+)", "https://imx.to/full :imgContinue=$1", "")
    fs = _FakeSieve([rule])
    found = fs.get_link_rule("https://imx.to/abc")
    out = fs.apply_link_url_transform(*found)
    assert out == ("https://imx.to/full", "imgContinue=abc")


def test_apply_link_url_transform_js_and_data_fail_open():
    js_rule = _rule(r"^x\.com/(\w+)", ":return 'https://x/'+$[1]", "")
    found = _FakeSieve([js_rule]).get_link_rule("https://x.com/abc")
    assert found is not None
    assert _FakeSieve([]).apply_link_url_transform(*found) is None
    data_rule = _rule(r"^y\.com/(\w+)", "data:,$&", "")
    found = _FakeSieve([data_rule]).get_link_rule("https://y.com/abc")
    assert found is not None
    assert _FakeSieve([]).apply_link_url_transform(*found) is None


def test_extract_res_urls_regex_group1():
    fs = _FakeSieve([])
    rule = _rule("^h", "https://h/x", r"<td[^>]*style=\"background:url\(([^)]+)\)")
    html = '<td style="background:url(https://cdn.example.com/full.jpg)">x</td>'
    assert fs.extract_res_urls(rule, html) == ["https://cdn.example.com/full.jpg"]


def test_extract_res_urls_js_res_skipped_then_img_fallback():
    fs = _FakeSieve([])
    rule = _rule("^h", "https://h/x", ":return $._.match(...)")
    html = '<html><body><img src="//cdn.example.com/a.webp"><img src="https://c.example.com/b.png"></body></html>'
    urls = fs.extract_res_urls(rule, html, page_url="https://h/viewer")
    assert "https://cdn.example.com/a.webp" in urls
    assert "https://c.example.com/b.png" in urls


def test_extract_res_urls_empty_html():
    fs = _FakeSieve([])
    assert fs.extract_res_urls({}, "") == []
    assert fs.extract_res_urls({}, "<p>no images</p>") == []
