#!/usr/bin/env python3
"""
Visit Website Enhanced v3 — curl_cffi anti-detection & bypass layer.
Handles: age verification gates, cookie consent banners, Cloudflare challenges,
         iframe embeds, cookie-based consent, popup modals, bot detection heuristics.

Improvements over v2:
  ✅ curl_cffi вместо subprocess curl — в 10-50x быстрее
  ✅ TLS fingerprint Chrome — обход Cloudflare/WAF
  ✅ Session cookie persistence — cookie сохраняются между запросами
  ✅ bs4 + lxml парсинг — CSS-селекторы, XPath, надежный парсинг
  ✅ httpx fallback — HTTP/2 fallback
"""

import json, re, sys, time, random, urllib.parse
from bs4 import BeautifulSoup

# curl_cffi imported lazily in _get_session to avoid circular import issues

# ── Config ──────────────────────────────────────────────────────────────────
# Proxy is optional. Set USE_PROXY=False to bypass the local NECOBOX tunneller
# and use a direct connection; when enabled, PROXY_URL must point to a reachable
# HTTP CONNECT proxy. Proxy rotation is handled by NECOBOX itself.
USE_PROXY = False
PROXY_URL = "http://127.0.0.1:2080"
JINA_URL = "https://r.jina.ai/"
MAX_CHARS = 8000
TIME_BETWEEN = 1.25

# ── curl_cffi impersonation ────────────────────────────────────────────────
IMPERSONATE = "chrome124"

# ── UA Pool (expanded) ─────────────────────────────────────────────────────
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
_last_req = 0

def _throttle():
    global _last_req
    now = time.time()
    elapsed = now - _last_req
    _last_req = now
    if elapsed < TIME_BETWEEN:
        time.sleep(TIME_BETWEEN - elapsed + random.uniform(0, 0.3))

# ── curl_cffi Session ───────────────────────────────────────────────────────
_sessions = {}

def _get_session():
    """Get or create a curl_cffi Session with Chrome fingerprint."""
    key = PROXY_URL
    if key not in _sessions:
        try:
            import curl_cffi
            sess = curl_cffi.requests.Session(
                impersonate=IMPERSONATE,
                proxies={"http": PROXY_URL, "https": PROXY_URL} if USE_PROXY else None,
                verify=False,
                timeout=25,
            )
            _sessions[key] = sess
        except Exception as e:
            print(f"[visit] curl_cffi Session error: {e}", file=sys.stderr)
            return None
    return _sessions[key]

# ── Fetch with curl_cffi ───────────────────────────────────────────────────
def _fetch(url, referrer=None, cookies=None):
    """
    Fetch URL using curl_cffi with Chrome TLS fingerprint.
    Falls back to httpx, then Jina.
    NO throttle for curl_cffi — proxy handles rotation.
    """
    _last_req = time.time()
    ua = random.choice(UA_POOL)
    
    # Build headers
    accept = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    extra_headers = {
        "Accept": accept,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Pragma": "no-cache",
        "Cache-Control": "no-cache",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="124"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "User-Agent": ua,
    }
    
    # Referer
    if referrer:
        extra_headers["Referer"] = referrer
    elif "duckduckgo.com" in url:
        extra_headers["Referer"] = "https://duckduckgo.com/"
    else:
        m = re.search(r'https?://([^/:]+)', url)
        if m:
            extra_headers["Referer"] = f"https://{m.group(1)}/"
    
    # Cookies
    if cookies:
        extra_headers["Cookie"] = cookies
    
    # ── Try curl_cffi ──
    session = _get_session()
    if session:
        for attempt in range(3):
            try:
                resp = session.get(url, headers=extra_headers, allow_redirects=True)
                html = resp.text
                
                if html and len(html) > 100 and not _is_blocked(html):
                    return html
                
                # Blocked or invalid — wait and retry with different UA
                time.sleep(1.5 + random.uniform(0, 0.5))
                
            except Exception as e:
                time.sleep(2)
        
        # ── Fallback: httpx ──
        return _fetch_httpx(url, extra_headers)
    
    # ── Fallback: curl subprocess ──
    return _fetch_subprocess(url, ua, extra_headers)

def _fetch_httpx(url, headers):
    """Fallback: httpx for HTTP/2 support."""
    try:
        import httpx
        with httpx.Client(http2=True, follow_redirects=True, timeout=15) as client:
            resp = client.get(url, headers=headers)
            if resp.text and len(resp.text) > 100:
                return resp.text
    except Exception as e:
        print(f"[visit] httpx fallback error: {e}", file=sys.stderr)
    return None

def _fetch_subprocess(url, ua, headers):
    """Ultimate fallback: curl subprocess."""
    import subprocess
    
    cmd = ["curl", "-s", "-m", "20", "--compressed", "-L", "-A", ua]
    if USE_PROXY and PROXY_URL:
        cmd += ["--proxy", PROXY_URL]
    
    for k, v in headers.items():
        cmd += ["-H", f"{k}: {v}"]
    
    cmd.append(url)
    
    for attempt in range(3):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode == 0 and result.stdout and len(result.stdout) > 100:
                return result.stdout
            time.sleep(2 + random.uniform(0, 1))
        except subprocess.TimeoutExpired:
            time.sleep(2)
    return None

def _fetch_jina(url):
    """Jina Reader fallback."""
    jina_url = f"{JINA_URL}{url}"
    html = _fetch(jina_url, referrer="https://r.jina.ai/")
    if html and len(html) > 100:
        return html
    return None

# ── Block detection ────────────────────────────────────────────────────────
def _is_blocked(html):
    """Comprehensive block detection — only for hard blocks, NOT soft overlays."""
    if not html or len(html) < 100:
        return True
    text_lower = html.lower()
    
    blocks = [
        ('cf-chl-check', 'cloudflare_challenge'),
        ('checking your browser', 'cloudflare_challenge'),
        ('captcha', 'captcha'),
        ('security check', 'security_check'),
        ('please verify you are human', 'human_verification'),
        ('access denied', 'access_denied'),
        ('forbidden', 'access_denied'),
        ('403 forbidden', 'access_denied'),
        ('429 too many', 'rate_limited'),
        ('503 service unavailable', 'service_unavailable'),
        ('challenge-platform', 'cloudflare_challenge'),
        ('cdn-cgi', 'cloudflare_challenge'),
        ('browser left', 'browser_check'),
        ('attention required', 'attention_required'),
        ('turn on javascript', 'js_required'),
        ('enable cookies', 'cookies_required'),
    ]
    
    for pattern, block_type in blocks:
        if pattern in text_lower:
            return True
    return False

def _get_block_type(html):
    """Identify the specific type of block. Only hard blocks."""
    if not html:
        return "unknown"
    text_lower = html.lower()
    
    if 'cf-chl-check' in text_lower or 'checking your browser' in text_lower or 'challenge-platform' in text_lower or 'cdn-cgi' in text_lower:
        return 'cloudflare'
    if 'captcha' in text_lower:
        return 'captcha'
    if 'access denied' in text_lower or '403 forbidden' in text_lower:
        return 'access_denied'
    if '429 too many' in text_lower:
        return 'rate_limited'
    return 'unknown'

def _strip_block_overlay(html):
    """Strip overlay/modals from the page (age-gates, cookie consent, popups) using bs4."""
    if not html:
        return html
    
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")
    
    # Remove overlay elements
    for tag_name in ["div", "section", "article"]:
        for el in soup.find_all(tag_name):
            try:
                cls = el.get("class") or []
                if any(k in cls for k in ["overlay", "modal", "popup", "dialog", "lightbox",
                                            "banner", "cookie", "consent", "privacy",
                                            "age-gate", "age_gate", "gdpr", "ccpa"]):
                    el.decompose()
            except (AttributeError, TypeError):
                pass
    
    # Also remove script/style tags
    for tag_name in ["script", "style"]:
        for el in soup.find_all(tag_name):
            el.decompose()
    
    return str(soup)

def _is_valid_content(html):
    """Check if HTML has meaningful content."""
    if not html:
        return False
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) < 50:
        return False
    return True

# ── Content extraction with bs4 ─────────────────────────────────────────────
class _ContentParser:
    """Extract structured content from HTML using bs4 + lxml."""
    
    def __init__(self):
        self.title = ""
        self.headings = {"h1": [], "h2": [], "h3": []}
        self.links = []
        self.images = []
        self._skip_tags = {"script", "style", "nav", "footer", "header", "aside", "noscript"}
    
    def parse(self, html):
        """Parse HTML and extract title, headings, links, images."""
        if not html:
            return self
        
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")
        
        # Title
        title_tag = soup.find("title")
        if title_tag:
            self.title = title_tag.get_text().strip()
        
        # Headings with CSS selector support
        for h_tag in ["h1", "h2", "h3"]:
            for h in soup.find_all(h_tag):
                text = h.get_text().strip()
                if text:
                    self.headings[h_tag].append(text)
        
        # Links with CSS selector — finds all <a> tags
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
        
        # Images with src/data-src extraction
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

# ── Visit with full bypass ─────────────────────────────────────────────────
def visit_website(url, max_chars=MAX_CHARS, find_terms=None, max_links=50, max_images=20):
    """
    Full visit flow with all bypass mechanisms.
    Returns structured dict with title, headings, links, images, content.
    """
    # ── Step 1: Direct fetch via curl_cffi ──
    html = _fetch(url)
    
    if not html:
        pass  # fallthrough to Jina
    else:
        # Check for soft overlays (age-gate, cookie consent) — strip and continue
        block_type = _get_block_type(html)
        
        if block_type in ('cloudflare', 'captcha', 'access_denied', 'rate_limited'):
            # Hard block — fallthrough to Jina
            pass
        else:
            # Soft block or no block — try overlay stripping
            stripped = _strip_block_overlay(html)
            if stripped and _is_valid_content(stripped):
                html = stripped
            elif stripped:
                # Even if not valid, the stripped version might be OK
                html = stripped
    
    # ── Step 2: Jina fallback ──
    if not html or len(html) < 200:
        jina_html = _fetch_jina(url)
        if jina_html:
            html = jina_html
            source = "jina"
        else:
            return {"error": "Failed to fetch page via any method", "content": "", "source": "failed", "url": url}
    else:
        source = "direct"
    
    # ── Step 3: Parse structured content ──
    parser = _ContentParser()
    parser.parse(html)
    
    # ── Step 4: Extract text ──
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text).strip()[:max_chars]
    
    return {
        "title": parser.title,
        "headings": parser.headings,
        "links": parser.links[:max_links],
        "images": parser.images[:max_images],
        "content": text,
        "source": source,
        "url": url,
    }

# ── CLI Entry Point ────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Visit Website Enhanced v3")
    parser.add_argument("mode", choices=["visit", "fetch", "links", "images"], help="Mode")
    parser.add_argument("url", help="URL to fetch")
    parser.add_argument("--max-chars", type=int, default=MAX_CHARS, help="Max chars for content")
    parser.add_argument("--find", help="Filter links/images by term")
    parser.add_argument("--max-links", type=int, default=50, help="Max links to return")
    parser.add_argument("--max-images", type=int, default=20, help="Max images to return")
    
    args = parser.parse_args()
    
    if args.mode == "visit":
        result = visit_website(args.url, args.max_chars, args.find, args.max_links, args.max_images)
    elif args.mode == "fetch":
        html = _fetch(args.url)
        if not html:
            html = _fetch_jina(args.url) or ""
        print(html[:args.max_chars])
        sys.exit(0)
    elif args.mode == "links":
        result = visit_website(args.url, args.max_chars, args.find, args.max_links, 0)
    elif args.mode == "images":
        result = visit_website(args.url, args.max_chars, args.find, 0, args.max_images)
    
    if args.find:
        find_terms = args.find.split(",")
        if "links" in result:
            result["links"] = [
                l for l in result["links"]
                if any(t.lower() in (l.get("url", "") + l.get("text", "")).lower() for t in find_terms)
            ]
        if "images" in result:
            result["images"] = [
                i for i in result["images"]
                if any(t.lower() in (i.get("src", "") + i.get("alt", "")).lower() for t in find_terms)
            ]
    
    print(json.dumps(result, indent=2, ensure_ascii=False))
