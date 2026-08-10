#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for shared URL-hygiene helpers and Retry-After parsing."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _common import normalize_url, base_domain, registrable_domain
import ddg_search


# --- normalize_url -------------------------------------------------------

def test_normalize_strips_tracking_params():
    url = "https://example.com/article?utm_source=x&id=5&fbclid=abc"
    out = normalize_url(url)
    assert "utm_source" not in out
    assert "fbclid" not in out
    assert "id=5" in out


def test_normalize_sorts_query():
    out = normalize_url("https://example.com/p?b=2&a=1")
    assert out == "https://example.com/p?a=1&b=2"


def test_normalize_drops_fragment():
    assert normalize_url("https://example.com/p#section") == "https://example.com/p"


def test_normalize_collapses_repeated_segments():
    out = normalize_url("https://example.com/threads/threads/threads/x")
    assert out == "https://example.com/threads/x"


def test_normalize_lowercases_host():
    assert normalize_url("HTTPS://EXAMPLE.COM/Page") == "https://example.com/Page"


def test_normalize_fail_open():
    assert normalize_url(None) == ""
    assert normalize_url("") == ""


# --- base_domain ---------------------------------------------------------

def test_base_domain():
    assert base_domain("https://sub.example.com/x") == "example.com"
    assert base_domain("https://example.com/x") == "example.com"
    assert base_domain("https://localhost:8080/x") == "localhost"
    assert base_domain("") == ""


# --- registrable_domain (public-suffix aware) ------------------------------

def test_registrable_domain_two_label_tlds():
    # Regression: naive last-two-labels turned markhewittphotography.co.uk into
    # "co.uk" and quarantined the whole TLD (observed: "BLOCK DOMAIN: co.uk").
    assert registrable_domain("markhewittphotography.co.uk") == "markhewittphotography.co.uk"
    assert registrable_domain("www.markhewittphotography.co.uk") == "markhewittphotography.co.uk"
    assert registrable_domain("the-greenhouse.co.za") == "the-greenhouse.co.za"
    assert registrable_domain("www.example.com.au") == "example.com.au"


def test_registrable_domain_regular():
    assert registrable_domain("sub.example.com") == "example.com"
    assert registrable_domain("xxgasm.com") == "xxgasm.com"
    assert registrable_domain("localhost") == "localhost"
    assert registrable_domain("") == ""
    assert registrable_domain(None) == ""


# --- _parse_retry_after --------------------------------------------------

def test_retry_after_seconds():
    assert ddg_search._parse_retry_after("5") == 5
    assert ddg_search._parse_retry_after("0") == 0


def test_retry_after_http_date():
    # A date far in the past → 0; far future → large number (> timeout → no retry)
    out = ddg_search._parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT")
    assert out is not None and out >= 0


def test_retry_after_invalid():
    assert ddg_search._parse_retry_after("") is None
    assert ddg_search._parse_retry_after("banana") is None
    assert ddg_search._parse_retry_after(None) is None


# --- _detect_blocked precision (regression: MediaWiki captcha config) -------

def test_detect_blocked_not_fooled_by_captcha_config():
    # Commons pages carry wgConfirmEditCaptchaNeededForGenericEdit:"hcaptcha"
    # in JS config plus a search form — HTTP 200 real pages, NOT block pages.
    html = ('<html><form action="/w/index.php?search=x"></form>'
            '<script>var cfg={"wgConfirmEditCaptchaNeededForGenericEdit":"hcaptcha"};</script>'
            "<p>Category content</p></html>")
    assert ddg_search._detect_blocked(html) is False


def test_detect_blocked_still_catches_real_challenges():
    assert ddg_search._detect_blocked(
        '<div class="hcaptcha-widget"><iframe src="https://newassets.hcaptcha.com/captcha"></iframe></div>')
    assert ddg_search._detect_blocked('<div class="g-recaptcha">verify</div>')
    assert ddg_search._detect_blocked('cf-chl-check checking your browser')


def test_detect_blocked_ignores_plain_404_text():
    # 'страница не найдена' (page not found) is a normal 404, not a block.
    assert ddg_search._detect_blocked("<html>\u0441\u0442\u0440\u0430\u043d\u0438\u0446\u0430 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430 404</html>") is False


# --- HTML entity unescape in extracted image URLs ---------------------------

def test_extract_fullsize_images_unescapes_entities():
    html = '<meta property="og:image" content="https://i0.wp.com/x.jpg&amp;ssl=1">'
    urls = ddg_search.extract_fullsize_images(html, "https://example.com/")
    assert urls and "&amp;" not in urls[0]
    assert urls[0] == "https://i0.wp.com/x.jpg&ssl=1"
