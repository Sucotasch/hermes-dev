#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shared URL-hygiene helpers (ported from Temp/web-media-parser/src/parser/utils.py).

* normalize_url — strip tracking params, sort query, drop fragment, collapse
  repeated adjacent path segments (anti urljoin-bloat), lowercase scheme/domain.
"""

import re
import time
from urllib.parse import urlparse, parse_qsl, urlencode

_IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")

# Tracking query params that never affect content identity — stripped during
# normalization so the same resource re-encountered with different tracking
# suffixes deduplicates correctly (avoids _1/_2 duplicates after Resume).
TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "fbclid", "gclid", "mc_cid", "mc_eid", "igshid",
})


def strip_tracking_params(url):
    """Remove only tracking query params (utm_*, fbclid, gclid, …), keep the
    order and values of every other parameter.

    Unlike normalize_url this does NOT sort or re-encode the query, so signed
    CDN URLs (token/signature/expiry params) survive untouched — the URL stays
    byte-identical apart from the removed tracking keys. Fail-open.
    """
    try:
        if not url or "?" not in url:
            return url
        base, _, rest = url.partition("?")
        # Fragment must not bleed into a query pair (utm_source=x#frag is a
        # tracking param + fragment, not a key to drop the fragment with).
        query, _, fragment = rest.partition("#")
        if not query:
            return url
        kept = []
        changed = False
        for pair in query.split("&"):
            key = pair.split("=", 1)[0].lower()
            if key in TRACKING_PARAMS:
                changed = True
                continue
            kept.append(pair)
        if not changed:
            return url
        out = base + ("?" + "&".join(kept) if kept else "")
        if fragment:
            out += "#" + fragment
        return out
    except Exception:
        return url


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


# Pre-emptive consent/age-gate cookies (ported from web-media-parser WP-5.2).
# Sent with the FIRST request to a page so cookie-consent and age-verification
# walls are answered before they render — the server then serves the real
# content instead of a gateway stub. Cookie sessions still persist any
# Set-Cookie the site returns (per-thread session cache), giving per-domain
# continuity across the run.
CONSENT_COOKIES = {
    "cookieconsent_status": "dismiss",
    "gdpr_accepted": "true",
    "cookies_accepted": "true",
    "euconsent": "true",
    "CookieConsent": "true",
    "cc_cookie_accept": "1",
    "cookie_consent": "true",
    "privacy_policy_accepted": "true",
    # Pre-emptive strike for age gates and gateways
    "age_verified": "1",
    "vantage": "1",
    "over18": "1",
    "nw": "1",
    "nsfw": "1",
    "terms": "1",
}

# URLs we must never decorate with consent cookies: the search/reader proxy
# (r.jina.ai) and the search engine itself.
_NO_CONSENT_HOST_HINTS = ("r.jina.ai", "duckduckgo.com", "html.duckduckgo.com")


def consent_cookie_header(url):
    """Return a `Cookie` header value with pre-set consent/age cookies, or None.

    Never applied to search/reader proxies (r.jina.ai, duckduckgo). Fail-open:
    any error returns None (no cookies, request proceeds unchanged).
    """
    try:
        if not url or not isinstance(url, str):
            return None
        low = url.lower()
        if any(h in low for h in _NO_CONSENT_HOST_HINTS):
            return None
        return "; ".join(f"{k}={v}" for k, v in CONSENT_COOKIES.items())
    except Exception:
        return None


# ── Netscape cookies.txt support (user-supplied session cookies) ───────────
# Lets a caller send real login/age-gate/session cookies from a browser export
# alongside a fetch. Jar semantics follow donsetch (EVALUATED_TOOLS §5a):
# RFC 6265 domain/path matching, Secure cookies never replay over plain HTTP,
# longest path first, header capped at 50 pairs / 4096 bytes. Fail-open: any
# error or empty match yields "" (the request proceeds without cookies).

MAX_COOKIE_HEADER_PAIRS = 50
MAX_COOKIE_HEADER_BYTES = 4096


def parse_netscape_cookies(text):
    """Parse cookies.txt (Netscape format) content into a list of dicts.

    Line format (TAB-separated):
        domain <TAB> include_subdomains <TAB> path <TAB> secure <TAB> expires <TAB> name <TAB> value
    A "#HttpOnly_" prefix marks an HttpOnly cookie — the prefix is honored and
    stripped (an HttpOnly cookie is still sendable in a Cookie request
    header). Other "#" lines are comments. Lines without 7 TAB fields are
    retried leniently with whitespace splitting (the value keeps internal
    spaces). Fail-open: malformed input yields the cookies parsed so far.
    """
    jar = []
    try:
        for raw in (text or "").splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#HttpOnly_"):
                line = line[len("#HttpOnly_"):]
            elif line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                parts = line.split(None, 6)      # lenient: any whitespace
            if len(parts) < 7:
                continue
            domain, _flag, path, secure, expires, name, value = parts[:7]
            domain = domain.strip().lower()
            name = name.strip()
            if not domain or not name:
                continue
            try:
                expires_at = float(str(expires).strip() or 0)
            except (TypeError, ValueError):
                expires_at = 0.0                 # unreadable = session cookie
            jar.append({
                "domain": domain,
                "path": path.strip() or "/",
                "secure": str(secure).strip().lower() == "true",
                "expires": expires_at,
                "name": name,
                "value": value,
            })
    except Exception:
        return jar
    return jar


def cookie_header_for(jar, url, now=None):
    """Build a `Cookie` header value for ``url`` from a parsed jar.

    Matching rules (RFC 6265 §5.1.4, simplified; donsetch jar semantics):
      * domain — exact host match, or a dot-leading domain matches the host
        suffix on a dot boundary (".example.com" matches "example.com" and
        "a.example.com", never "notexample.com");
      * path — cookie path prefixes the URL path on a "/" boundary
        ("/" matches everything);
      * Secure cookies are NEVER sent over plain HTTP (the secure-cookie-leak
        invariant, enforced here rather than trusted to the caller);
      * expired cookies are dropped (expires <= 0 means session: keep);
      * longest cookie path first, capped at MAX_COOKIE_HEADER_PAIRS /
        MAX_COOKIE_HEADER_BYTES (first overflow stops the header).
    Fail-open: "" when nothing matches or anything errors.
    """
    try:
        if not jar or not url:
            return ""
        parsed = urlparse(str(url))
        host = (parsed.hostname or "").lower()
        path = parsed.path or "/"
        is_https = (parsed.scheme or "").lower() == "https"
        now = time.time() if now is None else now
        picked = []
        for c in jar:
            if not isinstance(c, dict):
                continue
            dom = str(c.get("domain") or "").lower()
            if not dom:
                continue
            if dom.startswith("."):
                if not (host == dom[1:] or host.endswith(dom)):
                    continue
            elif host != dom:
                continue
            try:
                exp = float(c.get("expires") or 0)
            except (TypeError, ValueError):
                exp = 0.0
            if exp > 0 and exp <= now:
                continue
            if c.get("secure") and not is_https:
                continue
            cpath = str(c.get("path") or "/")
            if cpath != "/":
                boundary = cpath if cpath.endswith("/") else cpath + "/"
                if not (path == cpath or path.startswith(boundary)):
                    continue
            picked.append(c)
        picked.sort(key=lambda c: len(str(c.get("path") or "/")), reverse=True)
        pairs = []
        total = 0
        for c in picked:
            if len(pairs) >= MAX_COOKIE_HEADER_PAIRS:
                break
            pair = f"{c['name']}={c.get('value') or ''}"
            extra = len(pair) + (2 if pairs else 0)   # "; " separator
            if total + extra > MAX_COOKIE_HEADER_BYTES:
                break
            pairs.append(pair)
            total += extra
        return "; ".join(pairs)
    except Exception:
        return ""
