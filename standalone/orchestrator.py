# -*- coding: utf-8 -*-
"""Deep research orchestrator — ties backend tools with LLM."""
import sys
import time
import re
from pathlib import Path

# Add backend to path
_BACKEND = Path(__file__).resolve().parent.parent / "plugins" / "web-tools" / "ddg"
sys.path.insert(0, str(_BACKEND))

import ddg_search
import visit_website_enhanced as vwe
from llm_client import classify_query_type, synthesize_answer


def run_deep_research(query, server_url="http://localhost:8888",
                      max_validate=50, verbose=True):
    """Execute full deep research pipeline.

    Args:
        query: user search query
        server_url: llama.cpp server URL
        max_validate: max URLs to validate
        verbose: print progress to terminal

    Returns:
        dict with "report" (markdown), "stats" (metrics), "sources" (list)
    """
    log = lambda msg: print(f"  {msg}", flush=True) if verbose else None
    timings = {}
    start_total = time.time()

    # Step 1: Classify intent
    log("Classifying query intent...")
    t = time.time()
    query_type = classify_query_type(query, server_url)
    timings["classify"] = round(time.time() - t, 1)
    log(f"  query_type: {query_type} ({timings['classify']}s)")

    # Step 2: Multi-query search
    log("Searching...")
    t = time.time()
    all_results = []
    seen_urls = set()
    variants = ddg_search._query_variants_wrapper(query)

    for i, q in enumerate(variants[:4]):
        r = ddg_search.web_search(q, count=50, region="wt-wt", safe="auto")
        if r:
            for item in r.get("results", []):
                u = item.get("url", "")
                if u and u not in seen_urls:
                    seen_urls.add(u)
                    all_results.append(item)
        log(f"  [{i+1}/{min(len(variants),4)}] {q[:50]}... {len(all_results)} URLs")

    timings["search"] = round(time.time() - t, 1)
    log(f"  Total: {len(all_results)} unique URLs ({timings['search']}s)")

    # Step 3: Blocklist filter
    before = len(all_results)
    all_results = [r for r in all_results if not ddg_search.is_blocked_domain(r.get("url", ""))]
    log(f"  After blocklist: {len(all_results)} (-{before - len(all_results)} blocked)")

    # Step 4: Validate URLs
    log("Validating URLs...")
    t = time.time()
    validated = []
    alive_count = 0
    from concurrent.futures import ThreadPoolExecutor

    def validate_one(item):
        check = ddg_search._check_url_live(item.get("url", ""), timeout=5)
        return item, check

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(validate_one, item) for item in all_results[:max_validate]]
        for f in futures:
            item, check = f.result()
            if check.get("alive"):
                alive_count += 1
                body = check.get("body", "")
                text = ""
                if body:
                    try:
                        from bs4 import BeautifulSoup
                        soup = BeautifulSoup(body, "lxml")
                        text = soup.get_text(separator=" ", strip=True)[:5000]
                    except Exception:
                        text = body[:5000]
                item["text"] = text
                item["alive"] = True
                item["text_length"] = check.get("text_length", 0)
                item["relevance"] = ddg_search.content_relevance_score(query, text)
                validated.append(item)

    timings["validate"] = round(time.time() - t, 1)
    log(f"  Alive: {alive_count}/{len(all_results[:max_validate])} ({timings['validate']}s)")

    # Step 5: Rank by relevance
    validated.sort(key=lambda x: x.get("relevance", 0), reverse=True)
    top_pages = validated[:25]

    # Step 6: Image search (if visual)
    images = []
    if query_type == "visual":
        log("Searching images...")
        t = time.time()
        r = ddg_search.image_search(query, count=10)
        if r:
            images = r.get("results", [])[:8]
        timings["images"] = round(time.time() - t, 1)
        log(f"  {len(images)} images ({timings['images']}s)")

    # Step 7: Compact evidence
    evidence = []
    for p in top_pages:
        text = p.get("text", "")
        summary = text.split("\n\n")[0][:500] if text else ""
        evidence.append({
            "url": p.get("url", ""),
            "title": p.get("title", ""),
            "relevance": round(p.get("relevance", 0), 2),
            "summary": summary,
        })

    log(f"  Evidence pack: {len(evidence)} pages")

    # Step 8: LLM synthesis
    log("Synthesizing answer...")
    t = time.time()
    answer = synthesize_answer(query, evidence, query_type, server_url)
    timings["synthesis"] = round(time.time() - t, 1)
    log(f"  Done ({timings['synthesis']}s)")

    total_time = round(time.time() - start_total, 1)
    log(f"\nTotal: {total_time}s")

    return {
        "report": answer,
        "stats": {
            "query": query,
            "query_type": query_type,
            "raw_urls": len(all_results),
            "alive": alive_count,
            "evidence_pages": len(evidence),
            "images": len(images),
            "timings": timings,
            "total_time": total_time,
        },
        "sources": [{"url": e["url"], "title": e["title"], "relevance": e["relevance"]}
                     for e in evidence],
    }
