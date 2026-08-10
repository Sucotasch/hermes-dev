"""Test each search strategy independently."""
import sys, time
sys.path.insert(0, "plugins/web-tools/ddg")
import ddg_search as ddg

QUERY = "sara st james"
STRATEGIES = [
    ("duckduckgo", lambda: ddg._search_ddg(QUERY, 1, 10, "wt-wt", "auto")),
    ("jina-ddg", lambda: ddg._search_jina_ddg(QUERY, 1, 10, "wt-wt", "auto")),
    ("searxng", lambda: ddg._search_searx(QUERY, 1, 10)),
    ("jina-duckduckgo", lambda: ddg._search_jina(QUERY, 1, 10)),
    ("ddgs_text", lambda: ddg._search_ddgs(QUERY, 10)),
    ("ddgs_library", None),  # test DDGS() directly
]

for name, fn in STRATEGIES:
    print(f"\n--- {name} ---")
    t0 = time.time()
    try:
        if name == "ddgs_library":
            from ddgs import DDGS
            with DDGS() as client:
                results = client.text(QUERY)
            count = len(results) if results else 0
            print(f"  DDGS().text() → {count} results ({time.time()-t0:.1f}s)")
            if results:
                for r in results[:3]:
                    print(f"    {r.get('href', r.get('url', 'N/A'))[:80]}")
        else:
            r = fn()
            elapsed = time.time() - t0
            if r and r.get("results"):
                print(f"  OK: {len(r['results'])} results ({elapsed:.1f}s)")
                for item in r["results"][:3]:
                    print(f"    {item.get('url', 'N/A')[:80]}")
            else:
                print(f"  NO RESULTS ({elapsed:.1f}s) raw={r}")
    except Exception as e:
        print(f"  ERROR: {e} ({time.time()-t0:.1f}s)")
