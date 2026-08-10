#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shared URL-hygiene helpers (ported from Temp/web-media-parser/src/parser/utils.py).

* normalize_url — strip tracking params, sort query, drop fragment, collapse
  repeated adjacent path segments (anti urljoin-bloat), lowercase scheme/domain.
"""

from urllib.parse import urlparse, parse_qsl, urlencode

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


def base_domain(url):
    """Registrable-domain approximation: last two labels of the hostname."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return ""
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) > 2 else host
