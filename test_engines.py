"""Test DDG engine classes directly."""
import sys, time
sys.path.insert(0, "plugins/web-tools/ddg")

from ddgs.engines.duckduckgo import Duckduckgo
from ddgs.engines.yahoo import Yahoo
from ddgs.engines.yandex import Yandex
from ddgs.engines.mojeek import Mojeek

for name, cls in [("Duckduckgo", Duckduckgo), ("Yahoo", Yahoo), ("Yandex", Yandex), ("Mojeek", Mojeek)]:
    t0 = time.time()
    try:
        eng = cls()
        r = eng.search("sara st james", region="wt-wt", safesearch="moderate", page=1)
        count = len(r) if r else 0
        elapsed = time.time() - t0
        print(f"{name}: {count} results ({elapsed:.1f}s)")
        if r:
            href = r[0].get("href", "?")
            print(f"  first: {href[:70]}")
    except Exception as e:
        print(f"{name}: ERROR {e} ({time.time()-t0:.1f}s)")

# Also test direct HTTP to DDG API
print("\n--- Direct DDG API ---")
import urllib.request
try:
    url = "https://html.duckduckgo.com/html/?q=sara+st+james"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    t0 = time.time()
    resp = urllib.request.urlopen(req, timeout=10)
    html = resp.read().decode("utf-8", errors="replace")
    # Count result links
    import re
    results = re.findall(r'class="result__url"[^>]*href="([^"]+)"', html)
    print(f"DDG HTML: {len(results)} results ({time.time()-t0:.1f}s)")
    if results:
        print(f"  first: {results[0][:70]}")
except Exception as e:
    print(f"DDG HTML: ERROR {e} ({time.time()-t0:.1f}s)")
