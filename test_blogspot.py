"""Test if DDG/DDGS returns blogspot URLs for this query."""
import sys; sys.path.insert(0, "plugins/web-tools/ddg")
import ddg_search as ddg

# Test 1: regular search
r = ddg.web_search("Sara St James free image gallery", count=50)
if r and r.get("results"):
    urls = [item.get("url","") for item in r["results"]]
    blogspot = [u for u in urls if "blogspot" in u]
    print(f"web_search: {len(r['results'])} results, {len(blogspot)} blogspot")
    for u in blogspot[:5]:
        print(f"  {u[:80]}")
else:
    print("web_search: no results")

# Test 2: DDGS directly
try:
    from ddgs import DDGS
    with DDGS() as client:
        extra = client.text("Sara St James free image gallery")
    if extra:
        urls = [item.get("href","") for item in extra]
        blogspot = [u for u in urls if "blogspot" in u]
        print(f"DDGS: {len(extra)} results, {len(blogspot)} blogspot")
        for u in blogspot[:5]:
            print(f"  {u[:80]}")
except Exception as e:
    print(f"DDGS error: {e}")
