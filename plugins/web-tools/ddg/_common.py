#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shared URL-hygiene helpers (ported from Temp/web-media-parser/src/parser/utils.py).

* normalize_url — strip tracking params, sort query, drop fragment, collapse
  repeated adjacent path segments (anti urljoin-bloat), lowercase scheme/domain.
"""

import re
from urllib.parse import urlparse, parse_qsl, urlencode

_IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")

# Tracking query params that never affect content identity — stripped during
# normalization so the same resource re-encountered with different tracking
# suffixes deduplicates correctly (avoids _1/_2 duplicates after Resume).
TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "fbclid", "gclid", "mc_cid", "mc_eid", "igshid",
})


def normalize_url(url):
    """Normalize a URL for stable identity/dedup.

    Removes fragments, sorts query parameters, drops tracking params,
    lowercases scheme/domain, collapses repeated adjacent path segments
    (threads/threads/threads/x → threads/x). Fail-open: returns the input
    unchanged on any error.
    """
    try:
        if not url:
            return ""
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()

        path = parsed.path
        if path.endswith("/") and len(path) > 1:
            path = path[:-1]

        # Collapse runs of 3+ identical adjacent path segments — a urljoin
        # bloat signature that defeats dedup (threads/members/threads/...).
        segments = path.split("/")
        collapsed = []
        i = 0
        n = len(segments)
        while i < n:
            seg = segments[i]
            if not seg:
                collapsed.append(seg)
                i += 1
                continue
            j = i + 1
            while j < n and segments[j] == seg:
                j += 1
            run = j - i
            if run >= 3:
                collapsed.append(seg)
            else:
                collapsed.extend(segments[i:j])
            i = j
        path = "/".join(collapsed)

        query = parsed.query
        if query:
            pairs = [
                (k, v) for k, v in parse_qsl(query, keep_blank_values=True)
                if k.lower() not in TRACKING_PARAMS
            ]
            query = urlencode(sorted(pairs))

        return parsed._replace(
            scheme=scheme, netloc=netloc, path=path,
            query=query, fragment="",
        ).geturl()
    except Exception:
        return url


# Multi-label public suffixes (second-level TLDs): for these the registrable
# domain is THREE labels (www.example.co.uk → example.co.uk), not two. Without
# this table `markhewittphotography.co.uk` collapsed to `co.uk` and the whole
# TLD got domain-quarantined after two blocked pages (observed in the 2026-08-10
# test run: "BLOCK DOMAIN: co.uk").
_MULTI_LABEL_SUFFIXES = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "net.uk", "me.uk", "ltd.uk", "plc.uk",
    "co.za", "org.za", "net.za", "gov.za", "ac.za",
    "com.au", "net.au", "org.au", "edu.au", "gov.au",
    "co.nz", "org.nz", "net.nz", "ac.nz", "govt.nz",
    "co.jp", "ne.jp", "or.jp", "ac.jp", "go.jp",
    "com.br", "net.br", "org.br", "gov.br", "edu.br",
    "com.mx", "org.mx", "net.mx", "gob.mx", "edu.mx",
    "com.ar", "org.ar", "net.ar", "gob.ar", "edu.ar",
    "com.tr", "org.tr", "net.tr", "gov.tr", "edu.tr",
    "co.in", "org.in", "net.in", "gov.in", "ac.in", "edu.in",
    "com.cn", "org.cn", "net.cn", "gov.cn", "edu.cn",
    "com.ru", "org.ru", "net.ru", "msk.ru", "spb.ru",
    "com.ua", "org.ua", "net.ua", "gov.ua", "edu.ua", "in.ua",
    "com.pl", "org.pl", "net.pl", "gov.pl", "edu.pl",
    "co.kr", "or.kr", "com.eg", "com.sg", "com.my", "com.hk", "com.tw",
    "co.il", "org.il", "com.il", "co.id", "com.vn", "com.co", "com.ec",
    "co.th", "com.pe", "com.ve", "com.uy", "co.cr", "com.bo", "com.py",
    "co.ke", "co.ug", "co.tz", "co.zw", "com.ng", "com.gh", "co.ao",
    "com.bd", "com.pk", "com.lk", "com.np",
})


def registrable_domain(hostname):
    """Registrable-domain approximation (public-suffix aware).

    www.example.co.uk → example.co.uk; example.co.uk stays itself.
    Fallback: last two labels. Fail-open: "" on any error.
    """
    try:
        host = (hostname or "").strip().lower().rstrip(".")
    except Exception:
        return ""
    if not host:
        return ""
    # IPv4 hosts are not domains: never fold 192.168.0.1 into "0.1".
    if _IPV4_RE.match(host):
        return host
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    last2 = ".".join(parts[-2:])
    if last2 in _MULTI_LABEL_SUFFIXES:
        if len(parts) == 3:
            return host                 # x.co.uk is already the registrable domain
        return ".".join(parts[-3:])    # www.x.co.uk / a.b.co.uk → x.co.uk
    return ".".join(parts[-2:])


def base_domain(url):
    """Registrable-domain approximation for a full URL."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return ""
    return registrable_domain(host)
