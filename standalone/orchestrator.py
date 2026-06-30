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


def _deep_read_and_extract(pages, top_n=8, verbose=True, log=None):
    """Deep-read pages: fetch full content + extract images from raw HTML."""
    deep_pages = []
    all_images = []
    for p in pages[:top_n]:
        url = p.get("url", "")
        if not url:
            continue
        if log:
            log(f"    Reading: {url[:60]}...")
        try:
            # Get raw HTML for image extraction
            raw_html = vwe._fetch(url)
            if not raw_html or len(raw_html) < 300:
                continue

            # Extract images from raw HTML
            imgs = ddg_search.extract_fullsize_images(raw_html, url)

            # Parse text content for evidence
            from bs4 import BeautifulSoup
            try:
                soup = BeautifulSoup(raw_html, "lxml")
            except Exception:
                soup = BeautifulSoup(raw_html, "html.parser")
            text = soup.get_text(separator=" ", strip=True)[:10000]

            if text and len(text) > 300:
                p["deep_text"] = text
                deep_pages.append(p)
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
            for p in l2_val:
                p["relevance"] = ddg_search.content_relevance_score(query, p.get("text", ""))
                validated.append(p)
            level2_count = len(l2_val)
            alive_count += l2_alive
            validated.sort(key=lambda x: x.get("relevance", 0), reverse=True)
        timings["level2"] = round(time.time() - t, 1)
        log(f"  +{level2_count} pages ({timings.get('level2', 0)}s)")

    # Step 7: Deep-read + extract images from pages
    log("Deep-reading & extracting images from pages...")
    t = time.time()
    deep_pages, page_images = _deep_read_and_extract(validated[:25], top_n=10, verbose=verbose, log=log)
    timings["deep_read"] = round(time.time() - t, 1)
    log(f"  {len(deep_pages)} pages read, {len(page_images)} images extracted ({timings['deep_read']}s)")

    # Step 8: Deduplicate images
    seen_imgs = set()
    images = []
    for img in page_images:
        url = ddg_search.upgrade_to_fullsize(img["url"])
        if url not in seen_imgs:
            seen_imgs.add(url)
            images.append({"url": url, "source": img["source_page"], "title": img["source_title"]})
    images = images[:15]
    log(f"  {len(images)} unique full-size images")

    # Step 9: Build evidence with FULL deep-read text
    evidence = []
    for p in validated[:25]:
        text = p.get("deep_text") or p.get("text", "")
        evidence.append({
            "url": p.get("url", ""),
            "title": p.get("title", ""),
            "relevance": round(p.get("relevance", 0), 2),
            "content": text[:6000] if text else "",
        })
    log(f"  Evidence: {len(evidence)} pages ({sum(len(e['content']) for e in evidence)} chars)")

    # Step 10: Multi-pass synthesis
    log("Synthesizing...")
    t = time.time()

    # Pass 1: Extract facts from FULL content
    facts_parts = []
    for i, e in enumerate(evidence[:10]):
        facts_parts.append(f"Source [{i+1}] {e['title']}:\n{e['content'][:3000]}")
    facts_context = "\n\n".join(facts_parts)

    facts = chat_completion([
        {"role": "system", "content": "Extract specific facts from research sources. Include names, dates, numbers, titles, career details. Be precise and cite source numbers."},
        {"role": "user", "content": f"Extract key facts about: {query}\n\n{facts_context}"},
    ], server_url=server_url, temperature=0.1, max_tokens=3000)

    # Pass 2: Synthesize with images
    images_text = ""
    if images:
        images_text = "\nAvailable images from pages:\n" + "\n".join(
            f"- ![image]({img['url']})" for img in images[:10]
        )

    source_list = "\n".join(
        f"[{i+1}] [{e['title']}]({e['url']})" for i, e in enumerate(evidence[:15])
    )

    synthesis_prompt = f"""Write a comprehensive research report about: {query}
Query type: {query_type}

Extracted facts from sources:
{facts}

Sources:
{source_list}
{images_text}

Requirements:
1. Executive summary (3-5 sentences with key facts)
2. Detailed analysis using SPECIFIC facts from the extracted data (names, dates, numbers, titles)
3. Include ALL relevant images using ![description](URL) markdown syntax
4. Key takeaways with specific evidence
5. Sources section with [N] [Title](URL) clickable links

CRITICAL RULES:
- Use ONLY facts from the extracted data above, do not fabricate
- Every claim must cite source number [N]
- Include actual image URLs from the Available images section
- Write in the same language as the query
- Be specific: names, dates, numbers, film titles — not generalities"""

    answer = chat_completion([
        {"role": "system", "content": "You are an expert researcher. Write detailed reports using ONLY provided facts. Always include images when available."},
        {"role": "user", "content": synthesis_prompt},
    ], server_url=server_url, temperature=0.3, max_tokens=5000)

    timings["synthesis"] = round(time.time() - t, 1)
    log(f"  Done ({timings['synthesis']}s)")

    total_time = round(time.time() - start_total, 1)
    log(f"\nTotal: {total_time}s")

    return {
        "report": answer or "_Error: synthesis failed_",
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
