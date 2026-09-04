#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sitemap URL seeding for Level-2 expansion (Crawl4AI-inspired, minimal port).

Idea taken from Crawl4AI's AsyncUrlSeeder (docs.crawl4ai.com/core/url-seeding):
before walking hyperlinks page-by-page, ask the site itself for its URL list.

  domain -> robots.txt (Sitemap: lines) -> sitemap / sitemap index
         -> URL list -> junk filter -> BM25 scoring against the query

Design (repo invariants):
  * ZERO network on import; everything is lazy and bounded:
      - sitemap index fan-out capped (<= _MAX_SITEMAPS sub-sitemaps)
      - total URLs capped (<= _MAX_URLS) to bound memory/time
      - per-request timeout, per-domain budget for the whole run
  * fail-open: any error returns [] (expansion falls back to hyperlinks)
  * policy-free: no topic/visual/coverage keyword branching — the only
    external signal is the query text fed to BM25 (same mechanism the
    evidence pipeline already uses via evidence_rank).
  * dependency-free: stdlib urllib + the repo's own junk_filter/evidence_rank.

The single entry point is ``seed_urls_for_query(query, domains, ...)``,
called by the Hermes wrapper's Level-2 path. It never raises.
"""

import re
from urllib.parse import urlparse

# ── Bounds ──────────────────────────────────────────────────────────────────

_REQUEST_TIMEOUT = 5.0      # per HTTP request (robots.txt, each sitemap)
_MAX_SITEMAPS = 4            # sub-sitemaps actually fetched per domain
_MAX_SITEMAP_INDEX = 12      # <loc> entries considered from a sitemap index
_MAX_URLS = 400              # URLs kept per domain after collection
_MAX_URLS_OUT = 40           # URLs returned to the caller after BM25 ranking
_DOMAIN_TIME_BUDGET = 6.0    # seconds per domain (whole seeding run)
_MAX_DOMAINS = 4             # domains seeded per query (cost control)
_MAX_RAW_BYTES = 2 * 1024 * 1024   # download cap per sitemap file
_MAX_DECOMPRESSED_BYTES = 6 * 1024 * 1024  # gzip bomb guard

_LOC_RE = re.compile(r"<loc>\s*([^<]+?)\s*</loc>", re.I)
_SITEMAP_HINT_RE = re.compile(r"^https?://[^\s]+$", re.I)

# File suffixes that are never fetchable "pages" for our pipeline.
_NON_PAGE_SUFFIXES = (
    ".css", ".js", ".json", ".xml", ".txt", ".ico", ".woff", ".woff2",
    ".ttf", ".eot", ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".avif", ".mp4", ".webm", ".mp3", ".zip", ".rar", ".7z", ".gz",
)


# ── HTTP (plain urllib, no session churn) ──────────────────────────────────

def _http_get(url, timeout=_REQUEST_TIMEOUT):
    """GET text with a timeout and a byte cap. None on any failure (fail-open).

    Accepts plain XML, text (robots.txt), and gzip-compressed sitemaps
    (sitemap.xml.gz — news sites often ship these). The gzip path decompresses
    with a hard output cap (decompression-bomb guard). No content-type sniff:
    robots.txt has no <loc>/<urlset> markers, and servers mistype content-types
    routinely — callers check structure themselves.
    """
    try:
        import gzip
        import urllib.request
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Hermes-research"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(_MAX_RAW_BYTES + 1)
        if not body or len(body) > _MAX_RAW_BYTES or len(body) < 8:
            return None
        # Detect gzip: magic bytes (1f 8b) — covers .gz URLs and servers that
        # reply with Content-Encoding: gzip over plain URLs.
        if body[:2] == b"\x1f\x8b":
            with gzip.GzipFile(fileobj=__import__("io").BytesIO(body)) as gz:
                data = gz.read(_MAX_DECOMPRESSED_BYTES + 1)
            if len(data) > _MAX_DECOMPRESSED_BYTES:
                return None
            body = data
        if len(body) < 40:
            return None
        return body.decode("utf-8", errors="replace")
    except Exception:
        return None


# ── Sitemap discovery ────────────────────────────────────────────────────────

def _sitemap_urls_from_robots(domain):
    """Sitemap URLs declared in robots.txt. [] when absent/unfetchable."""
    host = (domain or "").strip().lower()
    if not host:
        return []
    if "://" in host:
        host = urlparse(host).hostname or ""
    if not host:
        return []
    robots = _http_get(f"https://{host}/robots.txt")
    if robots is None:
        robots = _http_get(f"http://{host}/robots.txt")
    if not robots:
        return []
    urls = []
    for line in robots.splitlines():
        # Sitemap: https://example.com/sitemap.xml  (case-insensitive key)
        m = re.match(r"\s*sitemap\s*:\s*(\S+)", line, re.I)
        if m and _SITEMAP_HINT_RE.match(m.group(1)):
            urls.append(m.group(1))
    # Deduplicate preserving order
    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:_MAX_SITEMAP_INDEX]


def _default_sitemap_candidates(domain):
    """Conventional sitemap locations used when robots.txt declares none."""
    host = (domain or "").strip().lower()
    if "://" in host:
        host = urlparse(host).hostname or ""
    if not host:
        return []
    return [f"https://{host}/sitemap.xml", f"https://{host}/sitemap_index.xml"]


def _collect_sitemap_locs(url, depth, fetched_count, out_urls, deadline):
    """Recursively fetch a sitemap / sitemap index. Bounded, fail-open."""
    import time as _time
    if depth > 1 or fetched_count[0] >= _MAX_SITEMAPS:
        return
    if _time.monotonic() > deadline:
        return
    fetched_count[0] += 1
    body = _http_get(url)
    if not body:
        return
    locs = _LOC_RE.findall(body)
    if not locs:
        return
    if "<sitemapindex" in body.lower():
        # Sitemap of sitemaps — follow each child (bounded).
        child_count = 0
        for child in locs:
            if child_count >= _MAX_SITEMAPS or _time.monotonic() > deadline:
                break
            if not _SITEMAP_HINT_RE.match(child):
                continue
            before = len(out_urls)
            _collect_sitemap_locs(child, depth + 1, fetched_count, out_urls, deadline)
            child_count += 1
            if len(out_urls) >= _MAX_URLS:
                return
        return
    # Regular sitemap: <loc> entries are page URLs.
    for loc in locs:
        if len(out_urls) >= _MAX_URLS:
            return
        loc = loc.strip()
        if loc.startswith("http") and loc not in out_urls:
            out_urls.append(loc)


def collect_domain_urls(domain, time_budget=_DOMAIN_TIME_BUDGET):
    """All page URLs from a domain's sitemaps (bounded). [] on failure."""
    import time as _time
    deadline = _time.monotonic() + time_budget
    out_urls = []
    fetched_count = [0]

    sitemaps = _sitemap_urls_from_robots(domain)
    if not sitemaps:
        sitemaps = _default_sitemap_candidates(domain)

    for sm in sitemaps:
        if _time.monotonic() > deadline or len(out_urls) >= _MAX_URLS:
            break
        _collect_sitemap_locs(sm, 0, fetched_count, out_urls, deadline)

    # Keep only plausible page URLs (fail-open: a bad filter only shrinks the
    # candidate pool, it can never block the hyperlink fallback).
    filtered = []
    try:
        from junk_filter import should_skip_crawl_url
        for u in out_urls:
            if not u.lower().endswith(_NON_PAGE_SUFFIXES) and not should_skip_crawl_url(u):
                filtered.append(u)
    except Exception:
        filtered = [u for u in out_urls if not u.lower().endswith(_NON_PAGE_SUFFIXES)]
    return filtered[:_MAX_URLS]


# ── BM25 ranking (reuses evidence_rank — same tokenization as the pipeline) ─

def _url_to_text(url):
    """URL -> space-separated words (scheme/host/path/query all split).

    evidence_rank's tokenizer deliberately KEEPS ``./:#-`` inside tokens
    (URL-like tokens stay intact for document text), which would make a
    whole URL one giant token. For URL ranking we need the opposite —
    Crawl4AI's seeder scores on domain parts / path segments — so split
    on every non-alphanumeric character first.
    """
    try:
        return re.sub(r"[\W_]+", " ", str(url or ""))
    except Exception:
        return str(url or "")


def _rank_urls_bm25(query, urls, top_n=_MAX_URLS_OUT):
    """Rank sitemap URLs against the query. BM25 over URL words.

    Corpus = URL strings split into words (path segments + filename tokens);
    the same BM25 implementation the evidence pipeline uses. URL ranking is
    a well-known Crawl4AI seeder feature (their URL-based scoring works on
    domain parts / path segments / query params — same spirit, our code).
    """
    if not query or not urls:
        return []
    try:
        from evidence_rank import rank_chunks_bm25
        chunks = [
            {"chunk_id": i, "text": _url_to_text(u), "tokens": 0}
            for i, u in enumerate(urls)
        ]
        ranked = rank_chunks_bm25(query, chunks, top_k=min(top_n, len(urls)))
        return [urls[int(c.get("chunk_id", 0))] for c in ranked]
    except Exception:
        # Fail-open: substring fallback (no dependency on evidence_rank).
        terms = [t.lower() for t in re.findall(r"\b\w+\b", query) if len(t) > 2]
        if not terms:
            return urls[:top_n]
        scored = []
        for u in urls:
            low = u.lower()
            score = sum(1 for t in terms if t in low)
            if score:
                scored.append((score, u))
        scored.sort(key=lambda x: -x[0])
        return [u for _, u in scored[:top_n]] or urls[:top_n]


# ── Public API ──────────────────────────────────────────────────────────────

def seed_urls_for_query(query, source_urls, max_urls=_MAX_URLS_OUT, max_domains=_MAX_DOMAINS):
    """Level-2 sitemap seeding: top sitemap URLs relevant to the query.

    Domains are taken from the Level-1 alive pages (source_urls). For each
    domain (bounded by max_domains, most-signal domains first) we fetch its
    sitemap, filter junk, and BM25-rank URLs against the query. Returns a
    deduplicated list of (url, domain) tuples, best first. Never raises; []
    means "no seeding — caller falls back to hyperlinks".
    """
    try:
        import time as _time

        if not query or not source_urls:
            return []

        # Distinct domains from alive Level-1 pages, best pages first.
        # www. is stripped so www.example.com and example.com count as one
        # domain (their sitemaps are almost always the same).
        domains = []
        seen_domain = set()
        for u in source_urls:
            if not isinstance(u, str):
                continue
            try:
                d = (urlparse(u).hostname or "").lower()
            except Exception:
                continue
            if d.startswith("www."):
                d = d[4:]
            if d and d not in seen_domain:
                seen_domain.add(d)
                domains.append(d)
        domains = domains[:max_domains]
        if not domains:
            return []

        # Remaining overall budget is split across domains: each domain gets
        # its own deadline so one slow site cannot eat the whole seeding run.
        out = []
        seen_url = set()
        overall_deadline = _time.monotonic() + max(6.0, 4.0 * max_domains)
        for d in domains:
            remaining = overall_deadline - _time.monotonic()
            if remaining <= 1.0:
                break
            urls = collect_domain_urls(d, time_budget=min(_DOMAIN_TIME_BUDGET, remaining))
            if not urls:
                continue
            ranked = _rank_urls_bm25(query, urls, top_n=max_urls)
            for u in ranked:
                if u not in seen_url:
                    seen_url.add(u)
                    out.append((u, d))
            if len(out) >= max_urls * 2:
                break
        # Final global BM25 across collected candidates (cross-domain balance).
        if out:
            flat = [u for u, _ in out]
            ranked = _rank_urls_bm25(query, flat, top_n=max_urls)
            out = [(u, dict(out).get(u, "")) for u in ranked]
        return out[:max_urls]
    except Exception:
        return []


# Simple CLI for manual checks: python sitemap_seeding.py example.com "query"
if __name__ == "__main__":
    import sys as _sys
    logging_domain = _sys.argv[1] if len(_sys.argv) > 1 else "docs.python.org"
    query = _sys.argv[2] if len(_sys.argv) > 2 else "async context managers"
    urls = collect_domain_urls(logging_domain)
    print(f"collected {len(urls)} urls from {logging_domain}")
    for u in _rank_urls_bm25(query, urls, top_n=10):
        print(" ", u)
