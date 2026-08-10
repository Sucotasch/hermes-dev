#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the ported junk filter (plugins/web-tools/ddg/junk_filter.py).

Mirrors the proven test set from Temp/web-media-parser/tests/test_junk_filter.py.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from junk_filter import is_ad_url, should_skip_junk_url, should_skip_crawl_url


# --- ad URL classification -----------------------------------------------

@pytest.mark.parametrize("url,page", [
    ("https://cdn.doubleclick.net/x/photo.jpg", "https://gallery.com/"),
    ("https://ads.googleadservices.com/i.jpg", "https://gallery.com/"),
    ("https://foo.taboola.com/img/1.jpg", "https://gallery.com/"),
    ("https://example.com/adsense/creative/2.jpg", "https://example.com/"),
    ("https://example.com/banner_300x250.jpg", "https://gallery.com/"),
])
def test_is_ad_url_positives(url, page):
    assert is_ad_url(url, page) is True, url


@pytest.mark.parametrize("url,page", [
    # LEGIT image hosts must never be flagged
    ("https://i.imgur.com/abc123.jpg", "https://imgur.com/"),
    ("https://live.staticflickr.com/65535/1_b.jpg", "https://flickr.com/"),
    ("https://images.unsplash.com/photo-1", "https://unsplash.com/"),
    # host token NOT a real ad network (suffix match precision)
    ("https://myadserver.example-gallery.com/photo.jpg", "https://example-gallery.com/"),
    # compound token (banner123 != banner) — precision
    ("https://example.com/banner123/photo.jpg", "https://example.com/"),
    # same-domain banner size = weak alone, never fires
    ("https://gallery.com/i/300x250/thumb.jpg", "https://gallery.com/"),
])
def test_is_ad_url_negatives(url, page):
    assert is_ad_url(url, page) is False, url


def test_is_ad_url_weak_third_party_size():
    # cross-domain banner size (no other signal) — weak+third-party fires
    assert is_ad_url(
        "https://cdn.other.net/i/300x250/1.jpg", "https://gallery.com/") is True
    # same-domain size alone stays safe
    assert is_ad_url(
        "https://gallery.com/i/300x250/1.jpg", "https://gallery.com/") is False


def test_is_ad_url_allowlist_override(monkeypatch):
    import junk_filter
    monkeypatch.setattr(junk_filter, "load_allowlist", lambda: {"doubleclick.net"})
    assert is_ad_url(
        "https://cdn.doubleclick.net/x.jpg", "https://gallery.com/") is False


def test_is_ad_url_fail_open():
    assert is_ad_url(None) is False
    assert is_ad_url("") is False


# --- junk transitions -----------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://vipergirls.to/threads/newreply.php?do=newreply&p=264891380",
    "https://vipergirls.to/threads/search.php?search_type=1",
    "https://vipergirls.to/threads/newthread.php",
    "https://vipergirls.to/threads/usercp.php",
    "https://vipergirls.to/threads/members/424045-Pixel",
    "https://vipergirls.to/threads/16328688-x?p=264860330&viewfull=1",
    "https://site.com/wp-admin/index.php",
])
def test_should_skip_junk_url_positives(url):
    assert should_skip_junk_url(url) is True, url


@pytest.mark.parametrize("url", [
    # Hubs and content paths must NEVER be skipped
    "https://vipergirls.to/forum.php",
    "https://vipergirls.to/threads/16328688-Akiramai-x62",
    "https://vipergirls.to/album.php?albumid=123",
])
def test_should_skip_junk_url_negatives(url):
    assert should_skip_junk_url(url) is False, url


def test_allowlist_passes_junk_transitions(monkeypatch):
    import junk_filter
    monkeypatch.setattr(junk_filter, "load_allowlist", lambda: {"vipergirls.to"})
    assert should_skip_junk_url(
        "https://vipergirls.to/threads/search.php") is False


def test_should_skip_junk_fail_open():
    assert should_skip_junk_url(None) is False
    assert should_skip_junk_url("") is False


# --- should_skip_crawl_url (WP-1 segment-aware) ---------------------------

@pytest.mark.parametrize("url", [
    "https://example.com/login",
    "https://example.com/privacy/policy",
    "https://example.com/account/settings",
    "https://example.com/terms",
    "https://example.com/checkout",
    "https://example.com/sitemap.xml",
    "https://example.com/robots.txt",
    "https://example.com/wp-admin/",
    "https://example.com/?utm_source=x",
    "https://example.com/go?utm_source=x&utm_medium=y",
    "https://cdn.doubleclick.net/page",
    "https://example.com/style.css",
])
def test_should_skip_crawl_url_positives(url):
    assert should_skip_crawl_url(url) is True, url


@pytest.mark.parametrize("url", [
    # real content pages must never be skipped
    "https://example.com/gallery/photo/123",
    "https://example.com/media/uploads/album",
    "https://example.com/tag/brunette-14",
    "https://example.com/category/asian",
    # 'ad' substring inside 'media'/'upload' must NOT skip
    "https://cdn.example.com/media/uploads/photo",
    # segment 'account' only matches whole segment — 'myaccount' is content
    "https://example.com/myaccount/photo",
    # direct media files are skipped (not pages) — see file-junk positives
    # below; a bare directory without extension stays a candidate
    "https://example.com/downloads/setup",
])
def test_should_skip_crawl_url_negatives(url):
    assert should_skip_crawl_url(url) is False, url


def test_should_skip_crawl_url_extra_stop_words():
    assert should_skip_crawl_url(
        "https://example.com/zoneprivacy", ["zoneprivacy"]) is True
    assert should_skip_crawl_url(
        "https://example.com/gallery/x", ["zoneprivacy"]) is False
    # short stop words (<3 chars) are ignored (precision)
    assert should_skip_crawl_url(
        "https://example.com/ad", ["ad"]) is False


def test_should_skip_crawl_url_allowlist(monkeypatch):
    import junk_filter
    monkeypatch.setattr(junk_filter, "load_allowlist", lambda: {"example.com"})
    assert should_skip_crawl_url("https://example.com/login") is False


def test_should_skip_crawl_url_fail_open():
    assert should_skip_crawl_url("") is True
    assert should_skip_crawl_url(None) is True
    assert should_skip_crawl_url("https://example.com/gallery/123") is False
