#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for shared URL-hygiene helpers and Retry-After parsing."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _common import (
    normalize_url, base_domain, registrable_domain, strip_tracking_params,
    consent_cookie_header,
)
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


def test_detect_blocked_cdn_cgi_needs_challenge():
    # Every Cloudflare-served page carries /cdn-cgi/ paths (rocket loader,
    # email protection) — alone it is NOT a block.
    normal = '<html><script src="/cdn-cgi/scripts/rocket-loader.min.js"></script><p>content</p></html>'
    assert ddg_search._detect_blocked(normal) is False
    challenged = '<html><div id="cf-chl-container"><script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script></div></html>'
    assert ddg_search._detect_blocked(challenged) is True


def test_registrable_domain_ipv4():
    assert registrable_domain("192.168.0.1") == "192.168.0.1"
    assert registrable_domain("10.0.0.255") == "10.0.0.255"


# --- HTML entity unescape in extracted image URLs ---------------------------

def test_extract_fullsize_images_unescapes_entities():
    html = '<meta property="og:image" content="https://i0.wp.com/x.jpg&amp;ssl=1">'
    urls = ddg_search.extract_fullsize_images(html, "https://example.com/")
    assert urls and "&amp;" not in urls[0]
    assert urls[0] == "https://i0.wp.com/x.jpg&ssl=1"


# --- _is_likely_content_page (WP-4 URL content signal) ---------------------

# --- strip_tracking_params (safe output for image URLs) -------------------

def test_strip_tracking_params():
    url = "https://cdn.example.com/a.jpg?utm_source=x&id=5&fbclid=abc"
    out = strip_tracking_params(url)
    assert "utm_source" not in out
    assert "fbclid" not in out
    # Non-tracking params keep their ORDER (signed URLs survive)
    assert out == "https://cdn.example.com/a.jpg?id=5"


def test_strip_tracking_params_keeps_signed_order():
    # Order of non-tracking params must NOT be re-sorted (signature risk)
    url = "https://cdn.x.com/f.jpg?token=aaa&expires=999&utm_campaign=y&sig=zzz"
    out = strip_tracking_params(url)
    assert out == "https://cdn.x.com/f.jpg?token=aaa&expires=999&sig=zzz"


def test_strip_tracking_params_noop():
    url = "https://cdn.x.com/f.jpg?w=800&h=600"
    assert strip_tracking_params(url) == url
    assert strip_tracking_params("https://cdn.x.com/f.jpg") == "https://cdn.x.com/f.jpg"


def test_strip_tracking_params_fragment_survives():
    # A fragment after a tracking param must NOT be dropped with the key
    assert strip_tracking_params(
        "https://x.com/a.jpg?utm_source=x#frag") == "https://x.com/a.jpg#frag"
    assert strip_tracking_params(
        "https://x.com/a.jpg?w=800&utm_source=x#frag") == "https://x.com/a.jpg?w=800#frag"


def test_extract_fullsize_images_dedup_utm_variants():
    # Two URLs that differ ONLY in tracking params collapse to one
    html = ('<meta property="og:image" content="https://cdn.x.com/a.jpg?utm_source=a">'
            '<meta property="og:image" content="https://cdn.x.com/a.jpg?utm_source=b&fbclid=1">')
    urls = ddg_search.extract_fullsize_images(html, "https://example.com/")
    assert len(urls) == 1
    # Output keeps the NON-tracking URL form (tracking stripped, order kept)
    assert urls[0] == "https://cdn.x.com/a.jpg"


def test_extract_fullsize_images_keeps_signed_params():
    html = ('<meta property="og:image" content="https://cdn.x.com/f.jpg?token=aaa&expires=999&sig=zzz">')
    urls = ddg_search.extract_fullsize_images(html, "https://example.com/")
    assert urls and urls[0] == "https://cdn.x.com/f.jpg?token=aaa&expires=999&sig=zzz"


# --- consent cookie pre-set (WP-5.2 port) --------------------------------

def test_consent_cookie_header_present():
    h = consent_cookie_header("https://gallery.com/view/123")
    assert h is not None
    assert "over18=1" in h
    assert "CookieConsent=true" in h
    assert "gdpr_accepted=true" in h


def test_consent_cookie_header_skips_proxies():
    assert consent_cookie_header("https://r.jina.ai/https://gallery.com/x") is None
    assert consent_cookie_header("https://duckduckgo.com/html/?q=x") is None
    assert consent_cookie_header("https://html.duckduckgo.com/html/?q=x") is None


def test_consent_cookie_header_fail_open():
    assert consent_cookie_header("") is None
    assert consent_cookie_header(None) is None


def test_is_likely_content_page():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "standalone"))
    import orchestrator
    assert orchestrator._is_likely_content_page("https://x.com/gallery/photo/123") is True
    assert orchestrator._is_likely_content_page("https://x.com/threads/12345678") is True
    assert orchestrator._is_likely_content_page("https://x.com/view/abc123") is True
    assert orchestrator._is_likely_content_page("https://x.com/2024/03/15/post") is True
    assert orchestrator._is_likely_content_page("https://x.com/about") is False
    assert orchestrator._is_likely_content_page("https://x.com/") is False
    assert orchestrator._is_likely_content_page("https://x.com/") is False
