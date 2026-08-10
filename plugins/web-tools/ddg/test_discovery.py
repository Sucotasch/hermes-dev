#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for INT-3: sieve link->url->res fullsize discovery.

discovery.discover_fullsize is tested with an injected fake fetch (no network);
discover_thumbnails with an injected fetch that respects budget bounds.
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import discovery
import ddg_search
import sieve


def _rule(link, url, res):
    import re as _re
    rule = {"link": link, "url": url, "res": res}
    rule["_link_re"] = _re.compile(link, _re.IGNORECASE)
    return rule


# --- extract_thumbnail_links ----------------------------------------------

def test_extract_thumbnail_links_pairs():
    html = ('<a href="https://imx.to/abc123"><img src="https://cdn.example.com/t1.jpg"></a>'
            '<a href="https://img.example.com/direct.jpg"><img src="https://cdn.example.com/t2.jpg"></a>'
            '<a href="/relative/viewer"><img src="/img/t3.jpg"></a>')
    pairs = ddg_search.extract_thumbnail_links(html, "https://site.example.com/gallery")
    # viewer-page hrefs kept, direct-image href dropped, relative resolved
    urls = [h for _, h in pairs]
    assert "https://imx.to/abc123" in urls
    assert "https://img.example.com/direct.jpg" not in urls
    assert "https://site.example.com/relative/viewer" in urls
    assert pairs[0][0] == "https://cdn.example.com/t1.jpg"


def test_extract_thumbnail_links_empty():
    assert ddg_search.extract_thumbnail_links("") == []
    assert ddg_search.extract_thumbnail_links("<p>no links</p>") == []


# --- discover_fullsize with injected fetch ---------------------------------

def test_discover_no_rule_no_network():
    # No sieve link rule matches → [] and the fetch is never called.
    called = []
    def fake_fetch(url, post_data=None):
        called.append(url)
        return ("html", "<html></html>")
    out = discovery.discover_fullsize("https://nonexistent-host-xyz.com/abc", page_url="https://x/", fetch=fake_fetch)
    assert out == []
    assert called == []


def test_discover_binary_response_uses_final_url():
    # Inject a fake link rule so discovery proceeds, then a binary response.
    inst = sieve._Sieve(path="__none__")
    inst._link_rules = [_rule(r"^img\.host/(\w+)", "https://img.host/full/$1", "")]
    import sieve as _sieve
    _sieve._SINGLETON = inst  # let discovery see the fake rules
    try:
        def fake_fetch(url, post_data=None):
            assert url == "https://img.host/full/abc123"
            assert post_data is None
            return ("binary", "https://img.host/full/abc123.jpg")
        out = discovery.discover_fullsize("https://img.host/abc123", fetch=fake_fetch)
        assert out == ["https://img.host/full/abc123.jpg"]
    finally:
        # restore real singleton for other tests
        import importlib
        _sieve._SINGLETON = None


def test_discover_html_res_extraction():
    inst = sieve._Sieve(path="__none__")
    inst._link_rules = [_rule(r"^ag\.ru/screenshots/(\w+/\d+)",
                              "http://www.ag.ru/screenshots/$1",
                              r"<td[^>]*style=\"background:url\(([^)]+)\)")]
    import sieve as _sieve
    _sieve._SINGLETON = inst
    try:
        html = '<td style="background:url(https://cdn.ag.ru/full.jpg)">'
        out = discovery.discover_fullsize(
            "https://ag.ru/screenshots/game/42", page_url="https://ag.ru/x",
            fetch=lambda url, post_data=None: ("html", html))
        assert out == ["https://cdn.ag.ru/full.jpg"]
    finally:
        _sieve._SINGLETON = None


def test_discover_fetch_failure_fail_open():
    inst = sieve._Sieve(path="__none__")
    inst._link_rules = [_rule(r"^h/(\w+)", "https://h/$1", "")]
    import sieve as _sieve
    _sieve._SINGLETON = inst
    try:
        out = discovery.discover_fullsize("https://h/abc", fetch=lambda url, post_data=None: None)
        assert out == []
    finally:
        _sieve._SINGLETON = None


# --- discover_thumbnails budget -------------------------------------------

def test_discover_thumbnails_budget_and_mapping():
    inst = sieve._Sieve(path="__none__")
    inst._link_rules = [_rule(r"^h/(\w+)", "https://h/full/$1", "")]
    import sieve as _sieve
    _sieve._SINGLETON = inst
    try:
        def fake_fetch(url, post_data=None):
            return ("binary", url + ".jpg")
        pairs = [("https://cdn.example.com/t%d.jpg" % i, "https://h/id%d" % i) for i in range(5)]
        out = discovery.discover_thumbnails(pairs, page_url="https://page/",
                                            budget=10.0, max_links=20,
                                            fetch=fake_fetch)
        # every pair whose href matched a rule resolves
        assert len(out) == 5
        assert out["https://cdn.example.com/t0.jpg"] == ["https://h/full/id0.jpg"]
    finally:
        _sieve._SINGLETON = None
