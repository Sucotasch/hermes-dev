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


def _validate_urls(urls, max_validate=100, verbose=True, log=None):
    """Validate URLs, return alive pages with relevance scores."""
    validated = []
    alive_count = 0

    def validate_one(item):
        check = ddg_search._check_url_live(item.get("url", ""), timeout=5)
        return item, check

    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = [ex.submit(validate_one, item) for item in urls[:max_validate]]
        done = 0
        for f in futures:
            done += 1
            if done % 20 == 0 and log:
                log(f"  ...{done}/{min(len(urls), max_validate)} ({alive_count} alive)")
            item, check = f.result()
            if check.get("alive"):
                alive_count += 1
                body = check.get("body", "")
                text = ""
                if body:
                    try:
                        from bs4 import BeautifulSoup
                        soup = BeautifulSoup(body, "lxml")
                        text = soup.get_text(separator=" ", strip=True)[:8000]
                    except Exception:
                        text = body[:8000]
                item["text"] = text
                item["alive"] = True
                item["text_length"] = check.get("text_length", 0)
                item["relevance"] = ddg_search.content_relevance_score("", text)
                validated.append(item)
    return validated, alive_count


def _deep_read_and_extract(pages, top_n=10, query="", verbose=True, log=None):
    """Deep-read pages: fetch full content + extract images from raw HTML.
    Applies content cleaning, relevance filtering, and domain dedup (max 2 per domain)."""
    deep_pages = []
    all_images = []
    domain_counts = {}
    from urllib.parse import urlparse

    def _base_domain(hostname):
        """Extract base domain: en.kinorium.com -> kinorium.com"""
        if not hostname:
            return ""
        parts = hostname.split(".")
        if len(parts) > 2:
            return ".".join(parts[-2:])
        return hostname

    for p in pages[:top_n * 3]:
        url = p.get("url", "")
        if not url:
            continue
        dom = _base_domain(urlparse(url).hostname)
        if domain_counts.get(dom, 0) >= 1:
            continue
        if log:
            log(f"    Reading: {url[:60]}...")
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
                continue

            imgs = ddg_search.extract_fullsize_images(raw_html, url)

            # Extract main content using Readability-style algorithm
            text = _extract_main_content(raw_html)
            text = _clean_content(text)

            # Relevance filter: skip pages with no useful content
            if text and len(text) > 300:
                content_score = ddg_search.content_relevance_score(query, text)
                if content_score < 0.15:
                    if log:
                        log(f"    [skip] low relevance ({content_score:.2f}): {url[:50]}")
                    continue
                p["deep_text"] = text
                deep_pages.append(p)
                domain_counts[dom] = domain_counts.get(dom, 0) + 1
                for img_url in imgs[:5]:
                    all_images.append({
                        "url": img_url,
                        "source_page": url,
                        "source_title": p.get("title", ""),
                    })
        except Exception:
            pass
    return deep_pages, all_images


def run_deep_research(query, server_url="http://localhost:8888",
                      max_validate=100, verbose=True):
    """Execute full deep research pipeline."""
    log = lambda msg: print(f"  {msg}", flush=True) if verbose else None
    timings = {}
    start_total = time.time()

    # Step 1: Classify
    log("Classifying intent...")
    t = time.time()
    query_type = classify_query_type(query, server_url)
    timings["classify"] = round(time.time() - t, 1)
    log(f"  query_type: {query_type} ({timings['classify']}s)")

    # Step 1b: Enrich query with aliases (for person queries)
    enriched_query = query
    if query_type == "person":
        log("Enriching query with aliases...")
        t = time.time()
        enriched_query = enrich_query(query, query_type, server_url)
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

    for i, q in enumerate(variants[:6]):
        r = ddg_search.web_search(q, count=50, region="wt-wt", safe="auto")
        if r:
            new = 0
            for item in r.get("results", []):
                u = item.get("url", "")
                if u and u not in seen_urls:
                    seen_urls.add(u)
                    all_results.append(item)
                    new += 1
        if log:
            log(f"  [{i+1}/{min(len(variants),6)}] {q[:50]}... +{new} ({len(all_results)} total)")
    timings["search"] = round(time.time() - t, 1)

    # Step 3: Blocklist
    before = len(all_results)
    all_results = [r for r in all_results if not ddg_search.is_blocked_domain(r.get("url", ""))]
    if log:
        log(f"  After blocklist: {len(all_results)} (-{before - len(all_results)})")

    # Step 4: Validate
    log(f"Validating {min(len(all_results), max_validate)} URLs...")
    t = time.time()
    validated, alive_count = _validate_urls(all_results, max_validate, verbose, log)
    timings["validate"] = round(time.time() - t, 1)
    log(f"  Alive: {alive_count}/{min(len(all_results), max_validate)} ({timings['validate']}s)")

    # Step 5: Rank by relevance
    validated.sort(key=lambda x: x.get("relevance", 0), reverse=True)

    # Step 6: Level 2 expansion
    level2_count = 0
    if alive_count < 20:
        log("Level 2 expansion...")
        t = time.time()
        top_urls = [p["url"] for p in validated[:10] if p.get("url")]
        level2_urls = []
        for url in top_urls:
            try:
                page = vwe.visit_website(url, max_chars=5000)
                for link in page.get("links", []):
                    href = link.get("url", "")
                    if href and href not in seen_urls and not ddg_search.is_blocked_domain(href):
                        seen_urls.add(href)
                        level2_urls.append({"url": href, "title": link.get("text", ""), "snippet": ""})
            except Exception:
                pass
        if level2_urls:
            log(f"  {len(level2_urls)} candidates")
            l2_val, l2_alive = _validate_urls(level2_urls[:30], 30, verbose, log)
            # Relevance gate + domain dedup (base domain)
            domain_counts = {}
            def _base_domain(h):
                if not h: return ""
                parts = h.split(".")
                return ".".join(parts[-2:]) if len(parts) > 2 else h
            for p in validated:
                from urllib.parse import urlparse
                dom = _base_domain(urlparse(p.get("url", "")).hostname)
                domain_counts[dom] = domain_counts.get(dom, 0) + 1
            for p in l2_val:
                p["relevance"] = ddg_search.content_relevance_score(query, p.get("text", ""))
                if p["relevance"] < 0.15:
                    continue
                dom = _base_domain(urlparse(p.get("url", "")).hostname)
                if domain_counts.get(dom, 0) >= 2:
                    continue
                domain_counts[dom] = domain_counts.get(dom, 0) + 1
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
    deep_pages, page_images = _deep_read_and_extract(validated, top_n=20, query=query, verbose=verbose, log=log)
    timings["deep_read"] = round(time.time() - t, 1)
    log(f"  {len(deep_pages)} pages read, {len(page_images)} images extracted ({timings['deep_read']}s)")

    # Step 8: Deduplicate images (only from relevant pages)
    seen_imgs = set()
    images = []
    relevant_urls = set()
    for p in validated:
        snippet = p.get("snippet", "") or p.get("text", "")[:500]
        if ddg_search.content_relevance_score(query, snippet) >= 0.15:
            relevant_urls.add(p.get("url"))
    for img in page_images:
        if img["source_page"] not in relevant_urls:
            continue
        url = ddg_search.upgrade_to_fullsize(img["url"])
        if url not in seen_imgs:
            seen_imgs.add(url)
            images.append({"url": url, "source": img["source_page"], "title": img["source_title"]})
    images = images[:10]
    log(f"  {len(images)} unique full-size images")

    # Step 9: Build evidence — only pages with actual content
    evidence = []
    for p in validated:
        text = p.get("deep_text") or p.get("text", "")
        if not text or len(text) < 100:
            continue
        snippet = p.get("snippet", "") or text[:500]
        relevance = round(ddg_search.content_relevance_score(query, snippet), 2)
        if relevance < 0.15:
            continue
        evidence.append({
            "url": p.get("url", ""),
            "title": p.get("title", ""),
            "relevance": relevance,
            "content": text[:4000],
        })
    log(f"  Evidence: {len(evidence)} pages ({sum(len(e['content']) for e in evidence)} chars)")

    # Step 10: LLM synthesis (conclusions only, not full text)
    log("Synthesizing conclusions...")
    t = time.time()

    # Give LLM the evidence content for synthesis
    evidence_content = ""
    for i, e in enumerate(evidence[:10]):
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
    ], server_url=server_url, temperature=0.3, max_tokens=2000)

    timings["synthesis"] = round(time.time() - t, 1)
    log(f"  Done ({timings['synthesis']}s)")

    # Step 11: Build final report (articles + images + synthesis)
    log("Building report...")
    report = _build_report(query, query_type, evidence, images, synthesis, timings)

    total_time = round(time.time() - start_total, 1)
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


def _build_report(query, query_type, evidence, images, synthesis, timings):
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
        parts.append("## Images\n")
        for img in images[:12]:
            parts.append(f"![{img.get('title', 'image')}]({img['url']})")
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
