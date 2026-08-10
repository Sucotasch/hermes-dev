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
