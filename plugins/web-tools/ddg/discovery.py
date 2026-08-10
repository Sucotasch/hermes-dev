#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sieve link->url->res fullsize discovery (INT-3).

For visual queries, gallery thumbnails often sit inside <a href> links whose
target is a *viewer page* (imx.to, ag.ru, …) instead of a direct image. When
the sieve has a `link` rule for that host we can resolve the fullsize original:

    link (match href) → url template (fetch URL, optional POST data) →
    fetch page → res regex(es) (or <img src> fallback) → fullsize URL(s)

Design (per Audit.md §14 INT-3):
  * visual queries only (caller enforces)
  * zero network when no `link` rule matches (pure regex check first)
  * bounded: per-run time budget, <=3 concurrent fetches, per-URL timeout
  * fail-open: any error → keep the thumbnail, never raise
"""

import time
from concurrent.futures import ThreadPoolExecutor

_FETCH_TIMEOUT = 6.0
_CONCURRENCY = 3
_BUDGET_DEFAULT = 12.0
_MAX_LINKS = 20


def _fetch_page(url, post_data=None, timeout=_FETCH_TIMEOUT):
    """Single-shot GET/POST via the shared curl_cffi session (direct).

    Returns ('html', text) or ('binary', final_url) or None on any failure.
    One attempt only — discovery is a best-effort upgrade, not a retry loop.
    """
    try:
        from ddg_search import _get_session
        session = _get_session()
        if session is None:
            return None
        headers = {"Accept": "text/html"}
        if post_data:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            resp = session.post(url, data=post_data, headers=headers,
                                timeout=timeout, allow_redirects=True)
        else:
            resp = session.get(url, headers=headers,
                               timeout=timeout, allow_redirects=True)
        if resp.status_code >= 400:
            return None
        ct = (resp.headers.get("content-type", "") or "").lower()
        if ct.startswith(("image/", "video/", "audio/")):
            return ("binary", str(resp.url))
        if "text/html" in ct or "<html" in (resp.text or "")[:2000].lower():
            return ("html", resp.text)
        return None
    except Exception:
        return None


def discover_fullsize(link_url, page_url="", fetch=None):
    """Resolve one thumbnail-transition link to fullsize URL(s).

    Returns a list of fullsize URLs (possibly several res matches), or [].
    Fail-open: no link rule / JS-only rule / fetch failure → [].
    """
    if not link_url:
        return []
    try:
        from sieve import _get_sieve
        sieve = _get_sieve()
        found = sieve.get_link_rule(link_url)
        if not found:
            return []
        rule, match = found
        groups = [match.group(0)]
        groups += [match.group(i) for i in range(1, match.re.groups + 1)]
        transformed = sieve.apply_link_url_transform(rule, match, page_url)
        if not transformed:
            return []  # no usable static url transform → nothing to fetch
        fetch_url, post_data = transformed
        fetch = fetch or _fetch_page
        result = fetch(fetch_url, post_data)
        if not result:
            return []
        kind, payload = result
        if kind == "binary":
            return [payload] if payload.startswith(("http://", "https://")) else []
        return sieve.extract_res_urls(rule, payload, page_url=page_url,
                                      groups=groups, href=link_url)
    except Exception:
        return []


def discover_thumbnails(pairs, page_url="", budget=_BUDGET_DEFAULT,
                        max_links=_MAX_LINKS, fetch=None, log=None):
    """Resolve (thumbnail_url, href) pairs to fullsize URLs, time-budgeted.

    Returns {thumbnail_url: [fullsize_urls, ...]}. At most `_CONCURRENCY`
    fetches in flight; the per-run `budget` (seconds) bounds total wall time.
    No rule match is free (pure regex); remaining links simply fall back to
    their thumbnails — nothing is lost, just not upgraded. `fetch` is
    injectable for tests (defaults to the curl_cffi single-shot fetch).
    """
    # WP-1: drop legal/account/noise viewer links (login, privacy, …) before
    # any network I/O — such pages hold no fullsize originals. Precision-first
    # whole-segment match; allowlisted hosts and real gallery paths pass.
    try:
        from junk_filter import should_skip_crawl_url
        pairs = [(t, h) for t, h in pairs
                 if h and not should_skip_crawl_url(h)]
    except Exception:
        pass
    pairs = list(pairs)[:max_links]
    if not pairs:
        return {}
    deadline = time.monotonic() + max(0.5, budget)
    out = {}
    href_cache = {}  # viewer href -> [urls] (avoid re-fetching the same page)
    executor = ThreadPoolExecutor(max_workers=_CONCURRENCY)
    pending = []  # (thumb, href, future)

    def _collect(t, h, fut):
        urls = []
        try:
            wait = max(0.3, deadline - time.monotonic())
            urls = fut.result(timeout=wait)
        except Exception:
            urls = []
        if urls:
            out[t] = urls
        href_cache[h] = urls or []

    try:
        for thumb, href in pairs:
            if time.monotonic() >= deadline:
                break
            if href in href_cache:
                if href_cache[href]:
                    out[thumb] = href_cache[href]
                continue
            if len(pending) >= _CONCURRENCY:
                t, h, fut = pending.pop(0)
                _collect(t, h, fut)
            pending.append((thumb, href, executor.submit(discover_fullsize, href, page_url, fetch)))
        for t, h, fut in pending:
            if time.monotonic() >= deadline:
                break
            _collect(t, h, fut)
    finally:
        executor.shutdown(wait=False)  # never block the pipeline on stragglers
    return out
