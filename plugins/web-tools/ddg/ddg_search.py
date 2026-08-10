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

import importlib.util
import json, re, sys, time, random, os, urllib.parse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup

# Load adjacent query_variants helper once at import time to avoid per-call overhead
_QUERY_VARIANTS_MODULE = None
try:
    _qv_path = os.path.join(os.path.dirname(__file__), 'query_variants.py')
    _qv_spec = importlib.util.spec_from_file_location('query_variants', _qv_path)
    _qv_module = importlib.util.module_from_spec(_qv_spec)
    _qv_spec.loader.exec_module(_qv_module)
    _QUERY_VARIANTS_MODULE = _qv_module
except Exception:
    pass

# ── Config ──────────────────────────────────────────────────────────────────
# Proxy detection order:
#   1) DDG_PROXY env var (explicit DDG selection)
#   2) HTTPS_PROXY / HTTP_PROXY env vars (system-wide)
#   3) ~/.hermes/proxy.env file (persistent settings from GUI)
# None — direct connection. No proxy is assumed when none of the above is set.
def _load_proxy_from_file():
    """Read proxy URL from ~/.hermes/proxy.env if exists."""
    try:
        proxy_file = Path.home() / ".hermes" / "proxy.env"
        if proxy_file.exists():
            content = proxy_file.read_text().strip()
            if content and not content.startswith("#"):
                return content
    except Exception:
        pass
    return None

_CONFIGURED_PROXY = (
    os.environ.get("DDG_PROXY")
    or os.environ.get("HTTPS_PROXY")
    or os.environ.get("HTTP_PROXY")
    or _load_proxy_from_file()
)
USE_PROXY = bool(_CONFIGURED_PROXY)
PROXY_URL = _CONFIGURED_PROXY
JINA_URL = "https://r.jina.ai/"
MAX_CHARS = 8000

# ── curl_cffi impersonation versions ────────────────────────────────────────
# Rotate between supported Chrome versions to vary TLS fingerprint
IMPERSONATE_POOL = ["chrome110", "chrome116", "chrome120", "chrome124"]
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

def _random_ua():
    return random.choice(UA_POOL)

# ── curl_cffi Session ───────────────────────────────────────────────────────
# One session per domain for cookie persistence
_sessions = {}

def _get_session(domain=None):
    """Get or create a curl_cffi Session with rotating Chrome fingerprint.
    Main session always uses direct connection. Proxy is only used for retry."""
    import curl_cffi

    # Rotate impersonation to vary TLS fingerprint across sessions
    imp = random.choice(IMPERSONATE_POOL)
    # Key by impersonate only (no proxy — main session is always direct)
    key = imp
    if key not in _sessions:
        try:
            sess = curl_cffi.requests.Session(
                 impersonate=imp,
                 verify=False,
                 timeout=15,
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

    if isinstance(url, str):
        host = urllib.parse.urlparse(url).hostname or ""
        if host.startswith(("yandex.com", "yandex.ru", "yandex.net", "yandex.cloud")):
            headers["Accept-Language"] = "ru-RU,ru;q=0.9"

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
                # Rotate UA on each retry attempt
                if attempt > 0:
                    headers["User-Agent"] = _random_ua()
                resp = session.get(url, headers=headers, allow_redirects=True)
                html = resp.text
                
                if html and not _detect_blocked(html) and _is_valid_content(html):
                    return html
                
                # Blocked or invalid — exponential backoff
                time.sleep(min(1.5 * (2 ** attempt) + random.uniform(0, 0.5), 10))
                
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
        proxy = PROXY_URL if USE_PROXY and PROXY_URL else None
        kwargs = {"http2": True, "follow_redirects": True, "timeout": 15}
        if proxy:
            kwargs["proxy"] = proxy
        with httpx.Client(**kwargs) as client:
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
        'cf-chl-check', 'checking your browser',
        'please verify you are human', 'access denied', 'forbidden',
        '403 forbidden', '429 too many', '503 service unavailable',
        'challenge-platform', 'cdn-cgi',
        'browser left', 'attention required',
        'anomaly-detected', 'perimeterx', 'px-captcha',
        'turn on javascript', 'enable cookies',
        'javascript is disabled', 'enable javascript and then reload',
        'you need to enable javascript', 'requires javascript',
        # Russian regional blocks
        'данный контент недоступен', 'доступ к данной странице ограничен',
        'эта страница недоступна', 'контент заблокирован',
        'доступ запрещён', 'страница не найдена',
        'доступ закрыт', 'доступ к информационному ресурсу ограничен',
        'информация на данной странице ограничена', 'ресурс заблокирован',
        'доступ временно ограничен', 'доступ приостановлен',
    ]
    # Check for actual captcha forms (not just config mentions)
    if 'captcha' in html_lower:
        # Only flag if captcha appears in visible context, not JS config
        if '<form' in html_lower and 'captcha' in html_lower:
            return True
        if 'please complete the captcha' in html_lower or 'enter the captcha' in html_lower:
            return True
        if 'hcaptcha' in html_lower and 'challenge' in html_lower:
            return True
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


# ── Readability-style content extractor ──────────────────────────────────────
def _extract_main_content(html):
    """Extract main article content from HTML, removing nav/sidebar/footer/ads.

    Simplified Mozilla Readability algorithm:
    1. Find all candidate elements (div, article, section, main)
    2. Score by text length, link density (lower = better), paragraph count
    3. Return the text of the highest-scoring element
    """
    from bs4 import BeautifulSoup
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    # Remove noise elements first
    for tag in soup.find_all(["script", "style", "noscript", "iframe", "svg"]):
        tag.decompose()

    # Score candidate containers
    candidates = []
    for tag in soup.find_all(["article", "main", "section", "div"]):
        text = tag.get_text(strip=True)
        if len(text) < 200:
            continue
        if tag.attrs is None:
            continue

        text_len = len(text)
        paragraphs = len(tag.find_all("p"))
        links = len(tag.find_all("a"))
        link_density = links / max(text_len / 100, 1)

        tag_bonus = 0
        if tag.name == "article":
            tag_bonus = 200
        elif tag.name == "main":
            tag_bonus = 150
        elif tag.name == "section":
            tag_bonus = 50

        classes = [c.lower() for c in (tag.get("class") or [])]
        noise_penalty = 0
        if any(k in classes for k in ["sidebar", "menu", "nav", "footer", "header",
                                       "comment", "social", "share", "related", "recommend"]):
            noise_penalty = 500

        archive_penalty = 0
        text_lower = text.lower()
        if re.search(r'(?:archive|calendar|blog archive|◄|►)', text_lower):
            archive_penalty = 300
        if re.search(r'(?:january|february|march|april|may|june|july|august|september|october|november|december)\s*\d{4}', text_lower):
            archive_penalty += 200

        score = text_len + paragraphs * 50 - link_density * 100 + tag_bonus - noise_penalty - archive_penalty
        candidates.append((score, tag))

    if not candidates:
        return soup.get_text(separator="\n", strip=True)[:5000]

    candidates.sort(key=lambda x: x[0], reverse=True)
    best_tag = candidates[0][1]

    for tag in best_tag.find_all(["nav", "footer", "header", "aside", "form",
                                   "script", "style", "table"]):
        tag.decompose()
    for tag in best_tag.find_all(True):
        if tag.attrs is None:
            continue
        classes = [c.lower() for c in (tag.get("class") or [])]
        if any(k in classes for k in ["sidebar", "menu", "nav", "comment", "social",
                                       "share", "related", "recommend", "tags", "labels"]):
            tag.decompose()
    for a in best_tag.find_all("a"):
        a_text = a.get_text(strip=True)
        if len(a_text) < 15:
            a.decompose()

    return best_tag.get_text(separator="\n", strip=True)[:5000]


# Boilerplate patterns to strip from content
_NOISE_LINES = [
    r'Posted by \w+ at \d+:\d+', r'Email This BlogThis!', r'Share to \w+',
    r'Blog Archive', r'Newer Post', r'Older Post', r'Subscribe to:',
    r'Post a Comment', r'No comments:', r'Labels?:', r'Powered by',
    r'Picture Window theme', r'©\d{4}', r'Followers', r'About Me',
    r'View web version', r'Home\s+About\s+Privacy', r'Desktop Version',
    r'Mobile Version', r'Login to add', r'Have your say',
    r'Edit Page', r'Help keep', r'Recommended$', r'Six Degrees',
    r'Contributors$', r'Top Contributors', r'Follow .* on Facebook',
    r'(\d{4}\s*►|►\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec))',
    r'Original Resolution:', r'Jump to navigation', r'Jump to search',
    r'Picture Of', r'See more ideas',
]
_NOISE_RE = re.compile('|'.join(_NOISE_LINES), re.IGNORECASE)

_NOISE_BLOCKS = {
    'about', 'faq', 'copyright policy', 'privacy notice', 'terms of service',
    'remove ads', 'cookie policy', 'contact us', 'advertise',
    'what is', 'there are some things', 'we would also be interested',
    'discussions', 'have your say', 'be the first to make a comment',
}


def _clean_content(text):
    """Aggressively remove navigation, tags, boilerplate from text."""
    if not text:
        return ""
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        line = line.strip()
        if not line or len(line) < 10:
            continue
        if _NOISE_RE.search(line):
            continue
        if len(line) < 40 and any(w in line.lower() for w in _NOISE_BLOCKS):
            continue
        if line.startswith('http') and ' ' not in line:
            continue
        cleaned.append(line)
    result = '\n'.join(cleaned)
    result = re.sub(r'(?:Blog Archive|Newer Post|Older Post|Subscribe to|Picture Window).*', '', result, flags=re.DOTALL)
    return result.strip()[:4000]


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
    """Search images via Jina -> Bing (replaces broken DDG i.js).
    
    Strategy:
    1. Jina fetches Bing image search HTML
    2. Extract thumbnail URLs and page URLs from Bing HTML
    """
    try:
        # Use Jina to fetch Bing image search
        jina_url = f"https://r.jina.ai/https://www.bing.com/images/search?q={urllib.parse.quote(query)}"
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
    """Search the web using DuckDuckGo engines with DDGS supplement.
    DuckDuckGo primary + DDGS always runs for broader coverage.
    """
    strategies = [
        ("duckduckgo", lambda: _search_ddg(query, page, count, region, safe)),
    ]

    import time as _time

    # DuckDuckGo primary
    engine_results = []
    for name, strategy in strategies:
        result = None
        for attempt in range(2):
            try:
                result = strategy()
            except Exception as exc:
                print(f"[ddg-search] Strategy '{name}' attempt {attempt+1} failed: {exc}", file=sys.stderr)
                if attempt == 0:
                    _time.sleep(2)
                continue
            if result and result.get("results"):
                engine_results = result["results"]
                break
            if attempt == 0:
                print(f"[ddg-search] Strategy '{name}' returned no results, retrying...", file=sys.stderr)
                _time.sleep(2)
            else:
                print(f"[ddg-search] Strategy '{name}' returned no results (final)", file=sys.stderr)
                break
        if engine_results:
            break

    # DDGS always runs to expand coverage (different result set than duckduckgo)
    try:
        from ddgs import DDGS
        with DDGS() as client:
            extra = client.text(query)
        if extra:
            query_words = set(query.lower().split())
            ddgs_results = []
            for item in extra:
                href = item.get("href") or item.get("url")
                if not href:
                    continue
                text = (item.get("title", "") + " " + item.get("body", "")).lower()
                matches = sum(1 for w in query_words if w in text)
                # Single-word queries must not require 2 word matches to keep DDGS coverage
                min_matches = 2 if len(query_words) >= 2 else 1
                if matches >= min_matches:
                    ddgs_results.append({"title": item.get("title", ""), "url": href, "snippet": item.get("body", "")})
            # Merge: engines first, then DDGS (deduplicated)
            seen = set()
            merged = []
            for r in engine_results + ddgs_results:
                url = r.get("url", "")
                if url and url not in seen:
                    seen.add(url)
                    merged.append(r)
            engine_results = merged
    except Exception:
        pass

    if engine_results:
        return {"results": engine_results[:count], "count": min(len(engine_results), count), "_source": "merged"}

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
    elif 'captcha' in text_lower:
        return 'captcha'
    elif 'age verification' in text_lower or 'you must be 18' in text_lower or 'age-gate' in text_lower:
        return 'age_gate'
    elif 'cookie consent' in text_lower or 'accept cookies' in text_lower or 'we use cookies' in text_lower:
        return 'cookie_consent'
    elif 'access denied' in text_lower or '403 forbidden' in text_lower:
        return 'access_denied'
    elif '429 too many' in text_lower:
        return 'rate_limited'
    return 'unknown'

def _strip_block_overlay(html):
    """Strip overlay/modals from the page (age-gates, cookie consent, popups)."""
    if not html:
        return html

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    OVERLAY_IDS = {"age-gate", "age_gate", "cookie-consent", "cookie-banner",
                   "consent-dialog", "gdpr-modal", "ccpa-banner", "privacy-banner"}
    OVERLAY_CLASSES = {"overlay", "modal", "popup", "dialog", "lightbox",
                       "banner", "cookie", "consent", "privacy", "age-gate",
                       "age_gate", "gdpr", "ccpa"}
    TEXT_PATTERNS = ["are you over 18", "are you 18", "age verification",
                     "accept all cookies", "accept cookies", "we use cookies",
                     "i agree", "i accept", "continue to site",
                     "verify you are", "confirm you are"]
    BUTTON_TEXTS = {"accept all", "i agree", "i accept", "accept cookies",
                    "allow all", "continue", "got it", "ok"}

    for tag_name in ["div", "section", "article", "aside", "header", "footer"]:
        for el in soup.find_all(tag_name):
            try:
                el_id = (el.get("id") or "").lower()
                el_cls = [c.lower() for c in (el.get("class") or [])]
                el_text = el.get_text(" ", strip=True).lower()[:200]

                # Match by ID
                if any(k in el_id for k in OVERLAY_IDS):
                    el.decompose()
                    continue

                # Match by class
                if any(k in el_cls for k in OVERLAY_CLASSES):
                    el.decompose()
                    continue

                # Match by text content (for JS-injected overlays)
                if any(p in el_text for p in TEXT_PATTERNS):
                    el.decompose()
                    continue
            except (AttributeError, TypeError):
                pass

    # Remove accept/agree buttons even outside overlay containers
    for btn in soup.find_all(["button", "a"]):
        try:
            btn_text = btn.get_text(strip=True).lower()
            if btn_text in BUTTON_TEXTS:
                btn.decompose()
        except (AttributeError, TypeError):
            pass

    # Remove script/style tags
    for tag_name in ["script", "style"]:
        for el in soup.find_all(tag_name):
            el.decompose()

    return str(soup)

# ── Domain blocklist (low-value sites) ──────────────────────────────────────
BLOCKED_DOMAINS = {
    # Analytics / tracking
    "google-analytics.com", "googletagmanager.com", "analytics.google.com",
    "hotjar.com", "mixpanel.com", "amplitude.com", "segment.io",
    "heap.io", "mouseflow.com", "fullstory.com", "clarity.ms",
    "matomo.org", "piwik.pro", "smartlook.com",
    # Ads
    "doubleclick.net", "googlesyndication.com", "adservice.google.com",
    "pagead2.googlesyndication.com", "adnxs.com", "adskeeper.com",
    "popads.net", "propellerads.com", "hilltopads.com",
    "adcolony.com", "unity3d.com/ads", "inmobi.com",
    # Social widgets (non-content)
    "addthis.com", "sharethis.com", "outbrain.com", "taboola.com",
    "spot.im", "disqus.com", "livefyre.com",
    # SEO spam / content farms
    "zippia.com", "rocketreach.co", "signalhire.com",
    "zoominfo.com", "apollo.io", "hunter.io",
    # Adult / porn aggregators (no useful content for research)
    "netporntube.com",
    # Redirect / shorteners (non-content)
    "t.co", "bit.ly", "tinyurl.com", "ow.ly", "is.gd", "buff.ly",
    "cutt.ly", "shorturl.at",
    # Placeholder / error pages
    "example.com", "example.org", "example.net",
    "localhost", "127.0.0.1",
    # Search engines / meta-sites (appear as results but contain no content)
    "bing.com", "www.bing.com", "m.bing.com",
    "search.yahoo.com", "search.yahoo.co.jp",
    "duckduckgo.com", "html.duckduckgo.com",
    "ask.com", "webcrawler.com",
    # Aggregator / platform pages (generic, not topic-specific)
    "start.ru", "store.steampowered.com",
    "apps.apple.com", "play.google.com", "tv.apple.com",
    "afisha.yandex.ru", "realty.yandex.ru",
    "market.yandex.ru", "travel.yandex.ru",
    "auto.ru", "dzen.ru",
    "e1.ru", "gismeteo.ru",
    "vk.com", "ok.ru",
    "allmovie.com",  # JS-blocked, returns minimal content
    # Generic portals (never contain specific topic info)
    # NOTE: images.yandex.ru is NOT blocked (useful for visual search)
    "mail.ru", "inbox.ru", "list.ru",
    "rambler.ru", "lenta.ru",
    "afisha.yandex.ru", "realty.yandex.ru",
    "market.yandex.ru", "travel.yandex.ru",
    "auto.yandex.ru", "dzen.ru",
    "e1.ru", "gismeteo.ru",
    "vk.com", "ok.ru",
    # Social media / professional networks (JS-blocked, internal routes)
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com",
    "t.me",  # Telegram preview pages
    # Map services (no article content)
    "maps.apple.com", "mapcarta.com",
}

# Visual-specific allowlist (override blocklist for image queries)
VISUAL_ALLOWLIST = {
    "pinterest.com", "pinterest.co.uk", "pinterest.ca",
    "deviantart.com", "artstation.com",
    "flickr.com", "staticflickr.com",
    "tumblr.com", "assets.tumblr.com",
    "reddit.com/i/", "i.redd.it",
    "imgur.com", "i.imgur.com",
    "500px.com", "unsplash.com", "pixabay.com",
}


def is_blocked_domain(url, query_type=None):
    """Check if URL belongs to a blocked domain. Returns True if blocked."""
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except Exception:
        return False
    host = host.lower()
    # Check blocked domains
    for d in BLOCKED_DOMAINS:
        if host == d or host.endswith("." + d):
            # Allow if visual query and domain is in visual allowlist
            if query_type == "visual":
                for a in VISUAL_ALLOWLIST:
                    if host == a or host.endswith("." + a):
                        return False
            return True
    return False


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


# ── JS data extraction from <script> tags ───────────────────────────────────
_SCRIPT_DATA_PATTERNS = [
    re.compile(r'<script\s+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S),
    re.compile(r'<script[^>]*>\s*window\.__[A-Z_]+\s*=\s*(\{.*?\})\s*;?\s*</script>', re.S),
    re.compile(r'<script\s+type="application/ld\+json"[^>]*>(.*?)</script>', re.S),
    re.compile(r'data-react-props="([^"]+)"'),
]


def extract_js_data(html):
    """Extract structured data from <script> tags (SSR data, JSON-LD, etc.)."""
    if not html:
        return {}
    data = {}
    for pattern in _SCRIPT_DATA_PATTERNS:
        for match in pattern.finditer(html):
            raw = match.group(1).strip()
            if not raw or len(raw) < 5:
                continue
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    if "__NEXT_DATA__" in (match.group(0) if match.group(0) else ""):
                        data["next_data"] = parsed
                    elif "ld+json" in (match.group(0) if match.group(0) else ""):
                        data.setdefault("json_ld", []).append(parsed)
                    else:
                        data.update(parsed)
            except (json.JSONDecodeError, ValueError):
                pass
    return data


# ── Full-size image extraction ──────────────────────────────────────────────
_TRACKING_IMG_RE2 = re.compile(r'(?:pixel|track|1x1|spacer|blank|clear\.gif|analytics|badge|doubleclick|gstatic)', re.I)
_FULLSIZE_IMG_MAX = 20


def upgrade_to_fullsize(url, source_url=""):
    """Try common patterns to upgrade a thumbnail URL to full-size.

    Handles: size suffixes, flickr tokens, path segments, CDN subdomains.
    Returns the best-guess full-size URL (may be same as input if no pattern matched).
    """
    if not url:
        return url
    original = url

    # 1. Remove size suffixes: -150x150, -small, -thumb, -1200x630, -thumbnail
    url = re.sub(r'-(?:\d+x\d+|small|thumb|medium|preview|thumbnail|scaled|crop|resize)(\.\w{3,4})$', r'\1', url, flags=re.I)

    # 2. Replace size tokens (flickr, tumblr): _s/_m/_n/_q/_t/_sq → _b/_o
    url = re.sub(r'_(?:s|m|n|q|t|sq)(\.(?:jpg|jpeg|png|webp|gif))$', r'_b\1', url, flags=re.I)

    # 3. Remove path segments: /thumbs/, /preview/, /small/, /medium/
    url = re.sub(r'/(?:thumbs|thumb|preview|small|medium|thumbnail|crop|resize)/', '/', url)

    # 4. Replace CDN subdomains: t. → i., thumbnails. → images.
    url = re.sub(r'^https?://t\.', 'https://i.', url)
    url = re.sub(r'^https?://thumbnails\.', 'https://images.', url)
    url = re.sub(r'^https?://thumb\.', 'https://cdn.', url)

    # 5. Remove query params that limit size: ?w=200&h=200, ?size=small
    url = re.sub(r'[?&](?:w|h|width|height|size|dim|quality|q)=\d+', '', url)
    url = re.sub(r'\?$', '', url)  # Clean trailing ?

    # 6. Site-specific: Imgur (thumbs → images, _b → _o)
    if 'imgur.com' in url:
        url = re.sub(r'/?thumbs/', '/images/', url)
        url = re.sub(r'_b(\.\w+)$', r'_o\1', url)

    # 7. Site-specific: Twitter/X (:orig suffix, strip format params)
    if 'pbs.twimg.com' in url:
        url = re.sub(r'\?format=\w+&name=\w+', '', url)
        if ':orig' not in url:
            url = url + ':orig' if '?' not in url else url

    # 8. Site-specific: Photobucket (thumbs → images)
    if 'photobucket.com' in url:
        url = re.sub(r'/thumbs/', '/images/', url)

    # 9. Site-specific: Flickr (_q → _o for original)
    if 'staticflickr.com' in url or 'live.staticflickr.com' in url:
        url = re.sub(r'_(?:q|sq|t|s|m|n)(\.(?:jpg|jpeg|png))$', r'_o\1', url)

    # 10. Imagus sieve rules (fail-open, domain-precise). Applied last — the
    #     sieve knows the exact fullsize URL for the host; heuristic steps
    #     above are only a fallback when no rule matches.
    try:
        from sieve import apply as _sieve_apply
        sieved = _sieve_apply(url, source_url or url)
        if sieved and sieved != url:
            url = sieved
    except Exception:
        pass

    return url if url != original else original


def extract_fullsize_images(html, base_url=""):
    """Extract full-size image URLs from page HTML using common patterns.

    Sources: og:image, srcset (max size), data-original/lazy-src,
    gallery <a><img> pattern, JSON-LD image field.
    Returns list of deduplicated absolute URLs.
    """
    if not html:
        return []

    urls = []

    # 1. OpenGraph image (most reliable full-size source)
    for m in re.finditer(r'<meta[^>]+(?:property|name)="og:image"[^>]+content="([^"]+)"', html, re.I):
        urls.append(m.group(1))
    for m in re.finditer(r'<meta[^>]+content="([^"]+)"[^>]+(?:property|name)="og:image"', html, re.I):
        urls.append(m.group(1))

    # 2. Gallery pattern: <a href="..."><img (links to full-size)
    for m in re.finditer(r'<a[^>]+href="([^"]+\.(?:jpg|jpeg|png|webp|avif)(?:\?[^"]*)?)"[^>]*>\s*<img', html, re.I):
        urls.append(m.group(1))

    # 3. srcset — extract largest variant
    for m in re.finditer(r'srcset="([^"]+)"', html, re.I):
        parts = m.group(1).split(',')
        best_url = ""
        best_w = 0
        for part in parts:
            part = part.strip()
            tokens = part.split()
            if not tokens:
                continue
            url = tokens[0]
            w_match = re.search(r'(\d+)w', part)
            w = int(w_match.group(1)) if w_match else 0
            if w > best_w:
                best_w = w
                best_url = url
        if best_url:
            urls.append(best_url)

    # 4. data-* lazy-load attributes (expanded set). When several data-*
    #    attrs exist for the same image, hi-res ones (data-hi-res-src,
    #    data-fullsize, data-maxres, data-original, …) must win the later
    #    dedup — so they are appended first (dedup keeps the first occurrence).
    _DATA_ATTR_RE = r'data-(?:src|original|lazy-src|full-src|hi-res-src|bg|poster|image|srcset|load|source|lazy|high-res|hires|retina|full|fullsize|fullsizeurl|max-res|maxres)="([^"]+)"'
    _HIRES_HINTS = ('hi-res', 'high-res', 'hires', 'fullsize', 'full-src',
                    'max-res', 'maxres', 'original', 'retina')
    data_matches = list(re.finditer(_DATA_ATTR_RE, html, re.I))
    data_matches.sort(key=lambda m: 0 if any(h in m.group(0).lower() for h in _HIRES_HINTS) else 1)
    for m in data_matches:
        urls.append(m.group(1))

    # 5. Framework-specific: v-lazy (Vue), [lazyLoad] (Angular), ng-src (AngularJS)
    for m in re.finditer(r'v-lazy\s*=\s*["\'](https?://[^"\']+)["\']', html, re.I):
        urls.append(m.group(1))
    for m in re.finditer(r'\[lazyLoad\]\s*=\s*["\'](https?://[^"\']+)["\']', html, re.I):
        urls.append(m.group(1))
    for m in re.finditer(r'ng-src\s*=\s*["\'](https?://[^"\']+)["\']', html, re.I):
        urls.append(m.group(1))

    # 6. Inline CSS background-image: url(...)
    for m in re.finditer(r'style\s*=\s*"[^"]*url\(["\']?([^)"\']+)["\']?\)', html, re.I):
        u = m.group(1)
        if re.search(r'\.(?:jpg|jpeg|png|webp|gif|avif)', u, re.I):
            urls.append(u)

    # 7. JS string URLs in <script> blocks
    for m in re.finditer(r'<script[^>]*>(.*?)</script>', html, re.I | re.S):
        script = m.group(1)
        for u in re.findall(r'["\'](https?://[^"\']+\.(?:jpg|jpeg|png|gif|webp|avif)(?:\?[^"\']*)?)["\']', script, re.I):
            urls.append(u)
        for u in re.findall(r'\.src\s*=\s*["\'](https?://[^"\']+)["\']', script, re.I):
            if re.search(r'\.(?:jpg|jpeg|png|gif|webp|avif)', u, re.I):
                urls.append(u)

    # 8. JSON-in-data-attribute: data-config='{"image":"..."}'
    for m in re.finditer(r'data-\w+\s*=\s*["\'](\{[^"\']*\})["\']', html, re.I):
        try:
            import json as _json
            data = _json.loads(m.group(1))
            if isinstance(data, dict):
                for v in data.values():
                    if isinstance(v, str) and re.search(r'^https?://.+\.(?:jpg|jpeg|png|webp|gif|avif)', v, re.I):
                        urls.append(v)
        except Exception:
            pass

    # 9. JSON-LD image field
    for m in re.finditer(r'"image"\s*:\s*"(https?://[^"]+)"', html):
        urls.append(m.group(1))

    # 6. <figure> + <a href> pattern (gallery pages)
    for m in re.finditer(r'<figure[^>]*>\s*<a[^>]+href="([^"]+\.(?:jpg|jpeg|png|webp)(?:\?[^"]*)?)"', html, re.I):
        urls.append(m.group(1))

    # 10. Catch-all: any attribute containing image URL
    for m in re.finditer(r'<[^>]+\s(?:\w+-)?\w+\s*=\s*["\']([^"\']+\.(?:jpg|jpeg|png|webp|gif|avif)(?:\?[^"\']*)?)["\']', html, re.I):
        u = m.group(1)
        if u not in urls and re.search(r'^https?://', u, re.I):
            urls.append(u)

    # Resolve relative URLs and deduplicate
    seen = set()
    resolved = []
    parsed_base = urllib.parse.urlparse(base_url) if base_url else None
    for url in urls:
        url = url.strip()
        if not url:
            continue
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/") and parsed_base:
            url = f"{parsed_base.scheme}://{parsed_base.netloc}{url}"
        if url in seen:
            continue
        seen.add(url)
        # Filter tracking pixels
        if _TRACKING_IMG_RE2.search(url):
            continue
        # Filter trash media (icons, animated gifs, svg)
        if re.search(r'\.(?:gif|ico|svg|cur)(?:\?|$)', url, re.I):
            continue
        # Upgrade thumbnail to full-size
        url = upgrade_to_fullsize(url, base_url)
        resolved.append(url)

    # Fallback: regular <img> tags with size hints (if few results so far)
    if len(resolved) < 5:
        for m in re.finditer(r'<img[^>]+src="([^"]+)"[^>]*>', html, re.I):
            img_url = m.group(1)
            tag = m.group(0)
            if img_url.startswith("//"):
                img_url = "https:" + img_url
            elif img_url.startswith("/") and parsed_base:
                img_url = f"{parsed_base.scheme}://{parsed_base.netloc}{img_url}"
            if img_url in seen or not img_url.startswith("http"):
                continue
            # Skip tiny images by width/height attributes
            w = re.search(r'width="(\d+)"', tag, re.I)
            h = re.search(r'height="(\d+)"', tag, re.I)
            if (w and int(w.group(1)) < 50) or (h and int(h.group(1)) < 50):
                continue
            if _TRACKING_IMG_RE2.search(img_url):
                continue
            seen.add(img_url)
            resolved.append(img_url)
            if len(resolved) >= _FULLSIZE_IMG_MAX:
                break

    return resolved[:_FULLSIZE_IMG_MAX]


# ── Deep Search: validation, classification, image preview ──────────────────

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
        
        # Skip tracking pixels and tiny icons
        src_lower = src.lower()
        if any(p in src_lower for p in ["pixel", "track", "1x1", "spacer", "blank", "clear.gif", "analytics", "badge"]):
            continue
        if img.get("width") in ("1", "2") or img.get("height") in ("1", "2"):
            continue
        
        # Handle relative URLs
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/") and domain_base:
            parsed = urllib.parse.urlparse(domain_base)
            if parsed.scheme:
                src = parsed.scheme + "://" + parsed.netloc + src
            else:
                continue
        elif src.startswith("/") and not domain_base:
            continue
        
        if src.startswith("http"):
            urls.append({"src": src, "alt": alt})
            if len(urls) >= max_count:
                break
    
    return urls


# ── Bot-challenge detection (soft Cloudflare/CAPTCHA pages) ──────────────
_BOT_CHALLENGE_RE = re.compile(
    r"(?:captcha|cloudflare|cloudflare-|are you a robot|are you not a robot|verify you are human|verify you are a human|verify your identity|checking your browser|attention required|access is denied|ddg verification|robot challenge|sorry, you have been blocked|please enable javascript)",
    re.I,
)
_SHORT_PAGE_CHARS = 300
_BOT_CHALLENGE_MARKER = "bot_challenge"


def _tag_bot_challenge(items):
    hits = 0
    for item in items:
        blob = " ".join([
            item.get('title', ''),
            item.get('snippet', ''),
            item.get('text', ''),
        ])
        if _BOT_CHALLENGE_RE.search(blob):
            item['status'] = _BOT_CHALLENGE_MARKER
            item['alive'] = False
            item['error'] = 'bot_challenge'
            hits += 1
    return hits


def _parse_retry_after(raw):
    """Parse a Retry-After header (seconds or RFC 7231 HTTP-date) into seconds.

    Returns None when unparseable (caller should not retry).
    """
    if not raw:
        return None
    raw = raw.strip()
    try:
        return max(0, int(raw))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        from datetime import datetime, timezone
        return max(0, int((parsedate_to_datetime(raw) - datetime.now(timezone.utc)).total_seconds()))
    except Exception:
        return None


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
         "proxy_used": False,
         "error": None,
         "body": None,
     }

    session = _get_session()
    if not session:
        result["error"] = "no session"
        return result

    def _proxy_retry(method="head"):
        """One proxy attempt; returns (status_code, body_or_None) or None."""
        if not (USE_PROXY and PROXY_URL):
            return None
        try:
            import curl_cffi
            ps = curl_cffi.requests.Session(
                impersonate=random.choice(IMPERSONATE_POOL),
                proxies={"http": PROXY_URL, "https": PROXY_URL},
                verify=False, timeout=timeout,
            )
            resp = getattr(ps, method)(url, timeout=timeout, allow_redirects=True)
            if resp.status_code < 400:
                return resp.status_code, getattr(resp, "text", "") or None
        except Exception:
            pass
        return None

    def _finalize(raw):
        """Set body/text metrics; returns True when the page counts as alive."""
        result["body"] = raw
        text = re.sub(r"<[^>]+>", " ", raw)
        text = re.sub(r"\s+", " ", text).strip()
        result["text_length"] = len(text)
        result["text_words"] = len(re.findall(r"\w+", text))
        if result["text_length"] < 500 or result["text_words"] < 50:
            result["error"] = "empty or too-small page"
            return False
        result["alive"] = True
        return True

    # Phase 1: HEAD (fast fail for dead/blocked)
    try:
        head_resp = session.head(url, timeout=timeout, allow_redirects=True)
    except Exception as e:
        err = str(e)
        result["error"] = err
        # Dead site (DNS/timeout): proxy GET as last resort (was unreachable)
        if any(k in err.lower() for k in
               ["getaddrinfo", "timeout", "failed to resolve", "name or service"]):
            pr = _proxy_retry("get")
            if pr and pr[1] and not _detect_blocked(pr[1]):
                result["status"] = pr[0]
                if _finalize(pr[1]):
                    result["proxy_used"] = True
        return result

    result["status"] = head_resp.status_code
    result["content_type"] = head_resp.headers.get("content-type", "")[:200]
    cl = head_resp.headers.get("content-length", "0")
    result["content_length"] = int(cl) if cl.isdigit() else 0
    status = result["status"]

    # Binary-media guard: a "webpage" answering with an image/video/audio
    # payload is not HTML (photo hosts 302 image URLs straight to a CDN).
    # Do NOT download megabytes of binary just to fail the text scan.
    ct = (result["content_type"] or "").lower()
    if ct.startswith(("image/", "video/", "audio/")):
        result["error"] = "binary media content, not HTML"
        return result

    # 429 Retry-After backoff (seconds or RFC 7231 HTTP-date), once per run.
    if status == 429:
        raw_ra = head_resp.headers.get("Retry-After", "")
        retry_after = _parse_retry_after(raw_ra)
        if retry_after is not None and retry_after <= timeout:
            time.sleep(min(retry_after, 10))
            try:
                retry_resp = session.head(url, timeout=timeout, allow_redirects=True)
                if retry_resp.status_code < 400:
                    head_resp = retry_resp
                    status = retry_resp.status_code
                    result["status"] = status
                    result["content_type"] = retry_resp.headers.get("content-type", "")[:200]
            except Exception:
                pass

    # Phase 2: hard statuses — proxy GET (actually delivers content), else blocked/dead
    if status in (403, 429, 451, 503):
        pr = _proxy_retry("get")
        if pr and pr[1] and not _detect_blocked(pr[1]) and _finalize(pr[1]):
            result["status"] = pr[0]
            result["proxy_used"] = True
            return result
        if pr:
            # Proxy reachable but still no usable content → genuinely blocked
            result["blocked"] = True
            result["error"] = "blocked (captcha/cloudflare/etc)"
            return result
        if status == 503:
            # Server may have recovered — one direct GET retry after a short delay
            try:
                time.sleep(2)
                retry_resp = session.get(url, timeout=timeout, allow_redirects=True)
                if retry_resp.status_code < 400 and not _detect_blocked(retry_resp.text):
                    result["status"] = retry_resp.status_code
                    result["body"] = retry_resp.text
                    if _finalize(retry_resp.text):
                        return result
            except Exception:
                pass
        result["blocked"] = True
        result["error"] = f"HTTP {status}"
        return result
    elif status in (404, 405, 410, 500, 502, 504) or status >= 400:
        result["error"] = f"HTTP {status}"
        return result

    # Phase 3: 2xx/3xx — GET body
    try:
        body_resp = session.get(url, timeout=timeout, allow_redirects=True)
    except Exception as e:
        result["error"] = str(e)
        return result
    result["status"] = body_resp.status_code
    result["content_type"] = body_resp.headers.get("content-type", "")[:200]
    cl2 = body_resp.headers.get("content-length", "0")
    result["content_length"] = max(result["content_length"], int(cl2) if cl2.isdigit() else 0)
    if result["status"] >= 400:
        result["error"] = f"HTTP {result['status']}"
        return result

    # Binary-media guard on the GET response too (redirect may have landed on
    # a CDN image): skip the HTML text scan for binary payloads.
    get_ct = (result["content_type"] or "").lower()
    if get_ct.startswith(("image/", "video/", "audio/")):
        result["error"] = "binary media content, not HTML"
        return result

    raw = body_resp.text
    if _detect_blocked(raw):
        pr = _proxy_retry("get")
        if pr and pr[1] and not _detect_blocked(pr[1]):
            raw = pr[1]
            result["status"] = pr[0]
            result["blocked"] = False
            result["error"] = None
            result["proxy_used"] = True
        if _detect_blocked(raw):
            result["blocked"] = True
            result["error"] = "blocked (captcha/cloudflare/etc)"
            return result

    _finalize(raw)
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


def content_relevance_score(query, text):
    """Score how relevant a page's text content is to the query.

    Returns 0.0-1.0. Higher = more relevant.
    Requires at least one 2-word phrase match to prevent false positives
    from single-word overlap (e.g., "sara" + "james" in unrelated context).
    """
    if not text:
        return 0.0
    query_words = [w.lower() for w in re.findall(r'\b\w+\b', query) if len(w) > 2]
    if not query_words:
        return 0.0
    text_lower = text.lower()[:10000]

    # Require at least one 2-word phrase match
    has_phrase = False
    for i in range(len(query_words) - 1):
        phrase = f"{query_words[i]} {query_words[i+1]}"
        if phrase in text_lower:
            has_phrase = True
            break
    if not has_phrase and len(query_words) >= 2:
        return 0.0

    # Single word scoring (with phrase requirement above)
    hits = sum(1 for w in query_words if w in text_lower)
    base_score = hits / len(query_words)
    if len(text) < 200:
        base_score *= 0.3
    multi_hit = sum(1 for w in query_words if text_lower.count(w) >= 3)
    base_score += (multi_hit / len(query_words)) * 0.2
    return min(base_score, 1.0)


# ── URL filtering (homepage / search / video) ───────────────────────────────
_HOMEPAGE_PATHS = {"", "/", "home", "index.html", "index.htm"}
_SEARCH_PATTERNS = ("search?q=", "search?s=", "/images/search", "/search/", "/search?")
_VIDEO_DOMAINS = ("youtube.com", "rutube.ru", "rutube", "yandex.ru/video", "dzen.ru/video",
                  "vimeo.com", "tiktok.com")
_VIDEO_PATH_PATTERNS = ("watch?", "view_video.php", "video_", ".mp4", ".avi", ".mov")


def _is_video_url(url):
    """True if the URL points to video content (domain or path pattern)."""
    url_lower = (url or "").lower()
    return (any(d in url_lower for d in _VIDEO_DOMAINS) or
            any(p in url_lower for p in _VIDEO_PATH_PATTERNS))


def _should_filter_url(url, query_type=None):
    """Drop homepages, search result pages, and (for non-video queries) video URLs.

    Also drops known ad/tracker/junk URLs via the precision-first junk filter
    (allowlist-aware). Junk URL filtering never fires for allowed domains.
    """
    try:
        parsed = urllib.parse.urlparse(url or "")
        path = parsed.path.strip("/").lower()
        url_lower = (url or "").lower()
    except Exception:
        return False
    if path in _HOMEPAGE_PATHS or any(p in url_lower for p in _SEARCH_PATTERNS):
        return True
    if query_type != "video" and _is_video_url(url_lower):
        return True
    # Ad/tracker hosts (precision-first, allowlist-aware). Junk-transition
    # rules (forum chrome, login/register) are NOT applied here — search
    # results are content pages, not crawl links; those rules belong in the
    # Level-2 expansion path (see _filter_expansion_url).
    try:
        from junk_filter import is_ad_url
        if is_ad_url(url):
            return True
    except Exception:
        pass
    return False


def _filter_expansion_url(url):
    """Filter Level-2 expansion candidates: ad/tracker hosts + junk transitions.

    Unlike _should_filter_url (search results), expansion candidates come from
    page links where forum chrome (search.php, member.php, wp-admin, viewfull,
    login/register) is genuinely useless.
    """
    try:
        from junk_filter import is_ad_url, should_skip_junk_url
        if is_ad_url(url) or should_skip_junk_url(url):
            return True
    except Exception:
        pass
    return False


def search_deep(query, validate=True, classify=True, max_validate=50,
                timeout_per_url=10, output_format="json",
                query_variants=None, compose=False, query_type=None):
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
        query_type: agent-provided tag only; backend does not branch on it.
    
    Returns:
        dict with validated results, categories, and summary (or markdown string if compose=True)
    """
    import time as _time
    
    start_time = time.time()
    
    # Step 1a: enforce backend-neutral behavior
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

    # Filter blocked domains before validation (saves N network requests)
    all_raw = [item for item in all_raw if not is_blocked_domain(item.get("url", ""), query_type)]

    # Filter homepages, search results, and video URLs (saves validation time)
    _filtered_out = []
    _kept = []
    for item in all_raw:
        if _should_filter_url(item.get("url", ""), query_type):
            _filtered_out.append(item.get("url", ""))
        else:
            _kept.append(item)
    if _filtered_out:
        all_raw = _kept

    if not query_variants and raw_count < max_validate and _QUERY_VARIANTS_MODULE:
        try:
            suggested = _QUERY_VARIANTS_MODULE._suggest_query_variants(query, all_raw, max_variants=3)
            for q in suggested:
                batch = web_search(q, page=1, count=40, region="wt-wt", safe="off").get("results", [])
                for it in batch:
                    u = it.get("url")
                    if u and u not in seen_urls:
                        seen_urls.add(u)
                        all_raw.append(it)
        except Exception as exc:
            print(f"[ddg-search] dynamic query generation failed: {exc}", file=sys.stderr)

    # Video filter again after dynamic variants (catches any new video URLs)
    if query_type != "video":
        _video_kept = []
        _video_filtered = []
        for item in all_raw:
            if _is_video_url(item.get("url", "")):
                _video_filtered.append(item.get("url", ""))
            else:
                _video_kept.append(item)
        if _video_filtered:
            all_raw = _video_kept

    if not all_raw:
        return {"depth": "deep", "error": "No results after dynamic variants", "summary": {"total_raw": 0, "alive": 0, "dead": 0, "classified_categories": 0}, "elapsed": round(time.time() - start_time, 2)}

    raw_count = len(all_raw)

    # Step 2: URL validation (parallel via ThreadPoolExecutor)
    # Domain quarantine: 403/captcha after 2 failures → skip remaining URLs from domain
    validated = []
    alive_count = 0
    dead_count = 0
    blocked_count = 0
    quarantined_count = 0
    domain_fails = {}   # {domain: fail_count}
    quarantined = set() # domains with 2+ blocked failures

    def _base_domain(hostname):
        if not hostname:
            return ""
        parts = hostname.split(".")
        return ".".join(parts[-2:]) if len(parts) > 2 else hostname

    # Pre-scan: separate normal and quarantined URLs
    results_slice = all_raw[:max_validate]
    normal_urls = []
    quarantined_urls = []
    for r in results_slice:
        url = r.get("url", "")
        try:
            host = urllib.parse.urlparse(url).hostname or ""
            dom = _base_domain(host.lower())
        except Exception:
            dom = ""
        if dom in quarantined:
            quarantined_urls.append(r)
        else:
            normal_urls.append(r)

    # Limit quarantined URLs to avoid wasting time
    MAX_DEFERRED = 10
    quarantined_urls = quarantined_urls[:MAX_DEFERRED]

    # Process: normal first, quarantined at the end
    ordered_urls = normal_urls + quarantined_urls
    batch_size = 10

    def _validate_one(item):
        """Validate a single URL — called in thread pool."""
        url = item.get("url", "")
        check = _check_url_live(url, timeout=timeout_per_url)
        return item, check

    for batch_start in range(0, len(ordered_urls), batch_size):
        batch = ordered_urls[batch_start:batch_start + batch_size]
        # Domain quarantine: skip URLs whose domain is already proven blocking us
        batch = [
            r for r in batch
            if _base_domain((urllib.parse.urlparse(r.get("url", "")).hostname or "").lower()) not in quarantined
        ]
        if not batch:
            continue
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(_validate_one, item) for item in batch]
            for future in futures:
                res, check = future.result()
                url = res.get('url', '')

                try:
                    host = urllib.parse.urlparse(url).hostname or ""
                    dom = _base_domain(host.lower())
                except Exception:
                    dom = ""

                if not check['alive']:
                    dead_count += 1
                    status = check.get('status')
                    reason = (check.get('error') or '').lower()
                    # Defer temporarily-unavailable sites (503/timeout/DNS); quarantine hard blocks
                    is_deferred = (status in (503, 504)) or ("timeout" in reason) or ("getaddrinfo" in reason)
                    if not is_deferred and (check.get('blocked') or (status and status in (403, 429, 451))):
                        domain_fails[dom] = domain_fails.get(dom, 0) + 1
                        if domain_fails[dom] >= 2 and dom not in quarantined:
                            quarantined.add(dom)
                            quarantined_count += 1
                    if check.get('blocked') and not is_deferred:
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
                    soup = BeautifulSoup(body_html, 'html.parser')
                    text = soup.get_text(separator=' ', strip=True)
                except Exception:
                    text = re.sub(r'<[^>]+>', ' ', body_html)
                    text = re.sub(r'\s+', ' ', text).strip()

                title = res.get('title', '') or res.get('snippet', '')[:100]
                res_out = dict(res)
                res_out['alive'] = True
                res_out['status'] = check['status']
                res_out['content_type'] = check['content_type']
                res_out['content_length'] = check['content_length']
                res_out['text_length'] = check.get('text_length', len(text))
                res_out['text_words'] = check['text_words']
                res_out['text'] = text[:4000]

                res_out['relevance'] = round(_relevance_score(query, title, text), 2)

                image_count = len(re.findall(r'<img[^>]+src=', body_html))
                res_out['image_count'] = image_count

                if image_count > 0:
                    res_out['image_urls'] = _extract_image_urls(body_html, url, max_count=3)

                if classify:
                    cat, conf = _classify_by_content(url, title, text)
                    res_out['category'] = cat
                    res_out['category_confidence'] = round(conf, 2)

                validated.append(res_out)

    # Step 4: Soft bot-challenge tagging on validated items
    _tag_bot_challenge(validated)
    alive_count = sum(1 for r in validated if r.get('alive'))

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
            "quarantined_domains": quarantined_count,
            "classified_categories": len(categories) if classify else 0,
            "max_validate": max_validate,
            "elapsed_seconds": elapsed,
        },
        "elapsed": elapsed,
    }
    
    # Add top-level flat results (sorted by relevance)
    validated.sort(key=lambda x: x.get("relevance", 0), reverse=True)
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
