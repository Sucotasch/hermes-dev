#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for shared URL-hygiene helpers and Retry-After parsing."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _common import normalize_url, base_domain
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
