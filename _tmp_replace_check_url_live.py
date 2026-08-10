# -*- coding: utf-8 -*-
"""Replace _check_url_live in ddg_search.py with the corrected implementation."""
import re
from pathlib import Path

path = Path("plugins/web-tools/ddg/ddg_search.py")
src = path.read_text(encoding="utf-8")

start_marker = "def _check_url_live(url, timeout=10):"
end_marker = "\n\ndef _relevance_score("

start = src.index(start_marker)
end = src.index(end_marker, start)

new_fn = '''def _check_url_live(url, timeout=10):
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
        text = re.sub(r"\\s+", " ", text).strip()
        result["text_length"] = len(text)
        result["text_words"] = len(re.findall(r"\\w+", text))
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

    # Phase 2: hard statuses — proxy retry, else mark blocked/dead
    if status in (403, 429, 451, 503):
        pr = _proxy_retry("get" if status == 503 else "head")
        if pr:
            result["status"] = pr[0]
            result["proxy_used"] = True
            if status == 503 and pr[1]:
                # Proxy GET already returned the body — finalize directly
                if not _detect_blocked(pr[1]) and _finalize(pr[1]):
                    return result
                result["blocked"] = True
                result["error"] = "blocked (captcha/cloudflare/etc)"
                return result
            # 403/429/451 recovered via proxy HEAD → fall through to direct GET below
        elif status == 503:
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
        else:
            # Hard block, proxy failed or disabled → now marked blocked correctly
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
'''

src = src[:start] + new_fn + src[end:]
path.write_text(src, encoding="utf-8")
print("OK: _check_url_live replaced")
