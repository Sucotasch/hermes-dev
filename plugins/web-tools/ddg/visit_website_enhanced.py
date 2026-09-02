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

import json, re, sys, time, random, os, urllib.parse
import html as _html
import threading
import importlib.util
from bs4 import BeautifulSoup

# curl_cffi imported lazily in _get_session to avoid circular import issues

# Shared URL-hygiene helpers (_common.py lives in the same directory).
_NORMALIZE_URL = None
_STRIP_TRACKING = None
try:
    _cm_path = os.path.join(os.path.dirname(__file__), '_common.py')
    _cm_spec = importlib.util.spec_from_file_location('_common', _cm_path)
    _cm_module = importlib.util.module_from_spec(_cm_spec)
    _cm_spec.loader.exec_module(_cm_module)
    _NORMALIZE_URL = _cm_module.normalize_url
    _STRIP_TRACKING = _cm_module.strip_tracking_params
    _CONSENT_HEADER = _cm_module.consent_cookie_header
except Exception:
    def _normalize_url(u):
        return u
    _NORMALIZE_URL = _normalize_url
    _STRIP_TRACKING = _normalize_url
    _CONSENT_HEADER = lambda url: None

# ── Config ──────────────────────────────────────────────────────────────────
# Proxy resolution mirrors ddg_search (DDG_PROXY env → HTTPS_PROXY/HTTP_PROXY →
# ~/.hermes/proxy.env) so both modules share one default. Orchestrator still
# overrides these per run.
def _read_proxy_config():
    try:
        env = (os.environ.get("DDG_PROXY")
               or os.environ.get("HTTPS_PROXY")
               or os.environ.get("HTTP_PROXY"))
        if env:
            return True, env
        pf = os.path.join(os.path.expanduser("~"), ".hermes", "proxy.env")
        if os.path.exists(pf):
            content = open(pf, "r", encoding="utf-8").read().strip()
            if content and not content.startswith("#"):
                return True, content
    except Exception:
        pass
    return False, "http://127.0.0.1:2080"


USE_PROXY, PROXY_URL = _read_proxy_config()
# TLS verification: disabled by default (preserves legacy behavior; some local
# tunnellers MITM). Set DDG_TLS_VERIFY=1 to enforce certificate checks.
_TLS_VERIFY = os.environ.get("DDG_TLS_VERIFY", "0") == "1"
JINA_URL = "https://r.jina.ai/"
MAX_CHARS = 8000

# ── curl_cffi impersonation ────────────────────────────────────────────────
IMPERSONATE_POOL = ["chrome110", "chrome116", "chrome120", "chrome124"]
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

# ── curl_cffi Session ───────────────────────────────────────────────────────
# Per-thread sessions: curl_cffi Sessions are not thread-safe and the pipeline
# fetches from multiple workers. See ddg_search for the same fix.
_session_local = threading.local()


def _get_session():
    """Get or create a per-thread curl_cffi Session with rotating fingerprint.
    Main session always uses direct connection. Proxy is only used for retry."""
    imp = random.choice(IMPERSONATE_POOL)
    sessions = getattr(_session_local, "sessions", None)
    if sessions is None:
        sessions = _session_local.sessions = {}
    key = imp
    if key not in sessions:
        try:
            import curl_cffi
            sess = curl_cffi.requests.Session(
                impersonate=imp,
                verify=_TLS_VERIFY,
                timeout=25,
            )
            sessions[key] = sess
        except Exception as e:
            print(f"[visit] curl_cffi Session error: {e}", file=sys.stderr)
            return None
    return sessions[key]


def _reset_sessions():
    """Drop cached per-thread sessions (run start: avoid stale cookies)."""
    _session_local.sessions = {}

# ── Fetch with curl_cffi ───────────────────────────────────────────────────
def _fetch(url, referrer=None, cookies=None):
    """
    Fetch URL using curl_cffi with Chrome TLS fingerprint.
    Falls back to httpx, then Jina.
    NO throttle for curl_cffi — proxy handles rotation.
    """
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
    
    # Cookies: explicit cookies win; otherwise pre-set consent/age cookies
    # (WP-5.2 port) so cookie-consent and age-verification walls are answered
    # on the FIRST request instead of serving a gateway stub.
    if cookies:
        extra_headers["Cookie"] = cookies
    else:
        consent = _CONSENT_HEADER(url)
        if consent:
            extra_headers["Cookie"] = consent
    
    # ── Try curl_cffi ──
    session = _get_session()
    if session:
        for attempt in range(3):
            try:
                # Rotate UA on each retry attempt
                if attempt > 0:
                    extra_headers["User-Agent"] = random.choice(UA_POOL)
                resp = session.get(url, headers=extra_headers, allow_redirects=True)
                html = resp.text
                
                if html and len(html) > 100 and not _is_blocked(html):
                    return html
                
                # Blocked or invalid — exponential backoff
                time.sleep(min(1.5 * (2 ** attempt) + random.uniform(0, 0.5), 10))
                
            except Exception as e:
                # DNS circuit breaker: don't retry on DNS failures
                if "getaddrinfo" in str(e).lower() or "name or service not known" in str(e).lower():
                    break
                time.sleep(2)
        
        # ── Proxy retry for blocked/dead sites ──
        if USE_PROXY and PROXY_URL:
            try:
                import curl_cffi
                proxy_session = curl_cffi.requests.Session(
                    impersonate=random.choice(IMPERSONATE_POOL),
                    proxies={"http": PROXY_URL, "https": PROXY_URL},
                    verify=_TLS_VERIFY, timeout=25,
                )
                extra_headers["User-Agent"] = random.choice(UA_POOL)
                proxy_resp = proxy_session.get(url, headers=extra_headers, allow_redirects=True)
                if proxy_resp.status_code < 400 and not _is_blocked(proxy_resp.text):
                    html = proxy_resp.text
                    if html and len(html) > 100 and not _is_blocked(html):
                        return html
            except Exception:
                pass

        # ── Fallback: httpx ──
        return _fetch_httpx(url, extra_headers)
    
    # ── Fallback: curl subprocess ──
    return _fetch_subprocess(url, ua, extra_headers)

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
    if html and len(html) > 100 and not _is_antibot_jina(html):
        return html
    return None


def _fetch_wayback(url):
    """Wayback Machine fallback — return snapshot HTML or None.

    Uses archive.org's /wayback/available API to find the latest snapshot,
    then fetches the plain URL (with toolbar, NOT the ``id_`` variant — the
    ``id_`` URL is unreliable, returning 503). 429 rate-limits are retried
    once with a 3s backoff.
    """
    import urllib.request, json, time as _time
    api = "https://archive.org/wayback/available?url=" + urllib.parse.quote(url, safe="")
    req = urllib.request.Request(api, headers={"User-Agent": "Hermes/2.0"})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            break
        except Exception:
            if attempt == 0:
                _time.sleep(3)
                continue
            return None
    snap = data.get("archived_snapshots", {}).get("closest")
    if snap and snap.get("status") and snap["status"][0] in ("2", "3"):
        snap_url = snap.get("url", "")
        fetch_url = snap_url.replace("http://", "https://")
        if not fetch_url:
            fetch_url = f"https://web.archive.org/web/{url}"
        html = _fetch(fetch_url)
        if html and len(html) > 300:
            return html
    return None


def _is_antibot_jina(body):
    """Fail-open Jina CAPTCHA/Cloudflare challenge detection (agent-reach port)."""
    try:
        from evidence_rank import is_jina_antibot

        return is_jina_antibot(body)
    except Exception:
        return False

# ── Block detection ────────────────────────────────────────────────────────
def _is_blocked(html):
    """Comprehensive block detection — only for hard blocks, NOT soft overlays."""
    if not html or len(html) < 100:
        return True
    text_lower = html.lower()

    # Captcha: only REAL challenge pages, never mere mentions of the word.
    # MediaWiki/WordPress pages carry captcha settings in JS config + a search
    # form; the bare-word rule falsely condemned them (observed: commons.
    # wikimedia.org HTTP 200 classified as blocked).
    if ('hcaptcha' in text_lower and
            ('challenge' in text_lower or '<iframe' in text_lower or 'hcaptcha-widget' in text_lower)):
        return True
    if ('recaptcha' in text_lower and
            ('verify' in text_lower or 'g-recaptcha' in text_lower)):
        return True
    if ('please complete the captcha' in text_lower or 'enter the captcha' in text_lower
            or 'complete the captcha to continue' in text_lower):
        return True
    # 'cdn-cgi' alone is NOT a block: every Cloudflare-served page (rocket
    # loader, __cf_email__, cf-turnstile refs) carries /cdn-cgi/ paths.
    if ('cdn-cgi' in text_lower and
            ('challenge' in text_lower or 'cf-chl' in text_lower)):
        return True

    blocks = [
        ('cf-chl-check', 'cloudflare_challenge'),
        ('checking your browser', 'cloudflare_challenge'),
        ('security check', 'security_check'),
        ('please verify you are human', 'human_verification'),
        ('access denied', 'access_denied'),
        ('403 forbidden', 'access_denied'),
        ('429 too many', 'rate_limited'),
        ('503 service unavailable', 'service_unavailable'),
        ('challenge-platform', 'cloudflare_challenge'),
        ('browser left', 'browser_check'),
        ('attention required', 'attention_required'),
        ('turn on javascript', 'js_required'),
        ('enable cookies', 'cookies_required'),
        ('javascript is disabled', 'js_required'),
        ('enable javascript and then reload', 'js_required'),
        ('you need to enable javascript', 'js_required'),
        ('requires javascript', 'js_required'),
        # Russian regional blocks. Plain 404 texts ('страница не найдена')
        # are NOT blocks and are intentionally absent.
        ('данный контент недоступен', 'regional_block'),
        ('доступ к данной странице ограничен', 'regional_block'),
        ('эта страница недоступна', 'regional_block'),
        ('контент заблокирован', 'regional_block'),
        ('доступ запрещён', 'regional_block'),
        ('доступ закрыт', 'regional_block'),
        ('доступ к информационному ресурсу ограничен', 'regional_block'),
        ('информация на данной странице ограничена', 'regional_block'),
        ('ресурс заблокирован', 'regional_block'),
        ('доступ временно ограничен', 'regional_block'),
        ('доступ приостановлен', 'regional_block'),
    ]

    for pattern, block_type in blocks:
        if pattern in text_lower:
            return True
    return False

def _get_block_type(html):
    """Identify the specific type of block. Only hard blocks.

    Returns a STABLE code agents/pipelines can branch on:
      cloudflare | aws_waf | recaptcha | captcha | login | access_denied |
      rate_limited | service_unavailable | regional_block | js_required |
      cookies_required | unknown
    """
    if not html:
        return "unknown"
    text_lower = html.lower()

    # ── Vendor-specific anti-bot walls (most specific first) ──
    # AWS WAF challenge (IMDB, many .gov/.edu): HTTP 202 + challenge.js
    if any(m in text_lower for m in ("awswaf", "token.awswaf", "challenge.js",
                                     "aws.waf", "awswaf-token")):
        return "aws_waf"
    # Cloudflare: Turnstile, cf-chl, challenge-platform, interstitial
    if any(m in text_lower for m in ("cf-chl-check", "checking your browser",
                                     "challenge-platform", "cf_chl_", "cf-chl-",
                                     "cf-clearance", "turnstile",
                                     "attention required", "just a moment")):
        return "cloudflare"
    # Google reCAPTCHA — only REAL widget/verify pages, never word mentions
    if ("recaptcha" in text_lower and
            ("g-recaptcha" in text_lower or "verify" in text_lower)):
        return "recaptcha"
    if "hcaptcha" in text_lower and ("challenge" in text_lower or "hcaptcha-widget" in text_lower):
        return "captcha"
    if 'captcha' in text_lower:
        return 'captcha'

    # ── Login wall (not anti-bot) ──
    login_hits = sum(1 for m in ("log in", "sign in", "login required",
                                 "please log in", "sign in to continue",
                                 "log in to continue", "you must be logged in")
                     if m in text_lower)
    if login_hits >= 2 and len(html) < 20000:
        return "login"

    # ── Plain status/JS walls ──
    if 'access denied' in text_lower or '403 forbidden' in text_lower:
        return 'access_denied'
    if '429 too many' in text_lower:
        return 'rate_limited'
    if '503 service unavailable' in text_lower:
        return 'service_unavailable'
    if any(m in text_lower for m in ("turn on javascript", "javascript is disabled",
                                     "enable javascript and then reload",
                                     "you need to enable javascript",
                                     "requires javascript", "enable javascript")):
        return "js_required"
    if 'enable cookies' in text_lower:
        return 'cookies_required'
    # Russian regional blocks
    if any(m in text_lower for m in ("данный контент недоступен", "доступ к данной странице ограничен",
                                     "эта страница недоступна", "контент заблокирован",
                                     "доступ запрещён", "доступ закрыт", "ресурс заблокирован",
                                     "доступ временно ограничен", "доступ приостановлен")):
        return "regional_block"
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

                if any(k in el_id for k in OVERLAY_IDS):
                    el.decompose()
                    continue
                if any(k in el_cls for k in OVERLAY_CLASSES):
                    el.decompose()
                    continue
                if any(p in el_text for p in TEXT_PATTERNS):
                    el.decompose()
                    continue
            except (AttributeError, TypeError):
                pass

    for btn in soup.find_all(["button", "a"]):
        try:
            btn_text = btn.get_text(strip=True).lower()
            if btn_text in BUTTON_TEXTS:
                btn.decompose()
        except (AttributeError, TypeError):
            pass

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
                    tag = match.group(0)
                    if "__NEXT_DATA__" in tag:
                        data["next_data"] = parsed
                    elif "ld+json" in tag:
                        data.setdefault("json_ld", []).append(parsed)
                    else:
                        data.update(parsed)
            except (json.JSONDecodeError, ValueError):
                pass
    return data


# ── Full-size image extraction ──────────────────────────────────────────────
_TRACKING_IMG_RE = re.compile(r'(?:pixel|track|1x1|spacer|blank|clear\.gif|analytics|badge|doubleclick|gstatic)', re.I)


def upgrade_to_fullsize(url, source_url=""):
    """Try common patterns to upgrade a thumbnail URL to full-size."""
    if not url:
        return url
    original = url
    url = re.sub(r'-(?:\d+x\d+|small|thumb|medium|preview|thumbnail|scaled|crop|resize)(\.\w{3,4})$', r'\1', url, flags=re.I)
    url = re.sub(r'_(?:s|m|n|q|t|sq)(\.(?:jpg|jpeg|png|webp|gif))$', r'_b\1', url, flags=re.I)
    url = re.sub(r'/(?:thumbs|thumb|preview|small|medium|thumbnail|crop|resize)/', '/', url)
    url = re.sub(r'^https?://t\.', 'https://i.', url)
    url = re.sub(r'^https?://thumbnails\.', 'https://images.', url)
    url = re.sub(r'^https?://thumb\.', 'https://cdn.', url)
    url = re.sub(r'[?&](?:w|h|width|height|size|dim|quality|q)=\d+', '', url)
    url = re.sub(r'\?$', '', url)

    # Site-specific: Imgur (thumbs → images, _b → _o)
    if 'imgur.com' in url:
        url = re.sub(r'/?thumbs/', '/images/', url)
        url = re.sub(r'_b(\.\w+)$', r'_o\1', url)

    # Site-specific: Twitter/X (:orig suffix, strip format params)
    if 'pbs.twimg.com' in url:
        url = re.sub(r'\?format=\w+&name=\w+', '', url)
        if ':orig' not in url:
            url = url + ':orig' if '?' not in url else url

    # Site-specific: Photobucket (thumbs → images)
    if 'photobucket.com' in url:
        url = re.sub(r'/thumbs/', '/images/', url)

    # Site-specific: Flickr (_q → _o for original)
    if 'staticflickr.com' in url or 'live.staticflickr.com' in url:
        url = re.sub(r'_(?:q|sq|t|s|m|n)(\.(?:jpg|jpeg|png))$', r'_o\1', url)

    # Imagus sieve rules (fail-open, domain-precise). Applied last — heuristic
    # steps above are only a fallback when no sieve rule matches.
    try:
        from sieve import apply as _sieve_apply
        sieved = _sieve_apply(url, source_url or url)
        if sieved and sieved != url:
            url = sieved
    except Exception:
        pass

    return url if url != original else original


def extract_fullsize_images(html, base_url=""):
    """Extract full-size image URLs from page HTML using common patterns."""
    if not html:
        return []
    urls = []
    parsed_base = urllib.parse.urlparse(base_url) if base_url else None

    for m in re.finditer(r'<meta[^>]+(?:property|name)="og:image"[^>]+content="([^"]+)"', html, re.I):
        urls.append(m.group(1))
    for m in re.finditer(r'<meta[^>]+content="([^"]+)"[^>]+(?:property|name)="og:image"', html, re.I):
        urls.append(m.group(1))

    for m in re.finditer(r'<a[^>]+href="([^"]+\.(?:jpg|jpeg|png|webp|avif)(?:\?[^"]*)?)"[^>]*>\s*<img', html, re.I):
        urls.append(m.group(1))

    for m in re.finditer(r'srcset="([^"]+)"', html, re.I):
        parts = m.group(1).split(',')
        best_url, best_w = "", 0
        for part in parts:
            part = part.strip()
            tokens = part.split()
            if not tokens:
                continue
            w_match = re.search(r'(\d+)w', part)
            w = int(w_match.group(1)) if w_match else 0
            if w > best_w:
                best_w = w
                best_url = tokens[0]
        if best_url:
            urls.append(best_url)

    # data-* lazy-load attributes. Hi-res attrs (data-hi-res-src, data-fullsize,
    # data-maxres, data-original, …) are appended first so they win the later
    # dedup (dedup keeps the first occurrence).
    _DATA_ATTR_RE = r'data-(?:src|original|lazy-src|full-src|hi-res-src|bg|poster|image|srcset|load|source|lazy|high-res|hires|retina|full|fullsize|fullsizeurl|max-res|maxres)="([^"]+)"'
    _HIRES_HINTS = ('hi-res', 'high-res', 'hires', 'fullsize', 'full-src',
                    'max-res', 'maxres', 'original', 'retina')
    data_matches = list(re.finditer(_DATA_ATTR_RE, html, re.I))
    data_matches.sort(key=lambda m: 0 if any(h in m.group(0).lower() for h in _HIRES_HINTS) else 1)
    for m in data_matches:
        urls.append(m.group(1))

    # Framework-specific: v-lazy (Vue), [lazyLoad] (Angular), ng-src (AngularJS)
    for m in re.finditer(r'v-lazy\s*=\s*["\'](https?://[^"\']+)["\']', html, re.I):
        urls.append(m.group(1))
    for m in re.finditer(r'\[lazyLoad\]\s*=\s*["\'](https?://[^"\']+)["\']', html, re.I):
        urls.append(m.group(1))
    for m in re.finditer(r'ng-src\s*=\s*["\'](https?://[^"\']+)["\']', html, re.I):
        urls.append(m.group(1))

    # Inline CSS background-image: url(...)
    for m in re.finditer(r'style\s*=\s*"[^"]*url\(["\']?([^)"\']+)["\']?\)', html, re.I):
        u = m.group(1)
        if re.search(r'\.(?:jpg|jpeg|png|webp|gif|avif)', u, re.I):
            urls.append(u)

    # JS string URLs in <script> blocks
    for m in re.finditer(r'<script[^>]*>(.*?)</script>', html, re.I | re.S):
        script = m.group(1)
        for u in re.findall(r'["\'](https?://[^"\']+\.(?:jpg|jpeg|png|gif|webp|avif)(?:\?[^"\']*)?)["\']', script, re.I):
            urls.append(u)
        for u in re.findall(r'\.src\s*=\s*["\'](https?://[^"\']+)["\']', script, re.I):
            if re.search(r'\.(?:jpg|jpeg|png|gif|webp|avif)', u, re.I):
                urls.append(u)

    # JSON-in-data-attribute: data-config='{"image":"..."}'
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

    for m in re.finditer(r'"image"\s*:\s*"(https?://[^"]+)"', html):
        urls.append(m.group(1))

    for m in re.finditer(r'<figure[^>]*>\s*<a[^>]+href="([^"]+\.(?:jpg|jpeg|png|webp)(?:\?[^"]*)?)"', html, re.I):
        urls.append(m.group(1))

    # Catch-all: any attribute containing image URL
    for m in re.finditer(r'<[^>]+\s(?:\w+-)?\w+\s*=\s*["\']([^"\']+\.(?:jpg|jpeg|png|webp|gif|avif)(?:\?[^"\']*)?)["\']', html, re.I):
        u = m.group(1)
        if u not in urls and re.search(r'^https?://', u, re.I):
            urls.append(u)

    seen = set()
    resolved = []
    for url in urls:
        # HTML entities in attribute values (…jpg&amp;ssl=1) break both the
        # download and the markdown image link — decode before dedup.
        url = _html.unescape(url.strip())
        if not url:
            continue
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/") and parsed_base:
            url = "%s://%s%s" % (parsed_base.scheme, parsed_base.netloc, url)
        # Drop non-URL placeholders: sites emit data-lazy="false",
        # srcset="false", data-loading="none", … (observed on github.com).
        # After the // and / resolution above, a real URL must be absolute.
        if not url.startswith(("http://", "https://")):
            continue
        # Dedup by NORMALIZED identity (utm_*/fbclid variants of the same
        # image collapse to one) but keep the tracking-stripped URL for output
        # (signed CDN params and their order are preserved).
        key = _NORMALIZE_URL(url)
        if key in seen:
            continue
        seen.add(key)
        url = _STRIP_TRACKING(url)
        if _TRACKING_IMG_RE.search(url):
            continue
        # Filter trash media (icons, animated gifs, svg)
        if re.search(r'\.(?:gif|ico|svg|cur)(?:\?|$)', url, re.I):
            continue
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
                img_url = "%s://%s%s" % (parsed_base.scheme, parsed_base.netloc, img_url)
            img_url = _html.unescape(img_url)
            if not img_url.startswith("http"):
                continue
            if _NORMALIZE_URL(img_url) in seen:
                continue
            seen.add(_NORMALIZE_URL(img_url))
            img_url = _STRIP_TRACKING(img_url)
            w = re.search(r'width="(\d+)"', tag, re.I)
            h = re.search(r'height="(\d+)"', tag, re.I)
            if (w and int(w.group(1)) < 50) or (h and int(h.group(1)) < 50):
                continue
            if _TRACKING_IMG_RE.search(img_url):
                continue
            seen.add(img_url)
            resolved.append(img_url)
            if len(resolved) >= 20:
                break

    return resolved[:20]


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
            # ── Step 2b: Wayback Machine fallback ──
            # Direct + Jina both failed or returned challenge shells. Try the
            # nearest archive.org snapshot (historical content, honestly labeled).
            wb_html = _fetch_wayback(url)
            if wb_html:
                html = wb_html
                source = "wayback"
            else:
                return {"error": "Failed to fetch page via any method", "content": "", "source": "failed", "url": url}
    else:
        source = "direct"
    
    # ── Step 3: Parse structured content ──
    parser = _ContentParser()
    parser.parse(html)
    
    # ── Step 4: Extract text ──
    # Main-content extraction via trafilatura when installed (cleaner text =
    # better relevance scores and less boilerplate wasting the 1500-char/page
    # compact budget). Fail-open: legacy tag-strip fallback otherwise.
    text = ""
    try:
        import trafilatura

        extracted = trafilatura.extract(
            html,
            output_format="txt",
            favor_precision=True,
            include_comments=False,
            include_tables=False,
        )
        if extracted:
            text = re.sub(r'\s+', ' ', extracted).strip()
    except Exception:
        text = ""
    if not text:
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'\s+', ' ', text).strip()
    text = text[:max_chars]

    # Extract full-size images and JS data (zero-cost: already have HTML)
    fullsize = extract_fullsize_images(html, url)
    js_data = extract_js_data(html)

    # Optional: publication date (htmldate, same author as trafilatura). Used by
    # news/historical queries for recency-aware ranking. Fail-open.
    published = ""
    try:
        from htmldate import find_date

        published = find_date(url, html) or ""
    except Exception:
        published = ""

    result = {
        "title": parser.title,
        "headings": parser.headings,
        "links": parser.links[:max_links],
        "images": parser.images[:max_images],
        "content": text,
        "published": published,
        "source": source,
        "url": url,
    }
    if fullsize:
        result["fullsize_images"] = fullsize
    if js_data:
        result["js_data"] = js_data
    return result

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
