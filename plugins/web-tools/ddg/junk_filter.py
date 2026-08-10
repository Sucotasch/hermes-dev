#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""P2-lite: compact, precision-first URL classifier for ads/trackers/junk.

Ported (unchanged semantics) from Temp/web-media-parser/src/parser/junk_filter.py
— a proven, tested module (30+ parametrized tests in the source repo).

Design principles:
  * Pure Python, no I/O at match time, compiled once at import.
  * Precision-first: host matches are exact suffix matches (dot-boundary),
    path matches are token-based. Weak signals (banner pixel sizes,
    third-party) never fire alone — only combined with a strong signal.
  * Safety valve: a per-machine allowlist file (domains, one per line) —
    anything matching it passes regardless of rules.
  * Fail-open: on any error the classifier returns False (never blocks
    content). When the feature is disabled, the code path is identical to
    before.
"""

import os
import sys
import re
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

ALLOWLIST_FILENAME = "junk_allowlist.txt"

# --- Data -----------------------------------------------------------------

# Known ad / tracker / analytics host suffixes (registrable-domain level).
# Matched as exact suffixes with a dot boundary, so "adserver.com" hits but
# "myadserver.example-gallery.com" (a legit user host) does not.
AD_HOST_SUFFIXES = (
    "doubleclick.net", "googlesyndication.com", "googleadservices.com",
    "googletagmanager.com", "google-analytics.com", "googletagservices.com",
    "adnxs.com", "appnexus.com", "taboola.com", "outbrain.com",
    "criteo.com", "criteo.net", "rubiconproject.com", "pubmatic.com",
    "openx.net", "moatads.com", "moat.com", "liadm.com", "serving-sys.com",
    "scorecardresearch.com", "quantserve.com", "casalemedia.com",
    "adsafeprotected.com", "advertising.com", "adtechus.com",
    "adform.net", "adform.com", "adservice.google.com", "2mdn.net",
    "everesttech.net", "media.net", "contextweb.com", "mathtag.com",
    "krxd.net", "sovrn.com", "yieldmo.com", "yieldlab.net", "smartadserver.com",
    "cpx.to", "clickbooth.com", "clickbank.net", "revcontent.com",
    "mgid.com", "teads.tv", "teads.com", "sekindo.com", "tidaltv.com",
    "spotxchange.com", "springserve.com", "freewheel.tv", "gfpkrw.net",
    "ttwnz.com", "exelator.com", "adglare.net", "adskeeper.com",
    "adsterra.com", "propellerads.com", "popads.net", "onclickads.net",
    "revenuehits.com", "juicyads.com", "exoclick.com", "ero-advertising.com",
    "trafficfactory.biz", "trafficjunky.net", "exosrv.com", "badoink.com",
)

# Path tokens (token-based, same semantics as _matches_ad_keyword). These are
# ad/creative markers that a real gallery almost never uses in media URLs.
AD_PATH_TOKENS = (
    "advert", "adserver", "adservice", "adsense", "adroll", "affiliate",
    "banner", "creative", "impression", "beacon", "pixel", "tracking",
    "tracker", "analytics", "sponsor", "promo", "popup", "popunder",
    "doubleclick", "googlesyndication", "taboola", "outbrain", "criteo",
    "mgid", "revcontent", "juicyads", "exoclick", "propellerads", "popads",
)

# Numeric banner sizes in a filename/path: "banner_300x250.jpg", "728x90/…".
# WEAK signal — never fires alone.
_AD_SIZE_RE = re.compile(r"[-_/](\d{2,4}x\d{2,4})[-_/.]", re.I)

# --- Allowlist -------------------------------------------------------------

_allowlist_cache = None
_allowlist_mtime = None


def _allowlist_paths():
    """Candidate locations for the allowlist file, in priority order."""
    paths = []
    if getattr(sys, "frozen", False):
        paths.append(os.path.join(os.path.dirname(sys.executable), ALLOWLIST_FILENAME))
        paths.append(os.path.join(os.path.dirname(sys.executable), "resources", ALLOWLIST_FILENAME))
    here = os.path.dirname(os.path.abspath(__file__))
    paths.append(os.path.join(here, ALLOWLIST_FILENAME))
    paths.append(os.path.join(here, "resources", ALLOWLIST_FILENAME))
    paths.append(ALLOWLIST_FILENAME)
    return paths


def load_allowlist() -> set:
    """Domains that must never be classified as junk. Cached by mtime."""
    global _allowlist_cache, _allowlist_mtime
    path = next((p for p in _allowlist_paths() if os.path.exists(p)), None)
    if path is None:
        return set()
    try:
        mtime = os.path.getmtime(path)
        if _allowlist_cache is not None and _allowlist_mtime == mtime:
            return _allowlist_cache
        with open(path, "r", encoding="utf-8") as f:
            domains = {
                line.strip().lower().lstrip(".") for line in f
                if line.strip() and not line.lstrip().startswith("#")
            }
        _allowlist_cache, _allowlist_mtime = domains, mtime
        return domains
    except Exception as e:
        logger.debug(f"junk allowlist load failed: {e}")
        return set()


def _in_allowlist(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return False
    if not host:
        return False
    allow = load_allowlist()
    if not allow:
        return False
    if host in allow:
        return True
    return any(host.endswith("." + a) for a in allow)


# --- Matching --------------------------------------------------------------

def _host_matches_ad(url: str) -> bool:
    """Exact (dot-boundary) suffix match against AD_HOST_SUFFIXES."""
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return False
    if not host:
        return False
    for suffix in AD_HOST_SUFFIXES:
        if host == suffix or host.endswith("." + suffix):
            return True
    return False


def _path_has_ad_token(url: str) -> bool:
    try:
        combined = f"{urlparse(url).netloc}/{urlparse(url).path}".lower()
    except Exception:
        combined = (url or "").lower()
    tokens = [t for t in re.split(r"[^\w]+", combined) if t]
    for tok in tokens:
        if not tok:
            continue
        for kw in AD_PATH_TOKENS:
            k = kw.replace("-", "")
            t = tok.replace("-", "")
            if t == k:
                return True
            if len(t) == len(k) + 1 and t.startswith(k) and t.endswith("s"):
                return True
    return False


def is_ad_url(url, page_url=None):
    """True when url is very likely an ad/tracker.

    Strong signals (host suffix, path token) decide alone. Weak signals
    (banner pixel size + third-party) only count together. Never blocks
    allowlisted hosts.
    """
    if not url or not isinstance(url, str):
        return False
    if _in_allowlist(url):
        return False
    if _host_matches_ad(url) or _path_has_ad_token(url):
        return True
    # Weak: numeric banner size in the path. Only fires when the request is
    # third-party (different registrable domain than the page) — a same-site
    # 300x250 thumbnail in a gallery must never be dropped.
    if _AD_SIZE_RE.search(url) and page_url:
        try:
            page_host = urlparse(page_url).netloc.lower()
            url_host = urlparse(url).netloc.lower()
            if page_host and url_host and _third_party(page_host, url_host):
                return True
        except Exception:
            pass
    return False


def _third_party(page_host: str, url_host: str) -> bool:
    """Approximate third-party check (different registrable domains)."""
    try:
        from _common import registrable_domain
        return registrable_domain(page_host) != registrable_domain(url_host)
    except Exception:
        def base(host):
            parts = host.split(".")
            return ".".join(parts[-2:]) if len(parts) > 1 else host
        return base(page_host) != base(url_host)


# --- Junk transitions (forum chrome etc.) -----------------------------------

# Regexes for crawl links that are universally, unambiguously useless.
# Principle: NEVER skip listing/hub pages (forum.php, index.php — the thread
# hub on many platforms) or content paths (album.php — user photo albums on
# vBulletin). Only action forms, account/admin URLs and post permalinks
# (viewfull — duplicate of the same thread) are skipped.
SKIP_CRAWL_REGEXES = (
    re.compile(r"search\.php", re.I),
    re.compile(r"newreply\.php", re.I),
    re.compile(r"newthread\.php", re.I),
    re.compile(r"sendmessage\.php", re.I),
    re.compile(r"editpost\.php", re.I),
    re.compile(r"do=newreply", re.I),
    # account / admin / user profiles & lists — dead ends, no media
    re.compile(r"member\.php", re.I),
    re.compile(r"memberlist", re.I),
    re.compile(r"/members/", re.I),
    re.compile(r"usercp\.php", re.I),
    re.compile(r"private\.php", re.I),
    re.compile(r"login\.php", re.I),
    re.compile(r"register\.php", re.I),
    re.compile(r"wp-admin", re.I),
    re.compile(r"wp-login", re.I),
    # post permalinks — same thread, re-crawled per post (duplicates)
    re.compile(r"viewfull", re.I),
)


def should_skip_junk_url(url: str) -> bool:
    """True when a crawl link is known junk (forum chrome / system pages)."""
    if not url or not isinstance(url, str):
        return False
    if _in_allowlist(url):
        return False
    try:
        full = url.lower()
    except Exception:
        return False
    if _host_matches_ad(url):
        return True
    for rx in SKIP_CRAWL_REGEXES:
        if rx.search(full):
            return True
    return False


# --- Segment-aware crawl-link classifier (ported from web-media-parser WP-1) --
#
# Path segments that almost never hold scrapeable content (legal, account,
# commerce, corporate, noise). Matched as FULL path segments only — never as
# free substrings, so "ad" can never kill "media" or "upload". This filter is
# used BEFORE fetching: legal/login/privacy pages contain neither the queried
# text content nor gallery images, so skipping them saves validation requests
# and keeps them out of the report.
DEFAULT_LINK_SKIP_SEGMENTS = frozenset({
    # account / commerce
    "login", "signin", "signup", "register", "logout", "account", "profile",
    "cart", "checkout", "payment", "subscribe", "billing", "password", "auth",
    "settings", "dashboard", "preferences", "admin",
    # legal / corporate
    "privacy", "privacy-policy", "terms", "tos", "legal", "copyright",
    "dmca", "cookies", "cookie-policy", "gdpr", "imprint", "impressum",
    "about", "about-us", "contact", "careers", "jobs", "press", "help",
    "support", "faq", "feedback", "sitemap", "robots.txt",
    # noise
    "advert", "advertising", "ads", "adserver", "sponsor", "promo",
    "newsletter", "unsubscribe", "widget", "embed", "share", "redirect",
    "go", "out", "external", "tracking", "pixel", "analytics",
    "wp-admin", "wp-login", "search",
    # NOTE: tag/tags/category/categories are deliberately NOT here — galleries
    # live under /tag/ and /category/ hubs on many platforms.
})

# Substrings only safe as a full path segment or query key — never free.
_SKIP_QUERY_KEYS = frozenset({"utm_source", "utm_medium", "fbclid", "gclid"})

# File junk that is never a page to deep-read (explicit, end-of-path only).
_FILE_JUNK_SUFFIXES = (
    ".css", ".js", ".map", ".xml", ".json", ".txt", ".ico",
    ".woff", ".woff2", ".ttf", ".eot", ".svg", ".png", ".jpg", ".jpeg",
    ".gif", ".webp", ".avif", ".mp4", ".webm", ".mp3",
)

# Host-level ad/tracker networks (matches _AD_HOST_SUFFIXES semantics).
_AD_HOST_NETWORKS = (
    "doubleclick.", "googlesyndication.", "googleadservices.", "adservice.",
    "adnxs.", "taboola.", "outbrain.", "criteo.", "moatads.", "scorecardresearch.",
)


def _path_segments(url: str):
    """Lowercased non-empty path segments of a URL."""
    try:
        path = urlparse(url).path.lower()
    except Exception:
        return []
    return [s for s in path.split("/") if s]


def should_skip_crawl_url(url: str, extra_stop_words=None) -> bool:
    """True when a URL should not be fetched/queued for content parsing.

    Precision-first: legal/account/noise paths are matched as whole path
    segments, file junk as end-of-path suffixes, ad networks at host level.
    `extra_stop_words` are added as segments (len>=3, stripped of '/').
    Fail-open: any error returns False (never blocks content).
    """
    if not url or not isinstance(url, str):
        return True
    if _in_allowlist(url):
        return False
    try:
        p = urlparse(url)
    except Exception:
        return False
    if not p.scheme or not p.netloc:
        return True
    try:
        path = (p.path or "").lower()
        full = url.lower()

        # Explicit file junk (end-of-path only — /media/uploads/1.jpg is a
        # media file, not a page; skip it from PAGE candidates but this module
        # also guards viewer-link discovery where we must never fetch binaries).
        if path.endswith(_FILE_JUNK_SUFFIXES):
            return True

        # Host-level ad networks (dot-boundary prefix is safe: "doubleclick.").
        if any(n in full for n in _AD_HOST_NETWORKS):
            return True
        if _host_matches_ad(url):
            return True

        # Segment match — the precise, false-positive-safe part.
        skip = set(DEFAULT_LINK_SKIP_SEGMENTS)
        if extra_stop_words:
            for w in extra_stop_words:
                w = (w or "").strip().lower().strip("/")
                if len(w) >= 3:
                    skip.add(w)
        segs = _path_segments(url)
        if any(s in skip for s in segs):
            return True

        # Tracking-only query keys: a URL whose ONLY query params are
        # utm/fbclid/gclid is a marketing redirect, not content.
        if p.query:
            try:
                keys = {k.split("=", 1)[0].lower() for k in p.query.split("&")}
            except Exception:
                keys = set()
            if keys and keys <= _SKIP_QUERY_KEYS:
                return True
    except Exception:
        return False
    return False
