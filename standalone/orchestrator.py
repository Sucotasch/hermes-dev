# -*- coding: utf-8 -*-
"""Deep research orchestrator — full pipeline with Level 2, deep-read, multi-pass synthesis."""
import sys
import time
import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

_BACKEND = Path(__file__).resolve().parent.parent / "plugins" / "web-tools" / "ddg"
sys.path.insert(0, str(_BACKEND))

import ddg_search
import visit_website_enhanced as vwe
from llm_client import chat_completion, classify_query_type


def _query_variants(query):
    """Generate query variants."""
    try:
        import query_variants
        generated = query_variants.generate(query)
        if generated:
            return generated
    except Exception:
        pass
    base = [query]
    tokens = [t for t in re.findall(r'\b\w+\b', query.lower()) if len(t) > 3]
    for t in tokens[:3]:
        base += [f'{t} detailed analysis', f'{t} expert overview', f'{t} comprehensive guide']
    return base[:6]


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


def _deep_read_top_pages(pages, query, top_n=5, verbose=True, log=None):
    """Fetch full content from top pages for deeper analysis."""
    deep_pages = []
    for p in pages[:top_n]:
        url = p.get("url", "")
        if not url:
            continue
        if log:
            log(f"    Deep-read: {url[:60]}...")
        try:
            result = vwe.visit_website(url, max_chars=10000)
            content = result.get("content", "")
            if content and len(content) > 500:
                p["deep_text"] = content[:10000]
                deep_pages.append(p)
        except Exception:
            pass
    return deep_pages


def run_deep_research(query, server_url="http://localhost:8888",
                      max_validate=100, verbose=True):
    """Execute full deep research pipeline with Level 2 and multi-pass synthesis."""
    log = lambda msg: print(f"  {msg}", flush=True) if verbose else None
    timings = {}
    start_total = time.time()

    # Step 1: Classify
    log("Classifying intent...")
    t = time.time()
    query_type = classify_query_type(query, server_url)
    timings["classify"] = round(time.time() - t, 1)
    log(f"  query_type: {query_type} ({timings['classify']}s)")

    # Step 2: Multi-query search (6 variants for deep coverage)
    log("Searching (6 variants)...")
    t = time.time()
    all_results = []
    seen_urls = set()
    variants = _query_variants(query)

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

    # Step 4: Validate (100 URLs)
    log(f"Validating {min(len(all_results), max_validate)} URLs...")
    t = time.time()
    validated, alive_count = _validate_urls(all_results, max_validate, verbose, log)
    timings["validate"] = round(time.time() - t, 1)
    log(f"  Alive: {alive_count}/{min(len(all_results), max_validate)} ({timings['validate']}s)")

    # Step 5: Rank by relevance
    validated.sort(key=lambda x: x.get("relevance", 0), reverse=True)

    # Step 6: Level 2 expansion (if alive < 20)
    level2_count = 0
    if alive_count < 20:
        log("Level 2 expansion (alive < 20)...")
        t = time.time()
        top_urls = [p["url"] for p in validated[:10] if p.get("url")]
        level2_urls = []
        for url in top_urls:
            try:
                page = vwe.visit_website(url, max_chars=5000)
                links = page.get("links", [])
                for link in links:
                    href = link.get("url", "")
                    if href and href not in seen_urls and ddg_search.content_relevance_score(query, href + " " + link.get("text", "")) > 0.1:
                        seen_urls.add(href)
                        level2_urls.append({"url": href, "title": link.get("text", ""), "snippet": ""})
            except Exception:
                pass

        if level2_urls:
            log(f"  {len(level2_urls)} Level 2 candidates")
            l2_validated, l2_alive = _validate_urls(level2_urls[:30], 30, verbose, log)
            for p in l2_validated:
                p["relevance"] = ddg_search.content_relevance_score(query, p.get("text", ""))
                validated.append(p)
            level2_count = len(l2_validated)
            alive_count += l2_alive
            validated.sort(key=lambda x: x.get("relevance", 0), reverse=True)
        timings["level2"] = round(time.time() - t, 1)
        log(f"  Level 2: +{level2_count} pages ({timings.get('level2', 0)}s)")

    # Step 7: Deep-read top pages
    log("Deep-reading top pages...")
    t = time.time()
    top_pages = validated[:25]
    deep_pages = _deep_read_top_pages(top_pages, query, top_n=8, verbose=verbose, log=log)
    timings["deep_read"] = round(time.time() - t, 1)
    log(f"  {len(deep_pages)} pages deep-read ({timings['deep_read']}s)")

    # Step 8: Image search (for visual AND person queries)
    images = []
    if query_type in ("visual", "person"):
        log("Searching images...")
        t = time.time()
        r = ddg_search.image_search(query, count=10)
        if r:
            for img in r.get("results", [])[:8]:
                url = img.get("url", "")
                if url:
                    images.append({
                        "url": url,
                        "title": img.get("title", ""),
                        "thumbnail": img.get("thumbnail", ""),
                    })
        timings["images"] = round(time.time() - t, 1)
        log(f"  {len(images)} image sources ({timings['images']}s)")

    # Step 9: Build evidence pack (use deep_text if available, else summary)
    evidence = []
    for p in validated[:25]:
        text = p.get("deep_text") or p.get("text", "")
        summary = text.split("\n\n")[0][:800] if text else ""
        evidence.append({
            "url": p.get("url", ""),
            "title": p.get("title", ""),
            "relevance": round(p.get("relevance", 0), 2),
            "summary": summary,
        })
    log(f"  Evidence pack: {len(evidence)} pages")

    # Step 10: Multi-pass synthesis
    log("Synthesizing (multi-pass)...")
    t = time.time()

    # Pass 1: Extract key facts from evidence
    facts_prompt = f"""Analyze these research sources about: {query}

For each source, extract 2-3 key facts with specific details (names, dates, numbers, technical specifics).

Sources:
"""
    for i, e in enumerate(evidence[:15]):
        facts_prompt += f"\n[{i+1}] {e['title']} ({e['relevance']:.0%})\n{e['summary']}\n"

    facts = chat_completion([
        {"role": "system", "content": "Extract key facts from research sources. Be specific with names, dates, numbers. Output as structured list."},
        {"role": "user", "content": facts_prompt},
    ], server_url=server_url, temperature=0.1, max_tokens=2000)

    # Pass 2: Synthesize comprehensive answer
    evidence_urls = "\n".join(
        f"[{i+1}] {e.get('title', '')} — {e.get('url', '')}"
        for i, e in enumerate(evidence[:15])
    )
    images_text = ""
    if images:
        images_text = "\nImage sources (pages with photos/portraits):\n" + "\n".join(
            f"- [{img.get('title', 'Image')}]({img.get('url', '')})"
            for img in images[:8]
        )
    synthesis_prompt = f"""You are an expert researcher writing a comprehensive report on: {query}
Query type: {query_type}

Key facts extracted from sources:
{facts}

Available sources with URLs:
{evidence_urls}
{images_text}

Write a detailed, expert-level report with:
1. Executive summary (3-5 sentences)
2. Detailed analysis with specific facts, names, dates, numbers
3. Technical details where relevant
4. Comparison/evaluation if applicable
5. Key takeaways
6. Sources section with clickable links

Rules:
- Start with the answer, not the process
- Use inline citations [N] for every claim
- Include specific technical details, not generalities
- If information is insufficient, state what's missing
- Write in the same language as the query
- Format as clean Markdown with headers
- In Sources section, format as: [N] [Title](URL) — summary
- For person topics, include an "Image Sources" section with clickable links to photo/portrait pages
- When image sources are provided, list them as links in a dedicated section"""

    answer = chat_completion([
        {"role": "system", "content": "You are an expert deep research analyst. Write comprehensive, detailed reports with specific facts and inline citations."},
        {"role": "user", "content": synthesis_prompt},
    ], server_url=server_url, temperature=0.3, max_tokens=4000)

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
            "evidence_pages": len(evidence),
            "images": len(images),
            "timings": timings,
            "total_time": total_time,
        },
        "sources": [{"url": e["url"], "title": e["title"], "relevance": e["relevance"]}
                     for e in evidence],
    }
