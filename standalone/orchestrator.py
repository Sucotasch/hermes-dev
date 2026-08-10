# -*- coding: utf-8 -*-
"""Deep research orchestrator — full pipeline with page-extracted images."""
import sys
import time
import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

_BACKEND = Path(__file__).resolve().parent.parent / "plugins" / "web-tools" / "ddg"
sys.path.insert(0, str(_BACKEND))

import ddg_search
import visit_website_enhanced as vwe
from llm_client import chat_completion, classify_query_type, enrich_query
from _common import normalize_url as _normalize_url, registrable_domain as _reg_domain


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
        # Skip tiny elements
        text = tag.get_text(strip=True)
        if len(text) < 200:
            continue
        if tag.attrs is None:
            continue

        # Score: text length + paragraph count - link density penalty
        text_len = len(text)
        paragraphs = len(tag.find_all("p"))
        links = len(tag.find_all("a"))
        link_density = links / max(text_len / 100, 1)  # links per 100 chars

        # Bonus for semantic tags
        tag_bonus = 0
        if tag.name == "article":
            tag_bonus = 200
        elif tag.name == "main":
            tag_bonus = 150
        elif tag.name == "section":
            tag_bonus = 50

        # Penalty for noise classes
        classes = [c.lower() for c in (tag.get("class") or [])]
        noise_penalty = 0
        if any(k in classes for k in ["sidebar", "menu", "nav", "footer", "header",
                                       "comment", "social", "share", "related", "recommend"]):
            noise_penalty = 500

        # Penalty for calendar/archive patterns (lists of dates/months)
        archive_penalty = 0
        text_lower = text.lower()
        if re.search(r'(?:archive|calendar|blog archive|◄|►)', text_lower):
            archive_penalty = 300
        if re.search(r'(?:january|february|march|april|may|june|july|august|september|october|november|december)\s*\d{4}', text_lower):
            archive_penalty += 200

        score = text_len + paragraphs * 50 - link_density * 100 + tag_bonus - noise_penalty - archive_penalty
        candidates.append((score, tag))

    if not candidates:
        # Fallback: return all text
        return soup.get_text(separator="\n", strip=True)[:5000]

    # Pick the best candidate
    candidates.sort(key=lambda x: x[0], reverse=True)
    best_tag = candidates[0][1]

    # Remove noise from the best container
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
    # Remove short link-only elements
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


def _has_query_keywords(text, query_str):
    """Check if query core phrase appears in text as consecutive 2+ word phrase.
    Extracts significant words (allowing 2-char words like 'st'), checks 3-word
    then 2-word phrases with word boundary. Single words are NOT matched."""
    if not query_str or not text:
        return False
    # Stop words to exclude
    stop_words = {"the", "and", "for", "with", "from", "that", "this", "are", "was",
                  "has", "had", "have", "not", "but", "can", "will", "all", "any",
                  "free", "image", "gallery", "photo", "photos", "picture", "pictures",
                  "video", "videos", "forum", "site", "web", "online", "best", "top",
                  "new", "old", "more", "very", "just", "about", "also"}
    words = [w.lower() for w in query_str.split()
             if len(w) >= 2 and w.lower() not in stop_words]
    if len(words) < 2:
        return False
    text_lower = text.lower()
    import re
    for n in (3, 2):
        for i in range(len(words) - n + 1):
            phrase = " ".join(words[i:i+n])
            pattern = r'(?<!\w)' + re.escape(phrase) + r'(?!\w)'
            if re.search(pattern, text_lower):
                return True
    return False


# Platform domains: use hostname+path dedup instead of base domain
# These hosts contain thousands of distinct blogs/pages under one domain
_PLATFORM_DOMAINS = {
    "blogspot.com", "blogspot.co.uk", "blogspot.de", "blogspot.fr",
    "wordpress.com", "wordpress.org",
    "livejournal.com", "dreamwidth.org",
    "tumblr.com", "posterous.com",
    "typepad.com", "webnode.com",
    "wixsite.com", "weebly.com",
    "substack.com", "medium.com",
    "github.io", "gitlab.io",
    "forumhouse.ru", "pikabu.ru",
}

# INT-3 fullsize discovery: per-run time budget for resolving thumbnail
# viewer pages (sieve link->url->res) — visual queries only, fail-open.
_FULLSIZE_DISCOVER_BUDGET = 12.0

# Utility/system path tokens never worth expanding into (Level-2 candidates):
# report-abuse, login/register, privacy/terms, feeds, etc.
# NOTE: only unambiguous system tokens. Content words like 'about'/'help'/
# 'press'/'api' were removed — they appear inside legit gallery paths
# (e.g. 'about-bottomless-bikinis') and must never be skipped.
_UTILITY_TOKENS = {
    "login", "signin", "signup", "register", "forgot", "password",
    "account", "settings", "privacy", "terms", "contact", "dmca",
    "sitemap", "faq", "advertise", "newsletter", "disclaimer", "cookie",
    "cookies", "license", "rss", "report", "abuse", "status", "uptime",
}

# Mirror domains: same content on different TLDs
_MIRROR_DOMAINS = {
    "bunkr.fi": "bunkr",
    "bunkr.ci": "bunkr",
    "bunkr.ax": "bunkr",
    "bunkr.si": "bunkr",
}


def _dedup_key(url):
    """Generate dedup key: for platforms use hostname+path, for others use base domain.
    Strips query params (?m=0, ?m=1) and handles mirror domains."""
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        # Handle mirror domains
        if host in _MIRROR_DOMAINS:
            host = _MIRROR_DOMAINS[host]
        for plat in _PLATFORM_DOMAINS:
            if host == plat or host.endswith("." + plat):
                path_parts = (parsed.path or "/").strip("/").split("/")
                first_segment = path_parts[0] if path_parts else ""
                return f"{host}/{first_segment}"
        return _reg_domain(host)
    except Exception:
        return ""


def _is_keyword_soup(url, query):
    """SEO keyword-stuffing detector: path is query words joined by '+'.

    The '+'-join pattern is an SEO-spam signature (bottomless+bikini+pics on
    hundreds of throwaway domains). Real sites never join path words with '+',
    so hyphenated legit URLs are untouched. Needs >=3 path tokens with a
    majority matching query words — false-positive safe.
    """
    try:
        from urllib.parse import urlparse
        path = urlparse(url).path or ""
        if "+" not in path:
            return False
        query_words = {w.lower() for w in re.findall(r"[a-z\u0430-\u044f\u0451]{3,}", query.lower())}
        if len(query_words) < 2:
            return False
        tokens = [t.strip("/ ") for t in path.split("+") if t.strip("/ ")]
        if len(tokens) < 2:
            return False
        hits = sum(1 for t in tokens if t.lower() in query_words)
        if len(tokens) == 2:
            return hits == 2  # strict for 2-token: every token is a query word
        return hits >= max(2, len(tokens) // 2)
    except Exception:
        return False


def _query_variants(query, query_type="general"):
    """Generate query variants based on query type."""
    SUFFIXES = {
        "person": [
            "career biography filmography",
            "free gallery photos portraits",
            "personal life interview",
            "aliases stage names real name",
        ],
        "technical": [
            "github repository source code",
            "documentation guide tutorial",
            "download installation setup",
            "best practices examples",
        ],
        "visual": [
            "free gallery photos portfolio",
            "high resolution original",
            "wallpapers collection",
            "art exhibition showcase",
        ],
        "video": [
            "video sources clips footage",
            "watch online stream full",
            "video archive collection",
            "trailer official release",
        ],
        "historical": [
            "history origins timeline",
            "detailed chronology evolution",
            "archival sources primary",
            "background context facts",
        ],
        "news": [
            "latest news recent",
            "current status today",
            "developments updates",
            "official announcements",
        ],
        "comparison": [
            "vs alternative comparison",
            "pros cons advantages",
            "detailed analysis benchmark",
            "which is better",
        ],
        "fact": [
            "exact answer explanation",
            "scientific evidence source",
            "detailed calculation how",
            "reference data statistics",
        ],
        "art": [
            "gallery exhibition collection",
            "artist biography works",
            "high resolution images",
            "art history analysis",
        ],
        "education": [
            "tutorial course lesson",
            "textbook guide beginner",
            "online course free",
            "academic lecture notes",
        ],
        "science": [
            "research paper study",
            "experiment results findings",
            "scientific explanation how",
            "latest discoveries breakthroughs",
        ],
        "general": [
            "detailed analysis overview",
            "comprehensive guide expert",
            "in-depth review",
            "complete information",
        ],
    }

    suffixes = SUFFIXES.get(query_type, SUFFIXES["general"])
    base = [query]
    for s in suffixes[:3]:
        base.append(f"{query} {s}")
    return base[:5]


def _validate_urls(urls, max_validate=100, verbose=True, log=None, query_type="general", query=""):
    """Validate URLs, return alive pages with relevance scores.
    Domain quarantine: 403/captcha failures → move to end of list (not skip).
    For visual queries: keyword check first, then img_bonus for galleries (15+ imgs)."""
    from urllib.parse import urlparse

    def _base_domain(hostname):
        """Public-suffix aware registrable domain (co.uk etc. kept whole)."""
        return _reg_domain(hostname)

    validated = []
    alive_count = 0
    dead_count = 0
    blocked_count = 0
    deferred_count = 0
    blocked_domains_count = 0
    proxy_success_count = 0
    http_errors = {}
    domain_fails = {}       # {domain: fail_count} for blocked domains
    blocked_domains = set()  # domains with 403/captcha after proxy (skip entirely)
    deferred_domains = set() # domains with 503/timeout (try at end)

    def validate_one(item):
        check = ddg_search._check_url_live(item.get("url", ""), timeout=5)
        return item, check

    def _process_one(item, check):
        """Handle one validation result (shared by main batches and the deferred pass)."""
        nonlocal alive_count, dead_count, blocked_count, deferred_count, \
            proxy_success_count, blocked_domains_count
        url = item.get("url", "")
        short_url = url[:80]
        dom = _base_domain(urlparse(url).hostname)

        if check.get("alive"):
            alive_count += 1
            body = check.get("body", "")
            text = ""
            img_count = 0
            if body:
                try:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(body, "lxml")
                    text = soup.get_text(separator=" ", strip=True)[:8000]
                    if query_type == "visual":
                        img_count = len(soup.find_all("img"))
                except Exception:
                    text = body[:8000]
            item["text"] = text
            item["alive"] = True
            item["text_length"] = check.get("text_length", 0)
            # For visual queries: extract image URLs from validation HTML
            if query_type == "visual" and body:
                item["val_images"] = ddg_search.extract_fullsize_images(body, url)[:20]
            text_rel = ddg_search.content_relevance_score(query, text)
            # Visual img_bonus: only if keywords present AND 15+ images (gallery)
            has_keywords = _has_query_keywords(text, query)
            if query_type == "visual" and has_keywords and img_count >= 15:
                img_bonus = min((img_count - 14) * 0.02, 0.25)
            else:
                img_bonus = 0
            item["relevance"] = min(text_rel + img_bonus, 1.0)
            item["img_count"] = img_count
            if check.get("proxy_used"):
                proxy_success_count += 1
            validated.append(item)
            if log:
                bonus_str = f" +img={img_bonus:.2f}" if img_bonus else ""
                kw_str = " kw=✓" if has_keywords else ""
                proxy_str = " [proxy]" if check.get("proxy_used") else ""
                log(f"    ALIVE [{alive_count}] rel={item['relevance']:.2f} (text={text_rel:.2f}{bonus_str}) imgs={img_count}{kw_str} len={item['text_length']}{proxy_str} {short_url}")
        else:
            reason = check.get("error", "unknown")
            status = check.get("status")
            proxy_attempt = " (proxy failed)" if (ddg_search.USE_PROXY and not check.get("proxy_used")) else ""
            reason_l = reason.lower()
            # Deferred first: 503/timeout/DNS → try again at the end of the run
            is_deferred = (status in (503, 504)) or ("timeout" in reason_l) or ("getaddrinfo" in reason_l)
            is_blocked = (check.get("blocked") or (status and status in (403, 429, 451))) and not is_deferred

            if is_deferred:
                if dom not in deferred_domains and dom not in blocked_domains:
                    deferred_domains.add(dom)
                    deferred_count += 1
                    if log:
                        log(f"    DEFER: {dom} (temporarily unavailable) — moving to end")
                dead_count += 1
                if status:
                    http_errors[status] = http_errors.get(status, 0) + 1
                if log:
                    log(f"    DEAD HTTP {status}{proxy_attempt} (deferred) | {short_url}")
            elif is_blocked:
                domain_fails[dom] = domain_fails.get(dom, 0) + 1
                if domain_fails[dom] >= 2 and dom not in blocked_domains:
                    blocked_domains.add(dom)
                    blocked_domains_count += 1
                    if log:
                        log(f"    BLOCK DOMAIN: {dom} ({domain_fails[dom]} blocks after proxy) — skipping all URLs")
                blocked_count += 1
                if log:
                    log(f"    BLOCKED {reason}{proxy_attempt} | {short_url}")
            elif status:
                http_errors[status] = http_errors.get(status, 0) + 1
                dead_count += 1
                if log:
                    log(f"    DEAD HTTP {status}{proxy_attempt} | {short_url}")
            else:
                dead_count += 1
                if log:
                    log(f"    DEAD {reason}{proxy_attempt} | {short_url}")

    # Domain quarantine: skip URLs from domains already proven blocking us;
    # hold URLs from temporarily-unavailable domains for a final retry pass.
    to_check = urls[:max_validate]
    MAX_DEFERRED = 10
    deferred_pending = []

    def _partition(batch):
        keep, pending = [], []
        for item in batch:
            dom = _base_domain(urlparse(item.get("url", "")).hostname)
            if dom in blocked_domains:
                continue                            # skip entirely — domain blocking us
            elif dom in deferred_domains:
                pending.append(item)                # try again at the end
            else:
                keep.append(item)
        return keep, pending

    batch_size = 10
    for batch_start in range(0, len(to_check), batch_size):
        # Skip if already have enough alive pages
        if alive_count >= max_validate:
            break
        keep, pending = _partition(to_check[batch_start:batch_start + batch_size])
        deferred_pending.extend(pending)
        if not keep:
            continue
        with ThreadPoolExecutor(max_workers=10) as ex:
            futures = [ex.submit(validate_one, item) for item in keep]
            for f in futures:
                if alive_count >= max_validate:
                    break
                item, check = f.result()
                _process_one(item, check)

    # Final pass: deferred domains get one more chance (server may have recovered)
    deferred_pending = deferred_pending[:MAX_DEFERRED]
    if deferred_pending:
        if log:
            log(f"  Deferred: {len(deferred_pending)} URLs to try at end (max {MAX_DEFERRED})")
        for item in deferred_pending:
            if alive_count >= max_validate:
                break
            dom = _base_domain(urlparse(item.get("url", "")).hostname)
            if dom in blocked_domains:
                continue  # domain got quarantined during the run — skip
            item, check = validate_one(item)
            _process_one(item, check)

    if log:
        log(f"  Validation summary: {alive_count} alive, {dead_count} dead, {blocked_count} blocked, {blocked_domains_count} domain-blocked, {deferred_count} deferred")
        if proxy_success_count:
            log(f"  Proxy retries succeeded: {proxy_success_count}")
        if blocked_domains:
            log(f"  Blocked domains (skipped): {', '.join(sorted(blocked_domains))}")
        if deferred_domains:
            log(f"  Deferred domains (tried at end): {', '.join(sorted(deferred_domains))}")
        if http_errors:
            log(f"  HTTP errors: {dict(sorted(http_errors.items()))}")
    return validated, alive_count


def _deep_read_and_extract(pages, top_n=10, query="", verbose=True, log=None, query_type="general", max_imgs_per_page=5):
    """Deep-read pages: fetch full content + extract images from raw HTML.
    Applies content cleaning, relevance filtering, and domain dedup (max 1 per dedup key).
    For visual queries: image count boosts relevance to avoid dropping image-rich pages."""
    deep_pages = []
    all_images = []
    domain_counts = {}
    skipped_dom = 0
    skipped_fetch = 0
    skipped_short = 0
    skipped_relevance = 0
    _deep_read_start = time.monotonic()
    from urllib.parse import urlparse

    for p in pages[:top_n * 3]:
        url = p.get("url", "")
        if not url:
            continue
        key = _dedup_key(url)
        if domain_counts.get(key, 0) >= 1:
            skipped_dom += 1
            if log:
                log(f"    [skip] dedup ({key}): {url[:60]}")
            continue
        if log:
            log(f"    Reading: {url[:70]}...")
        try:
            raw_html = vwe._fetch(url)
            # Jina fallback for JS-heavy sites (Wikipedia, etc.)
            if not raw_html or len(raw_html) < 500:
                try:
                    jina_url = f"https://r.jina.ai/{url}"
                    raw_html = vwe._fetch(jina_url)
                except Exception:
                    pass
            if not raw_html or len(raw_html) < 300:
                skipped_fetch += 1
                if log:
                    log(f"    [skip] fetch failed (len={len(raw_html) if raw_html else 0}): {url[:60]}")
                continue

            imgs = ddg_search.extract_fullsize_images(raw_html, url)
            if query_type == "visual":
                # INT-3: resolve thumbnail-transition links (viewer pages such
                # as imx.to / ag.ru) to fullsize originals via sieve link->url->
                # res. Bounded: per-run time budget, <=3 concurrent fetches,
                # per-URL timeout, and zero network when no `link` rule matches.
                try:
                    from discovery import discover_thumbnails
                    pairs = ddg_search.extract_thumbnail_links(raw_html, url)
                    if pairs:
                        remaining = max(1.0, _FULLSIZE_DISCOVER_BUDGET - (
                            time.monotonic() - _deep_read_start))
                        resolved = discover_thumbnails(pairs, url, budget=remaining)
                        extra = [u for urls in resolved.values() for u in urls]
                        if extra:
                            imgs = list(imgs) + extra
                            if log:
                                log(f"      fullsize discovery: {len(extra)} from {len(pairs)} links")
                except Exception:
                    pass
            text = _extract_main_content(raw_html)
            text = _clean_content(text)

            # Relevance filter: for visual queries, images matter more than text
            text_len = len(text) if text else 0
            img_count = len(imgs)
            is_visual = query_type == "visual"
            has_keywords = _has_query_keywords(text, query)

            # Text threshold: 300 for normal, 50 for visual (image-heavy pages may have little text)
            min_text = 50 if is_visual else 300
            if text_len >= min_text:
                content_score = ddg_search.content_relevance_score(query, text)
                # Visual img_bonus: only if keywords present AND 15+ images (gallery)
                if is_visual and has_keywords and img_count >= 15:
                    img_bonus = min((img_count - 14) * 0.02, 0.25)
                else:
                    img_bonus = 0
                final_score = min(content_score + img_bonus, 1.0)
                # Store the deep-read score so evidence selection (Step 9) and
                # the image gate (Step 8) reuse it instead of re-scoring a
                # truncated 500-char snippet (which produced rel=1.00 → 0.00
                # mismatches in the 2026-08-10 run and dropped good galleries).
                p["deep_score"] = final_score
                # Threshold: 0.15 for normal, 0.05 for visual (keep image-rich pages)
                threshold = 0.05 if is_visual else 0.15
                if final_score < threshold:
                    skipped_relevance += 1
                    if log:
                        log(f"    [skip] low relevance ({final_score:.2f} = text={content_score:.2f}+img={img_bonus:.2f}): {url[:60]}")
                    continue
                p["deep_text"] = text
                p["img_count"] = img_count
                deep_pages.append(p)
                domain_counts[key] = domain_counts.get(key, 0) + 1
                if log:
                    bonus_str = f" +img={img_bonus:.2f}" if img_bonus else ""
                    kw_str = " kw=✓" if has_keywords else ""
                    log(f"    OK [{len(deep_pages)}] rel={final_score:.2f} (text={content_score:.2f}{bonus_str}) imgs={img_count}{kw_str} text={text_len} | {url[:60]}")
                for img_url in (imgs if max_imgs_per_page <= 0 else imgs[:max_imgs_per_page]):
                    all_images.append({
                        "url": img_url,
                        "source_page": url,
                        "source_title": p.get("title", ""),
                    })
            elif is_visual and has_keywords and img_count >= 3:
                # Visual page with keywords but little text — keep if enough images
                p["deep_text"] = text or ""
                p["img_count"] = img_count
                # Text is short here; floor the score so the page survives the
                # evidence/image gates (it was kept for its images, not its text).
                content_score = ddg_search.content_relevance_score(query, text or "")
                p["deep_score"] = min(max(content_score, 0.3), 1.0)
                deep_pages.append(p)
                domain_counts[key] = domain_counts.get(key, 0) + 1
                if log:
                    log(f"    OK [{len(deep_pages)}] visual-only imgs={img_count} kw=✓ text={text_len} | {url[:60]}")
                for img_url in (imgs if max_imgs_per_page <= 0 else imgs[:max_imgs_per_page]):
                    all_images.append({
                        "url": img_url,
                        "source_page": url,
                        "source_title": p.get("title", ""),
                    })
            else:
                skipped_short += 1
                if log:
                    log(f"    [skip] short content ({len(text) if text else 0} chars): {url[:60]}")
        except Exception as e:
            skipped_fetch += 1
            if log:
                import traceback
                log(f"    [skip] error: {e} | {url[:60]}")
                log(f"    traceback: {traceback.format_exc()}")

    if log:
        log(f"  Deep-read summary: {len(deep_pages)} pages read, {len(all_images)} images")
        log(f"  Skipped: {skipped_dom} domain-dedup, {skipped_fetch} fetch-fail, {skipped_short} short, {skipped_relevance} low-relevance")
    return deep_pages, all_images


def _filter_images_for_report(images, log=None):
    """Filter images: skip bad formats, dedup by content hash, enforce minimum size.

    Two-phase download:
    1. Direct download (3s timeout) — fast for accessible images
    2. Proxy retry for failed images — recovers blocked URLs
    """
    import httpx
    from hashlib import md5
    import io
    from concurrent.futures import ThreadPoolExecutor
    import ddg_search

    # Pillow is optional: without it we skip format/hash/size filtering (degraded but functional).
    try:
        from PIL import Image
    except ImportError:
        Image = None

    SKIP_FORMATS = ('.gif', '.svg', '.ico', '.cur', '.bmp', '.tiff')
    MIN_WIDTH, MIN_HEIGHT = 600, 450

    # Precision-first ad/tracker URL filter (allowlist-aware, fail-open):
    # skip downloads of likely ad/pixel images entirely — saves requests and
    # keeps ads out of the visual report.
    try:
        from junk_filter import is_ad_url
        images = [img for img in images if not is_ad_url(img.get("url", ""))]
    except Exception:
        pass

    if Image is None:
        if log:
            log(f"    Image filter: skipped (Pillow not installed) — keeping {len(images)} images")
        return images

    def _is_skippable(url):
        url_lower = url.lower().split('?')[0]
        return any(url_lower.endswith(ext) for ext in SKIP_FORMATS)

    def _try_download(url, proxy=None):
        try:
            resp = httpx.get(url, timeout=3, follow_redirects=True, proxy=proxy)
            if resp.status_code == 200:
                return resp.content
        except:
            pass
        return None

    # Phase 1: Direct download
    quarantine = []
    seen_hashes = set()
    filtered = []

    def _process_phase1(img):
        if _is_skippable(img['url']):
            return None
        content = _try_download(img['url'], proxy=None)
        if content is None:
            return {'img': img, 'quarantine': True}
        try:
            content_hash = md5(content).hexdigest()
            pil_img = Image.open(io.BytesIO(content))
            w, h = pil_img.size
            return {'img': img, 'hash': content_hash, 'width': w, 'height': h, 'quarantine': False}
        except:
            return None

    with ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(_process_phase1, images))

    for r in results:
        if r is None:
            continue
        if r.get('quarantine'):
            quarantine.append(r['img'])
            continue
        if r['hash'] in seen_hashes:
            continue
        if r['width'] < MIN_WIDTH or r['height'] < MIN_HEIGHT:
            continue
        seen_hashes.add(r['hash'])
        r['img']['width'] = r['width']
        r['img']['height'] = r['height']
        filtered.append(r['img'])

    # Phase 2: Proxy retry for quarantined images
    phase2_recovered = 0
    if quarantine and ddg_search.USE_PROXY and ddg_search.PROXY_URL:
        proxy = ddg_search.PROXY_URL

        def _process_phase2(img):
            if _is_skippable(img['url']):
                return None
            content = _try_download(img['url'], proxy=proxy)
            if content is None:
                return None
            try:
                content_hash = md5(content).hexdigest()
                pil_img = Image.open(io.BytesIO(content))
                w, h = pil_img.size
                return {'img': img, 'hash': content_hash, 'width': w, 'height': h}
            except:
                return None

        with ThreadPoolExecutor(max_workers=3) as ex:
            results = list(ex.map(_process_phase2, quarantine))

        for r in results:
            if r is None:
                continue
            if r['hash'] in seen_hashes:
                continue
            if r['width'] < MIN_WIDTH or r['height'] < MIN_HEIGHT:
                continue
            seen_hashes.add(r['hash'])
            r['img']['width'] = r['width']
            r['img']['height'] = r['height']
            filtered.append(r['img'])
            phase2_recovered += 1

    if log:
        log(f"    Image filter: {len(images)} → {len(filtered)} (quarantine: {len(quarantine)}, proxy recovered: {phase2_recovered})")

    return filtered


def run_deep_research(query, server_url="http://localhost:8888",
                      max_validate=100, verbose=True, log=None, model="local",
                      proxy_enabled=False, proxy_url="http://127.0.0.1:2080",
                      top_n=30, images_count=30, llm_sources=20, max_variants=6, max_imgs_per_page=5,
                      search_count=100):
    """Execute full deep research pipeline.

    Args:
        log: callable(str) — if provided, called with every progress message.
             Overrides verbose flag when set.
        model: model name to send to LLM server (default: "local" for llama.cpp).
        proxy_enabled: enable proxy for blocked/dead URLs.
        proxy_url: HTTP proxy URL (default: NECOBOX 127.0.0.1:2080).
    """
    # Apply proxy settings to both backend modules
    ddg_search.USE_PROXY = proxy_enabled
    ddg_search.PROXY_URL = proxy_url if proxy_enabled else None
    ddg_search._reset_sessions()
    vwe.USE_PROXY = proxy_enabled
    vwe.PROXY_URL = proxy_url if proxy_enabled else None
    vwe._reset_sessions()
    if log is None:
        log = lambda msg: print(f"  {msg}", flush=True) if verbose else None
    timings = {}
    start_total = time.time()

    # Step 1: Classify
    log("Classifying intent...")
    t = time.time()
    query_type = classify_query_type(query, server_url, model=model)
    timings["classify"] = round(time.time() - t, 1)
    log(f"  query_type: {query_type} ({timings['classify']}s)")

    # For visual queries: no image limit (user wants all relevant images)
    if query_type == "visual":
        images_count = 0

    # Step 1b: Enrich query with aliases (for person queries)
    enriched_query = query
    if query_type == "person":
        log("Enriching query with aliases...")
        t = time.time()
        enriched_query = enrich_query(query, query_type, server_url, model=model)
        timings["enrich"] = round(time.time() - t, 1)
        if enriched_query != query:
            log(f"  enriched: {enriched_query[:80]} ({timings['enrich']}s)")
        else:
            log(f"  no additional aliases found ({timings['enrich']}s)")

    # Step 2: Multi-query search
    log("Searching...")
    t = time.time()
    all_results = []
    seen_urls = set()
    variants = _query_variants(enriched_query, query_type)

    for i, q in enumerate(variants[:max_variants]):
        r = ddg_search.web_search(q, count=search_count, region="wt-wt", safe="auto")
        variant_urls = []
        if r:
            new = 0
            for item in r.get("results", []):
                u = item.get("url", "")
                # Normalize for dedup: strip tracking params (?m=0, utm_*,
                # fbclid), drop fragment, collapse repeated path segments
                u_clean = _normalize_url(u)
                if u_clean and u_clean not in seen_urls:
                    seen_urls.add(u_clean)
                    all_results.append(item)
                    variant_urls.append(u)
                    new += 1
        if log:
            log(f"  [{i+1}/{len(variants)}] \"{q[:60]}\" → {new} new URLs")
            for j, u in enumerate(variant_urls, 1):
                log(f"    {j}. {u[:90]}")
    timings["search"] = round(time.time() - t, 1)

    # Step 3: Blocklist + homepage + search URL + video filter
    from urllib.parse import urlparse
    before = len(all_results)
    blocked_urls = []
    homepage_urls = []
    search_urls = []
    video_urls = []
    service_urls = []
    spam_urls = []
    kept_results = []
    _HOMEPAGE_PATHS = {"", "/", "home", "index.html", "index.htm"}
    _SEARCH_PATTERNS = ("/search", "/images/search", "search?q=", "search?s=", "/search/")
    _VIDEO_DOMAINS = ("youtube.com", "rutube.ru", "rutube", "yandex.ru/video",
                      "dzen.ru/video", "vimeo.com", "tiktok.com")
    _VIDEO_PATH_PATTERNS = ("watch?", "view_video.php", "video_", ".mp4", ".avi", ".mov")
    _SERVICE_PATHS = ("/feed", "/preload", "/place", "/login", "/signin",
                      "/signup", "/register", "/settings", "/dashboard", "/account")
    for r in all_results:
        url = r.get("url", "")
        if ddg_search.is_blocked_domain(url):
            blocked_urls.append(url)
        else:
            url_lower = url.lower()
            path = urlparse(url).path.strip("/").lower()
            if path in _HOMEPAGE_PATHS:
                homepage_urls.append(url)
            elif any(p in url_lower for p in _SEARCH_PATTERNS):
                search_urls.append(url)
            elif any(p in path for p in _SERVICE_PATHS):
                service_urls.append(url)
            elif _is_keyword_soup(url, query):
                # SEO '+'-keyword stuffing (bottomless+bikini+pics on throwaway
                # domains) — never real content, wastes the validation budget.
                spam_urls.append(url)
            elif query_type != "video" and (
                any(d in url_lower for d in _VIDEO_DOMAINS) or
                any(p in url_lower for p in _VIDEO_PATH_PATTERNS)):
                video_urls.append(url)
            else:
                kept_results.append(r)
    all_results = kept_results
    if log:
        log(f"  After blocklist: {len(all_results)} kept, {len(blocked_urls)} blocked, {len(homepage_urls)} homepages, {len(search_urls)} search-URLs, {len(video_urls)} video, {len(service_urls)} service, {len(spam_urls)} keyword-soup")
        for u in blocked_urls:
            log(f"    BLOCKED: {u[:90]}")
        for u in homepage_urls:
            log(f"    HOMEPAGE: {u[:90]}")
        for u in search_urls:
            log(f"    SEARCH-URL: {u[:90]}")
        for u in video_urls:
            log(f"    VIDEO: {u[:90]}")

    # Step 3b: Filter GettyImages for person queries (wrong person risk)
    if query_type == "person":
        before_ge = len(all_results)
        all_results = [r for r in all_results if "gettyimages.com" not in r.get("url", "")]
        if log and len(all_results) < before_ge:
            log(f"  GettyImages filtered: {before_ge - len(all_results)} removed (person query)")

    # Step 4: Validate
    log(f"Validating {min(len(all_results), max_validate)} URLs...")
    t = time.time()
    validated, alive_count = _validate_urls(all_results, max_validate, verbose, log, query_type=query_type, query=query)
    timings["validate"] = round(time.time() - t, 1)
    log(f"  Alive: {alive_count}/{min(len(all_results), max_validate)} ({timings['validate']}s)")

    # Collect images from validation HTML (visual queries only)
    page_images = []
    if query_type == "visual":
        for p in validated:
            for img_url in p.get("val_images", []):
                page_images.append({"url": img_url, "source_page": p.get("url", ""), "source_title": p.get("title", ""), "from_validation": True})
        if page_images:
            log(f"  Validation images: {len(page_images)} from {len(validated)} pages")

    # Step 5: Rank by relevance
    validated.sort(key=lambda x: x.get("relevance", 0), reverse=True)

    # Step 6: Level 2 expansion
    level2_count = 0
    if alive_count < 20:
        log("Level 2 expansion...")
        t = time.time()
        top_urls = [p["url"] for p in validated[:10] if p.get("url")]
        # Pre-filter candidates by dedup key BEFORE validation: cap at 2 pages
        # per registrable domain so one host (xxgasm case: 15/16 alive links)
        # cannot consume the whole Level-2 budget.
        key_counts = {}
        for p in validated:
            key = _dedup_key(p.get("url", ""))
            key_counts[key] = key_counts.get(key, 0) + 1
        level2_urls = []
        for url in top_urls:
            try:
                page = vwe.visit_website(url, max_chars=5000)
                for link in page.get("links", []):
                    href = link.get("url", "")
                    if href and href not in seen_urls and not ddg_search.is_blocked_domain(href):
                        href_lower = href.lower()
                        if query_type != "video" and (
                            any(d in href_lower for d in _VIDEO_DOMAINS) or
                            any(p in href_lower for p in _VIDEO_PATH_PATTERNS)):
                            continue
                        # Ad/tracker hosts + junk transitions (forum chrome) for
                        # expansion candidates (page links, not search results).
                        try:
                            from junk_filter import is_ad_url, should_skip_junk_url
                            if is_ad_url(href) or should_skip_junk_url(href):
                                continue
                        except Exception:
                            pass
                        # Skip utility/homepage-ish links (report-abuse, login,
                        # feeds, contact…). Token-based AND first-segment-only:
                        # a utility token mid-path (e.g. 'gallery/contact-x')
                        # is never enough to drop a candidate.
                        try:
                            lpath = urlparse(href).path.strip("/").lower()
                            path_tokens = re.split(r"[^a-z0-9]+", lpath)
                            first = path_tokens[0] if path_tokens else ""
                            if lpath in _HOMEPAGE_PATHS or first in _UTILITY_TOKENS:
                                continue
                        except Exception:
                            pass
                        # Dedup-key cap before enqueueing (2 per registrable domain)
                        key = _dedup_key(href)
                        if key_counts.get(key, 0) >= 2:
                            continue
                        key_counts[key] = key_counts.get(key, 0) + 1
                        seen_urls.add(href)
                        level2_urls.append({"url": href, "title": link.get("text", ""), "snippet": ""})
            except Exception:
                pass
        if level2_urls:
            log(f"  {len(level2_urls)} candidates")
            l2_val, l2_alive = _validate_urls(level2_urls[:30], 30, verbose, log, query_type=query_type, query=query)
            # Relevance gate + domain dedup (platform-aware)
            domain_counts = {}
            for p in validated:
                key = _dedup_key(p.get("url", ""))
                domain_counts[key] = domain_counts.get(key, 0) + 1
            for p in l2_val:
                p["relevance"] = ddg_search.content_relevance_score(query, p.get("text", ""))
                if p["relevance"] < 0.15:
                    continue
                key = _dedup_key(p.get("url", ""))
                if domain_counts.get(key, 0) >= 2:
                    continue
                domain_counts[key] = domain_counts.get(key, 0) + 1
                validated.append(p)
            level2_count = len([p for p in l2_val if p.get("relevance", 0) >= 0.2])
            alive_count += l2_alive
            validated.sort(key=lambda x: x.get("relevance", 0), reverse=True)
        timings["level2"] = round(time.time() - t, 1)
        log(f"  +{level2_count} pages ({timings.get('level2', 0)}s)")

    # Step 7: Deep-read + extract images from pages
    # Sort by text length to process content-rich pages first (avoids domain dedup killing better pages)
    validated.sort(key=lambda x: len(x.get("text", "")), reverse=True)
    log("Deep-reading & extracting images from pages...")
    t = time.time()
    deep_pages, deep_images = _deep_read_and_extract(validated, top_n=top_n, query=query, verbose=verbose, log=log, query_type=query_type, max_imgs_per_page=max_imgs_per_page)
    page_images.extend(deep_images)
    timings["deep_read"] = round(time.time() - t, 1)
    log(f"  {len(deep_pages)} pages read, {len(page_images)} images extracted ({timings['deep_read']}s)")

    # Step 8: Deduplicate images (only from relevant pages)
    seen_imgs = set()
    images = []
    relevant_urls = set()
    # For visual queries: include pages with keywords + images
    img_threshold = 0.05 if query_type == "visual" else 0.15
    for p in validated:
        snippet = p.get("snippet", "") or p.get("text", "")[:500]
        deep = p.get("deep_score")
        rel = deep if deep is not None else ddg_search.content_relevance_score(query, snippet)
        img_count = p.get("img_count", 0)
        has_kw = _has_query_keywords(snippet, query)
        # Keep if relevance passes threshold OR (visual + keywords + images)
        if rel >= img_threshold or (query_type == "visual" and has_kw and img_count >= 3):
            relevant_urls.add(p.get("url"))
    img_from_irrelevant = 0
    img_dedup = 0
    for img in page_images:
        if img["source_page"] not in relevant_urls:
            img_from_irrelevant += 1
            continue
        url = ddg_search.upgrade_to_fullsize(img["url"], img.get("source") or img.get("source_page") or "")
        if url in seen_imgs:
            img_dedup += 1
            continue
        seen_imgs.add(url)
        images.append({"url": url, "source": img["source_page"], "title": img["source_title"]})
    if log:
        log(f"  Images: {len(page_images)} raw → {len(images)} unique (filtered: {img_from_irrelevant} from irrelevant pages, {img_dedup} duplicates)")
        for img in images[:10]:
            log(f"    IMG: {img['url'][:80]} from {img['source'][:60]}")
    images = images if images_count <= 0 else images[:images_count]

    # Step 8b: For visual queries — filter by format, dedup by content, check size
    if query_type == "visual" and images:
        before_count = len(images)
        images = _filter_images_for_report(images, log)
        if log:
            log(f"  Image filter: {before_count} → {len(images)} (format/dedup/size)")

    # Step 9: Build evidence — only pages with actual content
    evidence = []
    skipped_evidence = []
    is_visual = query_type == "visual"
    # Thresholds: visual queries are more lenient
    min_text_len = 30 if is_visual else 100
    min_relevance = 0.05 if is_visual else 0.15
    min_images = 3 if is_visual else 0

    for p in validated:
        text = p.get("deep_text") or p.get("text", "")
        url = p.get("url", "")
        img_count = p.get("img_count", 0)
        text_len = len(text) if text else 0

        # Skip if no content AND no images (for visual)
        if text_len < min_text_len and img_count < min_images:
            skipped_evidence.append(f"{url[:60]} (text={text_len} < {min_text_len}, imgs={img_count} < {min_images})")
            continue

        # Skip image URLs as evidence sources (not content pages)
        if re.search(r'\.(jpg|jpeg|png|gif|webp|avif)(?:\?|$)', url, re.I):
            skipped_evidence.append(f"{url[:60]} (image URL, not content)")
            continue

        # Prefer the deep-read score (already includes the gallery img_bonus);
        # only fall back to snippet re-scoring for pages that were never
        # deep-read. Re-scoring a truncated snippet was the cause of the
        # rel=1.00 → 0.00 mismatch (telegra.ph, autoadult) in the 2026-08-10 run.
        deep = p.get("deep_score")
        has_keywords = _has_query_keywords(text, query)
        if deep is not None:
            relevance = round(deep, 2)
            final_relevance = relevance
        else:
            snippet = p.get("snippet", "") or (text[:500] if text else "")
            relevance = round(ddg_search.content_relevance_score(query, snippet), 2)
            # Visual img_bonus: only if keywords present AND 15+ images (gallery)
            if is_visual and has_keywords and img_count >= 15:
                img_bonus = min((img_count - 14) * 0.02, 0.25)
            else:
                img_bonus = 0
            final_relevance = min(relevance + img_bonus, 1.0)

        if final_relevance < min_relevance:
            skipped_evidence.append(f"{url[:60]} (relevance={final_relevance:.2f} < {min_relevance})")
            continue

        evidence.append({
            "url": url,
            "title": p.get("title", ""),
            "relevance": final_relevance,
            "content": text[:4000] if text else "",
            "img_count": img_count,
        })
    if log:
        log(f"  Evidence: {len(evidence)} pages ({sum(len(e['content']) for e in evidence)} chars)")
        log(f"  Evidence selection:")
        for i, e in enumerate(evidence, 1):
            log(f"    [{i}] rel={e['relevance']:.2f} {e['title'][:50]} | {e['url'][:60]}")
        if skipped_evidence:
            log(f"  Skipped from evidence:")
            for s in skipped_evidence[:10]:
                log(f"    {s}")

    # Step 10: LLM synthesis (conclusions only, not full text)
    evidence.sort(key=lambda x: x.get("relevance", 0), reverse=True)
    log("Synthesizing conclusions...")
    t = time.time()

    # Give LLM the evidence content for synthesis
    evidence_content = ""
    for i, e in enumerate(evidence[:llm_sources]):
        content_preview = e['content'][:1500] if e['content'] else ""
        evidence_content += f"\n--- Source {i+1}: {e['title']} ---\n{content_preview}\n"

    synthesis = chat_completion([
        {"role": "system", "content": """You are a research analyst writing a synthesis for a deep research report.

The source articles above contain factual information about the topic. Extract and synthesize ALL relevant facts from the sources into:

1. Executive summary (3-5 sentences with key findings from the sources)
2. Key takeaways (bullet points with specific facts extracted from the sources - names, dates, details, filmography, career milestones)
3. Gaps and limitations (what information is genuinely NOT present in any source)

CRITICAL RULES:
- Extract facts that ARE present in the sources. Do NOT claim information is missing if it appears in any source.
- Use specific details: names, dates, film titles, career facts.
- If a source contains biographical data, include it in the takeaways.
- Only list gaps for information that truly cannot be found in ANY of the provided sources."""},
        {"role": "user", "content": f"Research topic: {query}\nQuery type: {query_type}\n\nSources:\n{evidence_content}\n\nWrite the synthesis section."},
    ], server_url=server_url, temperature=0.3, max_tokens=2000, model=model)

    timings["synthesis"] = round(time.time() - t, 1)
    log(f"  Done ({timings['synthesis']}s)")

    # Step 11: Build final report (articles + images + synthesis)
    log("Building report...")
    total_time = round(time.time() - start_total, 1)
    timings["total"] = total_time
    report = _build_report(query, query_type, evidence, images, synthesis, timings, validated)

    log(f"\nTotal: {total_time}s")

    return {
        "report": report,
        "stats": {
            "query": query,
            "query_type": query_type,
            "raw_urls": len(all_results),
            "alive": alive_count,
            "level2": level2_count,
            "deep_read": len(deep_pages),
            "images": len(images),
            "evidence_pages": len(evidence),
            "timings": timings,
            "total_time": total_time,
        },
        "sources": [{"url": e["url"], "title": e["title"], "relevance": e["relevance"]}
                     for e in evidence],
    }


def _build_report(query, query_type, evidence, images, synthesis, timings, validated=None):
    """Build report: articles + images + LLM synthesis."""
    from datetime import datetime

    parts = []

    # Header
    parts.append(f"# {query}\n")
    parts.append(f"**Query type:** {query_type} | **Sources:** {len(evidence)} | **Images:** {len(images)} | **Time:** {timings.get('total', 0)}s\n")

    # Source articles (full cleaned text)
    parts.append("---\n")
    parts.append("## Sources\n")
    for i, e in enumerate(evidence):
        if e.get("content"):
            parts.append(f"### [{i+1}] {e['title']}")
            parts.append(f"*{e['url']}*\n")
            parts.append(e["content"])
            parts.append("\n---\n")

    # Images
    if images:
        from urllib.parse import quote
        parts.append("## Images\n")
        for img in images:
            img_url = quote(img['url'], safe=":/?&=#%~")
            parts.append(f"![{img.get('title', 'image')}]({img_url})")
        parts.append("")

    # Gallery links for visual queries (manual access to galleries)
    if query_type == "visual" and validated:
        gallery_pages = [p for p in validated if p.get("url") and p.get("img_count", 0) >= 5]
        if gallery_pages:
            parts.append("## Gallery Links\n")
            parts.append("Pages with image galleries (click to open manually):\n")
            for p in gallery_pages:
                url = p.get("url", "")
                title = p.get("title", url[:60])
                img_count = p.get("img_count", 0)
                parts.append(f"- [{title}]({url}) ({img_count} images)")
            parts.append("")

    # LLM synthesis
    parts.append("## Analysis & Conclusions\n")
    parts.append(synthesis or "_No synthesis available_\n")

    # Sources footer
    parts.append("---\n")
    parts.append("## All Sources\n")
    for i, e in enumerate(evidence):
        parts.append(f"{i+1}. [{e['title']}]({e['url']})")

    return "\n".join(parts)
