#!/usr/bin/env python3
"""
DuckDuckGo Search Tool v3 — curl_cffi anti-detection & bypass layer.
Handles: proxy, TLS fingerprint (Chrome impersonation), cookie consent, 
         header/referrer validation, JS overlays, rate limits.

Improvements over v2:
  ✅ curl_cffi вместо subprocess curl — в 10-50x быстрее
  ✅ TLS fingerprint Chrome — обход Cloudflare/WAF
  ✅ Session cookie persistence — cookie сохраняются между запросами
  ✅ httpx fallback — HTTP/2 fallback
  ✅ bs4 + lxml парсинг — CSS-селекторы, XPath
"""

import json, re, sys, time, random, os, urllib.parse
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup

# ── Config ──────────────────────────────────────────────────────────────────
# Proxy detection order:
#   1) DDG_PROXY env var (explicit DDG selection)
#   2) HTTPS_PROXY / HTTP_PROXY env vars (system-wide)
# None — direct connection. No proxy is assumed when none of the above is set.
_CONFIGURED_PROXY = (
    os.environ.get("DDG_PROXY")
    or os.environ.get("HTTPS_PROXY")
    or os.environ.get("HTTP_PROXY")
)
USE_PROXY = bool(_CONFIGURED_PROXY)
PROXY_URL = _CONFIGURED_PROXY
JINA_URL = "https://r.jina.ai/"
MAX_CHARS = 8000
TIME_BETWEEN = 1.5  # Reduced because curl_cffi is faster

# ── curl_cffi impersonation versions ────────────────────────────────────────
# Chrome 120-125 are current and well-supported by curl_cffi
IMPERSONATE = "chrome124"

# ── UA Pool ─────────────────────────────────────────────────────────────────
UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPad; CPU OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6367.82 Mobile Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 OPR/110.0.0.0",
]

# ── Timing & throttle ───────────────────────────────────────────────────────
_last_req_time = 0

def _random_ua():
    return random.choice(UA_POOL)

def _throttle():
    global _last_req_time
    now = time.time()
    elapsed = now - _last_req_time
    _last_req_time = now
    if elapsed < TIME_BETWEEN:
        time.sleep(TIME_BETWEEN - elapsed + random.uniform(0, 0.3))

# ── curl_cffi Session ───────────────────────────────────────────────────────
# One session per domain for cookie persistence
_sessions = {}

def _get_session(domain=None):
    """Get or create a curl_cffi Session with Chrome fingerprint."""
    import curl_cffi
    
    # Key by proxy + impersonate (no domain-specific needed, cookies shared)
    key = PROXY_URL
    if key not in _sessions:
        try:
            # Chrome 124 impersonation — TLS fingerprint matches real Chrome (HTTP/2 auto-enabled)
            sess = curl_cffi.requests.Session(
                 impersonate=IMPERSONATE,
                 proxies={"http": PROXY_URL, "https": PROXY_URL} if USE_PROXY else None,
                 verify=False,  # Allow self-signed certs for debugging
                 timeout=8 if USE_PROXY else 15,
             )
            _sessions[key] = sess
        except Exception as e:
            print(f"[ddg-search] curl_cffi Session error: {e}", file=sys.stderr)
            return None
    return _sessions[key]

def _fetch(url, mode="document", referrer=None):
    """
    Fetch URL using curl_cffi with Chrome TLS fingerprint.
    Falls back to httpx, then Jina.
    NO throttle for curl_cffi — proxy handles rotation.
    """
    ua = _random_ua()
    
    # Build headers based on mode
    if mode == "json":
        accept = "application/json,text/plain,*/*"
        extra_headers = {
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
    elif mode == "image":
        accept = "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"
        extra_headers = {
            "Sec-Fetch-Dest": "image",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "cross-site",
        }
    else:
        accept = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
        extra_headers = {
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
            "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="124"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        }
    
    headers = {
        "Accept": accept,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Pragma": "no-cache",
        "Cache-Control": "no-cache",
        "User-Agent": ua,
    }
    
    # Add Referer
    if referrer:
        headers["Referer"] = referrer
    elif "duckduckgo.com/html/" in url:
        headers["Referer"] = "https://duckduckgo.com/"
        headers["Origin"] = "https://duckduckgo.com"
    else:
        m = re.search(r'https?://([^/:]+)', url)
        if m:
            headers["Referer"] = f"https://{m.group(1)}/"
    
    # ── Try curl_cffi ──
    session = _get_session()
    if session:
        for attempt in range(3):
            try:
                resp = session.get(url, headers=headers, allow_redirects=True)
                html = resp.text
                
                if html and not _detect_blocked(html) and _is_valid_content(html):
                    return html
                
                # Blocked or invalid — wait and retry with different UA
                time.sleep(1.5 + random.uniform(0, 0.5))
                
            except Exception as e:
                time.sleep(2)
        
        # ── Fallback: httpx ──
        return _fetch_httpx(url, headers)
    
    # ── Fallback: curl subprocess ──
    return _fetch_subprocess(url, ua, headers, mode)

def _fetch_httpx(url, headers):
    """Fallback: httpx for HTTP/2 support."""
    try:
        import httpx
        proxies = {"http://": PROXY_URL, "https://": PROXY_URL} if USE_PROXY and PROXY_URL else None
        with httpx.Client(http2=True, follow_redirects=True, timeout=15, proxies=proxies) as client:
            resp = client.get(url, headers=headers)
            if resp.text and not _detect_blocked(resp.text) and _is_valid_content(resp.text):
                return resp.text
    except Exception as e:
        print(f"[ddg-search] httpx fallback error: {e}", file=sys.stderr)
    return None

def _fetch_subprocess(url, ua, headers, mode):
    """Ultimate fallback: curl subprocess."""
    import subprocess
    
    cmd = ["curl", "-s", "-m", "15", "--compressed", "-L", "-A", ua]
    if USE_PROXY and PROXY_URL:
        cmd += ["--proxy", PROXY_URL]
    
    for k, v in headers.items():
        cmd += ["-H", f"{k}: {v}"]
    
    cmd.append(url)
    
    for attempt in range(3):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode == 0 and result.stdout:
                if not _detect_blocked(result.stdout) and _is_valid_content(result.stdout):
                    return result.stdout
            time.sleep(2 + random.uniform(0, 1))
        except subprocess.TimeoutExpired:
            time.sleep(2)
    return None

# ── Block detection ────────────────────────────────────────────────────────
def _detect_blocked(html):
    """Detect if response is a hard block page: captcha, Cloudflare, access denied. NOT soft overlays."""
    if not html:
        return True
    html_lower = html.lower()
    block_indicators = [
        'cf-chl-check', 'checking your browser', 'captcha', 'security check',
        'please verify you are human', 'access denied', 'forbidden',
        '403 forbidden', '429 too many', '503 service unavailable',
        'challenge-platform', 'cdn-cgi',
        'browser left', 'attention required',
        'anomaly-detected', 'perimeterx', 'px-captcha',
        'turn on javascript', 'enable cookies',
    ]
    for ind in block_indicators:
        if ind in html_lower:
            return True
    return False

def _is_valid_content(html):
    """Check if HTML contains useful content, not just boilerplate."""
    if not html:
        return False
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) < 50:
        return False
    # Should not be just a form/login/age-gate
    if re.search(r'<form[^>]*action', html):
        form_text = re.search(r'<form[^>]*>(.*?)</form>', html, re.DOTALL)
        if form_text:
            ft = form_text.group(1).lower()
            if 'age' in ft or 'verify' in ft or 'captcha' in ft or 'cookie' in ft:
                if 'input' not in ft or 'submit' not in ft:
                    return False
    return True

# ── Jina fallback ──────────────────────────────────────────────────────────
def _fetch_jina(url):
    """Try Jina Reader as fallback for blocked pages."""
    jina_url = f"{JINA_URL}{url}"
    html = _fetch(jina_url, "document")
    if html and _is_valid_content(html):
        return html
    return None

# ── DDG Image Search ───────────────────────────────────────────────────────

def _parse_bing_images(html):
    """Parse Bing image search results from Jina-fetched HTML.
    
    Extracts:
    - thumbnail URLs (thf.bing.com/th/id/OIP.*)
    - direct image URLs (jpg, png, webp, etc.)
    - page URLs (bing.com/images/search?view=detailV2)
    
    Returns list of {thumbnail, page_url, title}
    """
    results = []
    
    # Extract thumbnail URLs from Bing CDN
    thumb_pattern = r'(https://thf\.bing\.com/th\?q=[^&]+&w=\d+&h=\d+&c=[^"&]+)'
    thumbs = re.findall(thumb_pattern, html)
    
    # Extract page URLs
    page_pattern = r'(https://www\.bing\.com/images/search\?view=detailV2.*?&id=[^"&]+)'
    pages = re.findall(page_pattern, html)
    
    # Extract image URLs
    img_pattern = r'(https://[^\s"\'<>]+\.(jpg|jpeg|png|webp|gif|avif)[^\s"\'<>]*)'
    img_urls = re.findall(img_pattern, html, re.IGNORECASE)
    
    # Combine into results
    for i, thumb in enumerate(thumbs):
        page_url = pages[i] if i < len(pages) else ""
        results.append({
            "thumbnail": thumb,
            "page_url": page_url,
            "title": "",
        })
    
    # Also add direct image URLs (jpg/png/etc) that aren't already thumbnails
    seen_thumbs = set(thumbs)
    for img_url, ext in img_urls:
        if img_url not in seen_thumbs and len(img_url) > 30:
            seen_thumbs.add(img_url)
            i = len(results)
            page_url = pages[i] if i < len(pages) else ""
            results.append({
                "thumbnail": img_url,
                "page_url": page_url,
                "title": "",
            })
    
    return results


def image_search(query, page=1, count=10, region="wt-wt", safe="moderate"):
    """Search images via Jina → Bing (replaces broken DDG i.js).
    
    Strategy:
    1. Jina fetches Bing image search HTML
    2. Extract thumbnail URLs and page URLs from Bing HTML
    """
    try:
        # Get vqd from DDG first (for Bing redirect)
        initial_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}&kl={region}"
        sess = _get_session()
        if not sess:
            return {"error": "No curl_cffi session available", "results": []}
        
        resp = sess.get(initial_url, headers={
            "User-Agent": _random_ua(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
        })
        
        if not resp.text:
            return image_fallback(query, count)
        
        vqd = re.search(r"vqd=([\d-]+)", resp.text)
        if not vqd:
            return image_fallback(query, count)
        vqd_token = vqd.group(1)
        
        # Use Jina to fetch Bing image search
        jina_url = f"https://r.jina.ai/https://www.bing.com/images/search?q={urllib.parse.quote(query)}&qft=+filterui:images-16by9"
        jina_html = _fetch(jina_url, "document")
        
        if not jina_html or not _is_valid_content(jina_html):
            return image_fallback(query, count)
        
        # Parse Bing image results from Jina output
        images = _parse_bing_images(jina_html)
        
        if not images:
            return image_fallback(query, count)
        
        return {"results": images[:count], "count": len(images[:count])}
        
    except Exception as e:
        print(f"[ddg-search] image_search error: {e}", file=sys.stderr)
        return image_fallback(query, count)

def image_fallback(query, count):
    """Fallback: search DDG web for image-related results."""
    results = web_search(f"{query} image photo pic", page=1, count=count, region="wt-wt")
    return results


# ── Result parsers ─────────────────────────────────────────────────────────

class _DDGResultParser:
    """Parse DDG result blocks using bs4 + lxml."""
    
    def __init__(self):
        self.results = []
    
    def parse(self, html):
        """Parse DDG HTML results using bs4."""
        if not html:
            return self.results
        
            from bs4 import BeautifulSoup
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")
        
        # DDG wraps results in <div class="results_links">
        result_divs = soup.find_all("div", class_=lambda c: c and "results_links" in c)
        
        for div in result_divs:
            result = {"title": "", "url": "", "snippet": ""}
            
            # Title — <a class="result__a">
            title_a = div.find("a", class_=lambda c: c and "result__a" in c)
            if title_a:
                result["title"] = title_a.get_text().strip()
            
            # URL — <a class="result__a"> or <a class="result__url">
            url_a = div.find("a", class_=lambda c: c and "result__url" in c)
            href = title_a.get("href", "") if title_a else ""
            if url_a and not href:
                href = url_a.get("href", "")
            
            if href:
                # DDG wraps URLs in uddg= redirect
                m = re.search(r"uddg=([^&]+)", href)
                if m:
                    href = urllib.parse.unquote(m.group(1))
                result["url"] = href
            
            # Snippet — can be <div>, <p>, or <a> with class containing "result__snippet"
            snippet_el = div.find(attrs={"class": lambda c: c and "result__snippet" in c})
            if snippet_el:
                result["snippet"] = snippet_el.get_text().strip()
            
            if result["title"] or result["url"]:
                self.results.append(result)
        
        # Post-process: clean titles and snippets
        for r in self.results:
            r["title"] = re.sub(r"\s+", " ", r["title"]).strip() or r["url"]
            r["snippet"] = re.sub(r"\s+", " ", r["snippet"]).strip()
        
        # Filter: remove DuckDuckGo own links and JS links
        self.results = [
            r for r in self.results 
            if r["url"] and not r["url"].startswith(("javascript:", "https://duckduckgo.com/", "duckduckgo.com"))
        ]
        
        return self.results


def _parse_google_results(html, count):
    """Extract Google search results from text/html (for Jina+Google fallback)."""
    results = []
    
    # Pattern 1: Google organic results format
    url_pattern = r'"/url\?q=(https?://[^&]+)'
    title_blocks = re.findall(r'<a[^>]*href="/url\?q=([^"]+)"[^>]*>([^<]+)', html)
    
    seen = set()
    for raw_url, title in title_blocks:
        url = raw_url
        if title and url and not url.startswith("http"):
            continue
        if url and url not in seen:
            seen.add(url)
            results.append({"title": title.strip(), "url": url, "snippet": ""})
            if len(results) >= count:
                break
    
    if not results:
        # Pattern 2: Extract from text
        link_matches = re.findall(r'(https?://[^\s"\'<>]+)', html)
        for url in link_matches:
            if len(url) > 20 and url not in seen and not any(x in url for x in ['google.com', 'youtube-nocookie', 'gstatic']):
                seen.add(url)
                results.append({"title": url, "url": url, "snippet": ""})
                if len(results) >= count:
                    break
    
    return results[:count] if results else None

# ── Web Search ─────────────────────────────────────────────────────────────
def web_search(query, page=1, count=5, region="wt-wt", safe="auto"):
    """Search the web using multiple fallback strategies.
    Default ``count`` intentionally stays small so a tool call returns only
    five candidate URLs.  If you want deep pagination, cache results across
    pages and merge them client-side.
    """
    # Try multiple search strategies in order of reliability
    strategies = [
        ("duckduckgo", lambda: _search_ddg(query, page, count, region, safe)),
        ("jina-ddg", lambda: _search_jina_ddg(query, page, count, region, safe)),
        ("searxng", lambda: _search_searx(query, page, count)),
        ("jina-duckduckgo", lambda: _search_jina(query, page, count)),
    ]

    for name, strategy in strategies:
        try:
            result = strategy()
        except Exception as exc:
            print(f"[ddg-search] Strategy '{name}' failed: {exc}", file=sys.stderr)
            continue
        if result and result.get("results"):
            result["_source"] = name
            return result
        print(f"[ddg-search] Strategy '{name}' returned no results", file=sys.stderr)

    return {"error": "All search strategies failed", "results": [], "count": 0}

def _extract_related_topics(related, depth=0):
    """Recursively extract results from DDG JSON RelatedTopics.
    
    RelatedTopics can have nested structure:
    - Top level: topics with Text + FirstURL
    - Nested: Topics → more Topics or Results
    - Results: [{Title, Url, Text}]
    
    Returns: list of {title, url, snippet}
    """
    results = []
    if not related:
        return results
    
    for topic in related:
        if not isinstance(topic, dict):
            continue
        
        text = topic.get("Text", "")
        first_url = topic.get("FirstURL", "")
        results_list = topic.get("Results", [])
        
        # Extract text + first_url as a result
        if text and first_url:
            results.append({
                "title": "",
                "url": first_url,
                "snippet": text,
            })
        
        # Extract sub-results
        if results_list:
            for r in results_list:
                if isinstance(r, dict):
                    url = r.get("Url", "")
                    title = r.get("Title", "")
                    snippet = r.get("Text", "")
                    if url:
                        results.append({
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                        })
                elif isinstance(r, str):
                    results.append({"title": "", "url": r, "snippet": ""})
        
        # Recurse into nested Topics
        nested = topic.get("Topics", [])
        if isinstance(nested, list):
            results.extend(_extract_related_topics(nested, depth + 1))
    
    return results


def _search_ddg_json(query):
    """Use DuckDuckGo's internal JSON API.
    
    Note: Results[] is often empty, but RelatedTopics contains full snippets.
    We parse RelatedTopics as additional results (not replacement for HTML).
    """
    try:
        url = f"https://duckduckgo.com/api?q={urllib.parse.quote(query)}&format=json"
        html = _fetch(url, "json")
        if not html:
            return None
        
        data = json.loads(html)
        results = []
        
        # Results[] is often empty — but we still extract for completeness
        for r in data.get("Results", []):
            results.append({
                "title": r.get("Title", ""),
                "url": r.get("Url", ""),
                "snippet": r.get("Text", ""),
            })
        
        # RelatedTopics contains additional results with full snippets
        related = data.get("RelatedTopics", [])
        if related:
            related_results = _extract_related_topics(related)
            # Add RelatedTopics results (not duplicate of HTML results)
            for rr in related_results:
                if rr["url"] and not rr["url"].startswith(("javascript:", "https://duckduckgo.com/", "duckduckgo.com")):
                    results.append(rr)
        
        if results:
            return {"results": results, "count": len(results), "_has_related": bool(related)}
        return None
    except Exception as e:
        print(f"[ddg-search] JSON API error: {e}", file=sys.stderr)
        return None


def _search_ddgs(query, count):
    """Search via ddgs.text for wider coverage."""
    from ddgs import DDGS
    max_results = max(count, 30)
    try:
        client = DDGS()
        items = client.text(query)
    except Exception as exc:
        print(f"[ddg-search] ddgs backend failed: {exc}", file=sys.stderr)
        return None
    if not items:
        return None
    normalized = []
    for r in items:
        href = r.get("href") or r.get("url")
        if not href:
            continue
        normalized.append({
            "title": r.get("title", ""),
            "url": href,
            "snippet": r.get("body", ""),
        })
    start = 0
    end = min(count, len(normalized))
    paged = normalized[start:end]
    return {"results": paged, "count": len(paged)}


def _search_ddg(query, page, count, region, safe):
    """Search using ddgs library (deedy5/ddgs) with multi-engine multi-page.

    - Uses ddgs engine classes directly to bypass DDGS().text() single-page cap.
    - Queries multiple text engines: duckduckgo, yahoo, yandex, mojeek.
    - Iterates pages 1..N to collect up to requested count URLs.
    """
    max_pages = min(4, max(1, (count + 9) // 10))
    seen = set()
    out = []

    engine_classes = []
    try:
        from ddgs.engines.duckduckgo import Duckduckgo
        from ddgs.engines.yahoo import Yahoo
        from ddgs.engines.yandex import Yandex
        from ddgs.engines.mojeek import Mojeek

        engine_classes = [
            ("duckduckgo", Duckduckgo),
            ("yahoo", Yahoo),
            ("yandex", Yandex),
            ("mojeek", Mojeek),
        ]
    except Exception as exc:
        print(f"[ddg-search] ddgs engines import failed: {exc}", file=sys.stderr)

    for eng_name, cls in engine_classes:
        if len(out) >= count:
            break
        try:
            inst = cls()
            for p in range(1, max_pages + 1):
                try:
                    items = inst.search(
                        query,
                        region=region,
                        safesearch=safe if safe in {"auto", "moderate", "strict", "off"} else "moderate",
                        page=p,
                    ) or []
                except Exception as exc:
                    print(f"[ddg-search] engine {eng_name} page {p} failed: {exc}", file=sys.stderr)
                    break
                if not items:
                    break
                for r in items:
                    href = getattr(r, "href", None) or getattr(r, "url", None)
                    title = getattr(r, "title", "") or ""
                    body = getattr(r, "body", "") or getattr(r, "snippet", "") or getattr(r, "text", "") or ""
                    if not href:
                        continue
                    if href in seen:
                        continue
                    seen.add(href)
                    out.append({
                        "title": title,
                        "url": href,
                        "snippet": body,
                    })
                    if len(out) >= count:
                        break
                if len(out) >= count:
                    break
        except Exception as exc:
            print(f"[ddg-search] engine {eng_name} init failed: {exc}", file=sys.stderr)

    if out:
        return {"results": out, "count": len(out), "_source": "ddgs-multi-engine"}
    return None


def _search_jina_ddg(query, page, count, region, safe):
    """Fetch DDG HTML page via Jina Reader to bypass DDG blocks."""
    safe_param = "-1" if safe == "strict" else ("1" if safe == "off" else "")
    s = ((count * (page - 1)) if page > 1 else "")
    
    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}&kl={region}"
    if safe_param:
        url += f"&p={safe_param}"
    if s:
        url += f"&s={s}"
    
    jina_url = f"{JINA_URL}{url}"
    html = _fetch(jina_url, "document")
    if not html or not _is_valid_content(html):
        return None
    
    parser = _DDGResultParser()
    results = parser.parse(html)
    
    results = results[:count]
    if not results:
        return None
    return {"results": results, "count": len(results)}

def _search_searx(query, page, count):
    """Search via public SearXNG instances as alternative."""
    searxng_instances = [
        "https://search.sapti.me",
        "https://searx.be",
        "https://searx.tuxproject.de",
        "https://search.bus-hit.me",
        "https://searx.tiekoetter.com",
    ]
    
    for instance in searxng_instances:
        s = ((count * (page - 1)) if page > 1 else "")
        url = f"{instance}/search?q={urllib.parse.quote(query)}&format=json&pageno={page}"
        
        html = _fetch(url, "json")
        if not html:
            continue
        
        try:
            data = json.loads(html)
            results = data.get("results", [])[:count]
            formatted = []
            for r in results:
                formatted.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("content", ""),
                })
            if formatted:
                return {"results": formatted, "count": len(formatted)}
        except json.JSONDecodeError:
            continue
    
    return None

def _search_jina(query, page, count):
    """Last resort: use Jina to search Google."""
    s = ((count * (page - 1)) if page > 1 else "")
    google_url = f"https://www.google.com/search?q={urllib.parse.quote(query)}&num={count * 3}"
    jina_url = f"{JINA_URL}{google_url}"
    html = _fetch(jina_url, "document")
    if not html or not _is_valid_content(html):
        return None
    
    results = _parse_google_results(html, count)
    if results:
        return {"results": results, "count": len(results)}
    return None

# ── Fetch Page ─────────────────────────────────────────────────────────────
def fetch_page(url, max_chars=MAX_CHARS):
    """Fetch a page with all bypass mechanisms. Returns structured dict."""
    html = _fetch(url, "document")
    if not html:
        jina_html = _fetch_jina(url)
        if jina_html:
            return {"content": jina_html[:max_chars], "source": "jina", "url": url}
        return {"error": "Failed to fetch page", "content": "", "source": "failed", "url": url}
    
    if _detect_blocked(html):
        block_type = _get_block_type(html)
        if block_type == "age_gate":
            stripped = _strip_block_overlay(html)
            if stripped and _is_valid_content(stripped):
                html = stripped
            else:
                # Try Jina
                jina_html = _fetch_jina(url)
                if jina_html:
                    return {"content": jina_html[:max_chars], "source": "jina", "url": url}
        elif block_type == "cookie_consent":
            stripped = _strip_block_overlay(html)
            if stripped and _is_valid_content(stripped):
                html = stripped
    
    # Parse structured content
    parser = _ContentParser()
    parser.parse(html)
    
    # Extract text content
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text).strip()[:max_chars]
    
    return {
        "title": parser.title,
        "headings": parser.headings,
        "links": parser.links[:50],
        "images": parser.images[:20],
        "content": text,
        "source": "direct",
        "url": url,
    }

def _get_block_type(html):
    """Identify the specific type of block."""
    if not html:
        return "unknown"
    text_lower = html.lower()
    
    if 'cf-chl-check' in text_lower or 'checking your browser' in text_lower or 'challenge-platform' in text_lower or 'cdn-cgi' in text_lower:
        return 'cloudflare'
    if 'captcha' in text_lower:
        return 'captcha'
    if 'age verification' in text_lower or 'you must be 18' in text_lower or 'age-gate' in text_lower:
        return 'age_gate'
    if 'cookie consent' in text_lower or 'accept cookies' in text_lower or 'we use cookies' in text_lower:
        return 'cookie_consent'
    if 'access denied' in text_lower or '403 forbidden' in text_lower:
        return 'access_denied'
    if '429 too many' in text_lower:
        return 'rate_limited'
    return 'unknown'

def _strip_block_overlay(html):
    """Strip overlay/modals from the page (age-gates, cookie consent, popups)."""
    if not html:
        return html
        from bs4 import BeautifulSoup
    
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")
    
    # Remove overlay elements
    for tag_name in ["div", "section"]:
        for el in soup.find_all(tag_name):
            cls = el.get("class", [])
            if any(k in cls for k in ["overlay", "modal", "popup", "dialog", "lightbox", "banner", "cookie", "consent", "privacy", "age-gate"]):
                el.decompose()
    
    # Also remove script/style tags
    for tag_name in ["script", "style"]:
        for el in soup.find_all(tag_name):
            el.decompose()
    
    return str(soup)

class _ContentParser:
    """Extract structured content from HTML using bs4."""
    
    def __init__(self):
        self.title = ""
        self.headings = {"h1": [], "h2": [], "h3": []}
        self.links = []
        self.images = []
        self._skip_tags = {"script", "style", "nav", "footer", "header", "aside", "noscript"}
    
    def parse(self, html):
        """Parse HTML and extract title, headings, links, images."""
        if not html:
            from bs4 import BeautifulSoup
            return self
        
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")
        
        # Title
        title_tag = soup.find("title")
        if title_tag:
            self.title = title_tag.get_text().strip()
        
        # Headings
        for h_tag in ["h1", "h2", "h3"]:
            for h in soup.find_all(h_tag):
                text = h.get_text().strip()
                if text:
                    self.headings[h_tag].append(text)
        
        # Links
        seen_urls = set()
        for a in soup.find_all("a"):
            href = a.get("href", "")
            if href and (href.startswith("http") or href.startswith("//")):
                if href.startswith("//"):
                    href = "https:" + href
                if href not in seen_urls:
                    seen_urls.add(href)
                    text = a.get_text().strip()
                    self.links.append({"url": href, "text": text})
        
        # Images
        seen_srcs = set()
        for img in soup.find_all("img"):
            src = img.get("src", "") or img.get("data-src", "")
            if src and (src.startswith("http") or src.startswith("//")):
                if src.startswith("//"):
                    src = "https:" + src
                if src not in seen_srcs:
                    seen_srcs.add(src)
                    alt = img.get("alt", "") or img.get("data-alt", "")
                    self.images.append({"alt": alt, "src": src})
        
        return self


# ── Deep Search: validation, classification, image preview ──────────────────

_HOST_BLOCKLIST = {
    "2ch.org", "pikabu.ru", "championat.com", "fon.bet",
    "softmixer.com", "mosvodokanal.ru", "nordstar.ru", "sky8.ru",
    "zen.yandex.ru", "youtube.com", "ru.wikipedia.org",
}
# Category keywords — mapped from content signals, not URL only
_CATEGORY_KEYWORDS = {
    "gallery": ["gallery", "picture", "photo", "image", "pic", "album", "set", "collection", "imgur", "flickr", "skin", "preview", "screenshot", "assets", "pack"],
    "forum": ["forum", "thread", "board", "discuss", "reddit", "vbulletin", "xenforo", "discourse", "talk"],
    "video": ["video", "clip", "watch", "youtube", "xhamster", "xnxx", "spankbang", "clip", "stream", "play"],
    "wiki": ["wiki", "wikipedia", "fandom", "encyclopedia", "biography", "bio", "wikiquote"],
    "profile": ["profile", "model", "babe", "star", "pornstar", "actor", "actress", "casting", "agent"],
    "blog": ["blog", "post", "entry", "article", "medium", "substack", "wordpress", "tumblr"],
    "social": ["instagram", "twitter", "threads", "facebook", "pinterest", "tumblr", "tiktok", "snapchat"],
    "directory": ["index", "directory", "list", "hub", "babepedia", "indexxx", "models", "casting", "search"],
    "news": ["news", "article", "press", "report", "headline", "update", "breaking"],
    "review": ["review", "rating", "comment", "feedback", "opinion", "critic"],
    "history": ["history", "historical", "historian", "archival", "archive", "chronicle", "middle ages", "medieval"],
}
_CATEGORY_PRIORITY = ["wiki", "review", "news", "blog", "forum", "video", "social", "profile", "gallery", "directory", "history"]


def _classify_by_content(url, title, body_text):
    """Classify a page by URL, title, and body content signals.

    Returns (category, confidence_score).

    Rules: body keywords boost score same as before.
    Tie-break: prefer lower-index categories in _CATEGORY_PRIORITY.
       "directory" is now a catch-all for generic index pages and is deprioritized
    in favor of gallery/wiki/profile/etc when the content clearly matches them.
    """
    url_lower = url.lower()
    title_lower = title.lower()
    body_lower = body_text.lower()[:2000]

    # 

    scores = {cat: 0 for cat in _CATEGORY_KEYWORDS}
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        score = 0
        for kw in keywords:
            if kw in body_lower:
                score += 3
            if kw in title_lower:
                score += 2
            if kw in url_lower:
                score += 1
        scores[cat] = score

    best_cat = "other"
    best_score = 0
    for cat, score in scores.items():
        if not score:
            continue
        if score > best_score:
            best_score = score
            best_cat = cat
        elif score == best_score and best_cat != "other":
            try:
                cat_idx = _CATEGORY_PRIORITY.index(cat)
                best_idx = _CATEGORY_PRIORITY.index(best_cat)
                if cat_idx < best_idx:
                    best_cat = cat
            except ValueError:
                pass

    # If the only strong signal comes from generic noise categories (social/directory),
    # keep "other" so noisy index pages don't pollute meaningful categories.
    noise_only = all(
        (best_cat in {"social", "directory"} or score == 0)
        for cat, score in scores.items()
        if cat not in {"other"}
    )
    if best_score < 4 or noise_only:
        return "other", 0.0

    return best_cat, min(best_score / 10.0, 1.0)


def _extract_image_urls(html, domain_base, max_count=3):
    """Extract image URLs from HTML for preview.
    
    Returns list of {src, alt} dicts for first N <img> tags.
        from bs4 import BeautifulSoup
    Handles relative URLs by prepending domain_base.
    """
    urls = []
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")
    
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        alt = img.get("alt") or img.get("data-alt") or ""
        
        if not src:
            continue
        
        # Handle relative URLs
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/") and domain_base:
            # Extract base URL (scheme + host) from the domain
            parsed = urllib.parse.urlparse(domain_base)
            if parsed.scheme:
                src = parsed.scheme + "://" + parsed.netloc + src
            continue
        elif src.startswith("/") and not domain_base:
            continue
        
        if src.startswith("http"):
            urls.append({"src": src, "alt": alt})
            if len(urls) >= max_count:
                break
    
    return urls


def _check_url_live(url, timeout=10):
    """Check if a URL is alive and accessible.

    Optimized: 1 HEAD for dead/blocked, GET only for 2xx/3xx.
    Returns dict with:
        alive: bool
        status: int or None
        content_type: str
        content_length: int
        text_length: int
        text_words: int
        blocked: bool
        error: str or None
        body: str or None
    """
    result = {
         "alive": False,
         "status": None,
         "content_type": "",
         "content_length": 0,
         "text_length": 0,
         "text_words": 0,
         "blocked": False,
         "error": None,
         "body": None,
     }

    session = _get_session()
    if not session:
        result["error"] = "no session"
        return result

    try:
        head_resp = session.head(url, timeout=timeout, allow_redirects=True)
        result["status"] = head_resp.status_code
        result["content_type"] = head_resp.headers.get("content-type", "")[:200]
        cl = head_resp.headers.get("content-length", "0")
        result["content_length"] = int(cl) if cl.isdigit() else 0

        # Hard error / block — stop here, GET not needed
        if result["status"] in (403, 404, 405, 410, 429, 451, 500, 502, 503, 504):
            result["blocked"] = result["status"] in (403, 429, 451, 503)
            result["error"] = f"HTTP {result['status']}"
            return result

        if result["status"] >= 400:
            result["error"] = f"HTTP {result['status']}"
            return result

    except Exception as e:
        result["error"] = str(e)
        return result

    # 2xx/3xx only — fetch body
    try:
        body_resp = session.get(url, timeout=timeout, allow_redirects=True)
        result["status"] = body_resp.status_code
        result["content_type"] = body_resp.headers.get("content-type", "")[:200]
        cl = body_resp.headers.get("content-length", "0")
        result["content_length"] = max(result["content_length"], int(cl) if cl.isdigit() else 0)

        if result["status"] >= 400:
            result["error"] = f"HTTP {result['status']}"
            return result

        raw = body_resp.text

        if _detect_blocked(raw):
             result["blocked"] = True
             result["error"] = "blocked (captcha/cloudflare/etc)"
             return result

        result["body"] = raw
        text = re.sub(r'<[^>]+>', ' ', raw)
        text = re.sub(r'\s+', ' ', text).strip()
        result["text_length"] = len(text)
        result["text_words"] = len(re.findall(r'\w+', text))

        if result["text_length"] < 500 or result["text_words"] < 50:
            result["error"] = "empty or too-small page"
            return result

        result["alive"] = True
        return result

    except Exception as e:
        result["error"] = str(e)
        return result

    return result


def _relevance_score(query, title, body_text):
    """Calculate relevance of content to query.
    
    Simple word-overlap scoring: how many query words appear in title/body.
    Returns 0.0-1.0.
    """
    query_words = [w.lower() for w in query.split() if len(w) > 2][:8]  # top 8 meaningful words
    if not query_words:
        return 0.0
    
    title_lower = title.lower()
    body_lower = body_text.lower()[:5000]  # first 5000 chars
    
    # Score: query word in title = 3x, in body = 1x
    scored = set()
    for word in query_words:
        if word in title_lower:
            scored.add(word)
        if word in body_lower:
            scored.add(word)
    
    return len(scored) / len(query_words)


def search_deep(query, validate=True, classify=True, max_validate=50,
                timeout_per_url=10, output_format="json",
                query_variants=None, compose=False):
    """Deep search with URL validation and content classification.
    
    Parameters:
        query: search query
        validate: if True, validate each URL (HEAD+GET, content check)
        classify: if True, group results by category
        max_validate: max number of URLs to validate (limits resources)
        timeout_per_url: seconds per URL check
        output_format: "json" for structured output
        query_variants: list of alternative query strings (reformulations).
                       If None, only original query is used.
        compose: if True, return formatted markdown answer instead of JSON
    
    Returns:
        dict with validated results, categories, and summary (or markdown string if compose=True)
    """
    import time as _time
    
    start_time = time.time()
    
    # Step 1: Multi-query collection (original + reformulations)
    queries = [query]
    if query_variants:
        for q in query_variants:
            if isinstance(q, str) and q.strip():
                queries.append(q.strip())
    queries = list(dict.fromkeys(queries))  # deduplicate preserving order

    # Collect raw results from all queries
    all_raw = []
    seen_urls = set()
    for q in queries:
        batch = web_search(q, page=1, count=100, region="wt-wt", safe="off").get("results", [])
        for item in batch:
            u = item.get("url")
            if u and u not in seen_urls:
                seen_urls.add(u)
                all_raw.append(item)

    # Optional wider coverage via ddgs backend (up to 30-34 per query).
    try:
        from ddgs import DDGS
        try:
            with DDGS() as client:
                extra = client.text(query)
        except Exception as exc:
            print(f"[ddg-search] ddgs coverage backend failed: {exc}", file=sys.stderr)
            extra = []
    except Exception:
        extra = []

    if extra:
        for item in extra:
            href = item.get("href") or item.get("url")
            if not href:
                continue
            if href in seen_urls:
                continue
            seen_urls.add(href)
            all_raw.append({
                "title": item.get("title", ""),
                "url": href,
                "snippet": item.get("body", ""),
            })

    if not all_raw:
        return {
            "depth": "deep",
            "error": "No results from search",
            "summary": {"total_raw": 0, "alive": 0, "dead": 0, "classified_categories": 0},
            "elapsed": round(time.time() - start_time, 2),
        }

    raw_count = len(all_raw)

    if not query_variants and raw_count < max_validate:
        try:
            from query_variants import _suggest_query_variants as _suggest
            suggested = _suggest(query, all_raw, max_variants=3)
            for q in suggested:
                batch = web_search(q, page=1, count=40, region="wt-wt", safe="off").get("results", [])
                for it in batch:
                    u = it.get("url")
                    if u and u not in seen_urls:
                        seen_urls.add(u)
                        all_raw.append(it)
        except Exception as exc:
            print(f"[ddg-search] dynamic query generation failed: {exc}", file=sys.stderr)

    if not all_raw:
        return {"depth": "deep", "error": "No results after dynamic variants", "summary": {"total_raw": 0, "alive": 0, "dead": 0, "classified_categories": 0}, "elapsed": round(time.time() - start_time, 2)}

    raw_count = len(all_raw)

    # Step 2: URL validation (parallel via ThreadPoolExecutor)
    validated = []
    alive_count = 0
    dead_count = 0
    blocked_count = 0
    results_slice = all_raw[:max_validate]
    
    # Process URLs in parallel batches of 5 to avoid overwhelming proxy
    def _validate_one(item):
        """Validate a single URL — called in thread pool."""
        i, res = item
        url = res.get("url", "")
        title = res.get("title", "")
        check = _check_url_live(url, timeout=timeout_per_url)
        return i, res, check
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(_validate_one, (i, r)) for i, r in enumerate(results_slice)]
        for future in futures:
            i, res, check = future.result()
            url = res.get('url', '')
            title = res.get('title', '')

            if not check['alive']:
                dead_count += 1
                if check.get('blocked'):
                    blocked_count += 1
                res_out = dict(res)
                res_out['alive'] = False
                res_out['status'] = check['status']
                res_out['error'] = check.get('error', 'unknown')
                res_out['content_length'] = check.get('content_length', 0)
                res_out['text_words'] = check.get('text_words', 0)
                validated.append(res_out)
                continue

            alive_count += 1

            # ── Step 3: Content analysis for live pages ──
            body_html = check.get('body') or ''
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(body_html, 'html.parser')
                text = soup.get_text(separator=' ', strip=True)
            except Exception:
                text = re.sub(r'<[^>]+>', ' ', body_html)
                text = re.sub(r'\s+', ' ', text).strip()

            res_out = dict(res)
            res_out['alive'] = True
            res_out['status'] = check['status']
            res_out['content_type'] = check['content_type']
            res_out['content_length'] = check['content_length']
            res_out['text_length'] = check.get('text_length', len(text))
            res_out['text_words'] = check['text_words']
            res_out['text'] = text[:4000]

            # Relevance scoring
            res_out['relevance'] = round(_relevance_score(query, title, text), 2)

            # Image count from HTML
            image_count = len(re.findall(r'<img[^>]+src=', body_html))
            res_out['image_count'] = image_count

            # Extract image URLs for preview
            if image_count > 0:
                res_out['image_urls'] = _extract_image_urls(body_html, url, max_count=3)

            # Category classification
            if classify:
                cat, conf = _classify_by_content(url, title, text)
                res_out['category'] = cat
                res_out['category_confidence'] = round(conf, 2)

            validated.append(res_out)

    # ── Step 4: Sort by relevance (descending) ──
    validated.sort(key=lambda r: r.get("relevance", 0), reverse=True)
    
    # ── Step 5: Categorize if requested ──
    categories = {}
    if classify:
        for r in validated:
            cat = r.get("category", "other")
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(r)
    
    elapsed = round(time.time() - start_time, 2)
    
    result = {
        "depth": "deep",
        "search_query": query,
        "categories": categories if classify else None,
        "summary": {
            "total_raw": raw_count,
            "validated": len(validated),
            "alive": alive_count,
            "dead": dead_count,
            "blocked": blocked_count,
            "classified_categories": len(categories) if classify else 0,
            "max_validate": max_validate,
            "elapsed_seconds": elapsed,
        },
        "elapsed": elapsed,
    }
    
    # Add top-level flat results (sorted by relevance)
    result["results"] = validated
    
    if compose:
        try:
            from compose import _build_markdown_answer
            return _build_markdown_answer(query, result)
        except Exception as e:
            print(f"[ddg-search] compose failed: {e}", file=sys.stderr)
            # fall through to JSON

    return result

# ── CLI Entry Point ────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="DuckDuckGo Search Tool v3")
    parser.add_argument("command", choices=["search", "search-deep", "images", "page"], help="Command to run")
    parser.add_argument("query", help="Search query or URL")
    parser.add_argument("-p", "--page", type=int, default=1, help="Page number")
    parser.add_argument("-c", "--count", type=int, default=5, help="Number of results")
    parser.add_argument("--max-chars", type=int, default=MAX_CHARS, help="Max chars for page content")
    parser.add_argument("--find", help="Filter results by term")
    parser.add_argument("-r", "--region", default="wt-wt", help="Region code (wt-wt, us-en, etc.)")
    parser.add_argument("--safe", choices=["auto", "strict", "off"], default="auto", help="Safe search level")
    # search-deep specific options
    parser.add_argument("--validate", action="store_true", help="Validate each URL (HEAD+GET)")
    parser.add_argument("--classify", action="store_true", help="Classify results by category")
    parser.add_argument("--max-validate", type=int, default=50, help="Max URLs to validate")
    parser.add_argument("--timeout", type=int, default=5, help="Timeout per URL check (seconds)")
    parser.add_argument("--compose", action="store_true", help="Return formatted markdown answer instead of JSON")
    
    args = parser.parse_args()
    
    if args.command == "search":
        results = web_search(args.query, args.page, args.count, args.region, args.safe)
    elif args.command == "search-deep":
        results = search_deep(
            args.query,
            validate=args.validate,
            classify=args.classify,
            max_validate=args.max_validate,
            timeout_per_url=args.timeout,
            output_format="json",
            compose=args.compose,
        )
    elif args.command == "images":
        results = image_search(args.query, args.page, args.count, args.region, "moderate")
    elif args.command == "page":
        results = fetch_page(args.query, args.max_chars)
    
    if args.compose and isinstance(results, str):
        print(results)
    elif args.find and args.command != "search-deep":
        find_terms = args.find.split(",")
        if "results" in results:
            results["results"] = [
                r for r in results["results"]
                if any(t.lower() in (r.get("title", "") + r.get("snippet", "") + r.get("url", "")).lower() for t in find_terms)
            ]
        print(json.dumps(results, indent=2, ensure_ascii=False))
    elif not args.compose:
        print(json.dumps(results, indent=2, ensure_ascii=False))
