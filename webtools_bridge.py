"""Runtime bridge v2: exposes the Hermes web-tools as plain Python primitives.

v2 improvements over v1:
  - CURL_CA_BUNDLE → certifi CA (silences curl_cffi cert-store stderr noise)
  - Disk cache for read (url, 6 h) and search (query, 1 h)
  - Wayback Machine fallback: when direct+jina fail, fetch from archive.org
  - --proxy flag: enable NecoBox 127.0.0.1:2080 retry for the whole call
  - --impersonate flag: use newest Chrome/Safari TLS fingerprints
  - --no-cache / --no-wayback toggles
  - Unique default --out (timestamp + pid) per call
  - source_query filled from the original query in search results
  - New subcommand "expand" — Level-2 hyperlink expansion (wrapper _safe_expand_and_fetch)

Used by the DeepSeek Harness host agent to SEARCH, READ, and IMAGE-SEARCH the
web without a paid API. The Hermes wrapper is loaded with a no-op registry stub
so it imports under the harness without the Hermes framework.

Subcommands:
  search  --query "..." [--max-validate N] [--no-cache] [...] -> raw validated pages
  read    --url "..." [--max-chars N] [--no-cache] [--no-wayback] [--proxy] -> page
  image   --query "..." [--no-cache] -> image search results
  expand  --query "..." --urls "u1,u2,..." [--max-new-links N] [--proxy] -> Level-2

All real output goes to a UTF-8 JSON file (default in the system TEMP directory,
override with --out); only an ASCII status line is printed to stdout (avoids
Windows cp1251 crashes).
"""
import sys, importlib.util, json, types, argparse, traceback, os, tempfile, time, hashlib, urllib.parse, urllib.request, random, subprocess, threading, shutil
from pathlib import Path

# ── Silences curl_cffi stderr warning ──────────────────────────────────────
# "failed to load native root certificate" comes from libcurl trying to read
# the Windows cert store (blocked by the harness sandbox). Pointing it at
# certifi's bundled CA file avoids the store access entirely.
try:
    import certifi
    os.environ.setdefault("CURL_CA_BUNDLE", certifi.where())
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except Exception:
    pass

# ── Paths ───────────────────────────────────────────────────────────────────
HERMES = Path(r"d:\Arx\Software Downloads\Hermes copy\hermes-dev")
WRAPPER = HERMES / "hermes-agent" / "tools" / "ddg_search_tool.py"
PLUGIN_DIR = HERMES / "plugins" / "web-tools" / "ddg"

# ── Cache ───────────────────────────────────────────────────────────────────
CACHE_DIR = os.path.join(tempfile.gettempdir(), "hermes_web_cache")
READ_TTL = 6 * 3600          # 6 hours
READ_WEAK_TTL = 5 * 60       # weak/failed reads: 5 min so they get retried soon
SEARCH_TTL = 1 * 3600        # 1 hour
EXPAND_TTL = 1 * 3600        # 1 hour
IMAGE_TTL = 30 * 60          # 30 min

# ── Newer impersonation targets ────────────────────────────────────────────
# curl_cffi 0.14.0 supports up to chrome142 and safari260. The repo's default
# pool (chrome110-124) is lagging; newer fingerprints reduce Cloudflare blocks.
NEW_IMPERSONATE = [
    "chrome136", "chrome133a", "chrome131", "chrome124",
    "safari180", "safari184", "safari260",
]
LEGACY_IMPERSONATE = ["chrome110", "chrome116", "chrome120", "chrome124"]

# ── Deno JS render engine ──────────────────────────────────────────────────
# Deno 2.7.7 + happy-dom 15.11.7 — lightweight JS execution without headless
# browser. The bundled binary and vendored npm cache come from the web-media-
# parser project (dist/WebMediaParser/bin/). Resolution order:
#   1. WEB_MEDIA_PARSER_DENO / DENO_BIN env
#   2. web-media-parser dist bundled bin/deno.exe
#   3. web-media-parser release bundled bin/deno.exe
#   4. deno on PATH
#   5. deno Python package (deno.find_deno_bin)
RENDER_WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "js_engine", "render_worker.js")
_DENO_WMP_DIST = r"d:\Arx\Software Downloads\_Images_EDIT-pack\web-media-parser\dist\WebMediaParser\bin\deno.exe"
_DENO_WMP_RELEASE = r"d:\Arx\Software Downloads\_Images_EDIT-pack\web-media-parser\release\WebMediaParser\bin\deno.exe"
_DENO_CACHE = r"d:\Arx\Software Downloads\_Images_EDIT-pack\web-media-parser\dist\WebMediaParser\bin\deno_cache"
RENDER_TIMEOUT = 8.0  # seconds; worker killed on timeout
RENDER_WAIT_MS = 500   # ms grace period for script microtasks


def _find_deno_bin():
    env = os.environ.get("WEB_MEDIA_PARSER_DENO") or os.environ.get("DENO_BIN")
    if env and os.path.exists(env):
        return env
    for cand in (_DENO_WMP_DIST, _DENO_WMP_RELEASE):
        if os.path.exists(cand):
            return cand
    path = shutil.which("deno") or shutil.which("deno.exe")
    if path:
        return path
    try:
        import deno  # type: ignore
        return deno.find_deno_bin()
    except Exception:
        return None


def _find_deno_cache():
    """Vendored npm cache with happy-dom; must exist for offline render."""
    if os.path.isdir(_DENO_CACHE):
        return _DENO_CACHE
    # fallback: src cache (dev mode)
    c2 = r"d:\Arx\Software Downloads\_Images_EDIT-pack\web-media-parser\src\parser\js_engine\deno_cache"
    if os.path.isdir(c2):
        return c2
    return None


def _render_available():
    return bool(_find_deno_bin()) and os.path.isfile(RENDER_WORKER) and bool(_find_deno_cache())


# ═══════════════════════════════════════════════════════════════════════════════
#  Cache helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _cache_path(kind, key):
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
    return os.path.join(CACHE_DIR, f"{kind}_{h}.json")


def _cache_get(kind, key, ttl):
    p = _cache_path(kind, key)
    try:
        if os.path.exists(p) and time.time() - os.path.getmtime(p) < ttl:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return None


def _cache_put(kind, key, data):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(_cache_path(kind, key), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
#  Registry stub + wrapper loader
# ═══════════════════════════════════════════════════════════════════════════════

def _install_stub_registry():
    class _Reg:
        def register(self, *a, **k): pass
        def get_tool_names_for_toolset(self, ts): return []
    tools_mod = types.ModuleType("tools")
    reg_mod = types.ModuleType("tools.registry")
    reg_mod.registry = _Reg()
    tools_mod.registry = reg_mod.registry
    sys.modules["tools"] = tools_mod
    sys.modules["tools.registry"] = reg_mod


def _load_wrapper():
    spec = importlib.util.spec_from_file_location("ddg_wrapper", WRAPPER)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(WRAPPER.parent))
    sys.path.insert(0, str(PLUGIN_DIR))
    spec.loader.exec_module(mod)
    return mod


# ═══════════════════════════════════════════════════════════════════════════════
#  Module patching helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _patch_modules(impersonate="newest", use_proxy=False, proxy_url="http://127.0.0.1:2080"):
    """Patch backend module globals for newer impersonation + optional proxy."""
    pool = _resolve_impersonate_pool(impersonate)
    for name in ("plugins.web_tools.ddg.ddg_search", "plugins.web_tools.ddg.visit_website_enhanced"):
        m = sys.modules.get(name)
        if m is None:
            continue
        if pool:
            m.IMPERSONATE_POOL = pool
        if use_proxy:
            m.USE_PROXY = True
            m.PROXY_URL = proxy_url


def _resolve_impersonate_pool(impersonate):
    if impersonate == "newest":
        return NEW_IMPERSONATE
    if impersonate == "legacy":
        return LEGACY_IMPERSONATE
    # comma-separated custom list
    if "," in impersonate:
        return [x.strip() for x in impersonate.split(",") if x.strip()]
    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  Wayback Machine resolver
# ═══════════════════════════════════════════════════════════════════════════════

def _wayback_latest(url):
    """Return (wayback_url, timestamp, snap_url) for the latest snapshot, or None.

    Uses the plain snapshot URL (with toolbar), NOT the ``id_`` raw variant —
    the ``id_`` URL is unreliable (returns 503 from web.archive.org). The
    toolbar HTML is still parsed fine by trafilatura.
    """
    try:
        api = "https://archive.org/wayback/available?url=" + urllib.parse.quote(url, safe="")
        req = urllib.request.Request(api, headers={"User-Agent": "HermesBridge/2.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        snap = data.get("archived_snapshots", {}).get("closest")
        if snap and snap.get("status") and snap["status"][0] in ("2", "3"):
            ts = snap.get("timestamp", "")
            snap_url = snap.get("url", "")
            fetch_url = snap_url.replace("http://", "https://") or f"https://web.archive.org/web/{url}"
            return fetch_url, ts, snap_url
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  Main commands
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_search(mod, args):
    cache_key = f"search:{args.query}:{args.max_validate}"
    if not args.no_cache:
        cached = _cache_get("search", cache_key, SEARCH_TTL)
        if cached is not None:
            return cached

    out = mod._safe_search_deep(args.query, validate=True, max_validate=args.max_validate)
    results = out.get("results", [])
    top = []
    for r in results[:args.max_validate]:
        top.append({
            "url": r.get("url"),
            "title": r.get("title"),
            "alive": r.get("alive"),
            "relevance": r.get("relevance"),
            "source_query": args.query,
            "text": (r.get("text") or r.get("snippet") or "")[:1200],
        })
    result = {"count": len(results), "top": top}

    if not args.no_cache:
        _cache_put("search", cache_key, result)
    return result


def cmd_read(mod, args):
    cache_key = f"read:{args.url}:{args.max_chars}"
    if not args.no_cache:
        cached = (_cache_get("read", cache_key, READ_TTL)
                  or _cache_get("readweak", cache_key, READ_WEAK_TTL))
        if cached is not None:
            return cached

    # Step 1: direct fetch via the Hermes pipeline
    page = mod.visit_website_enhanced(args.url, max_chars=args.max_chars)
    source = page.get("source", "")
    body = page.get("text") or page.get("content") or ""
    chars = len(body)

    # Step 2: Wayback fallback when direct failed or returned weak content
    # (JS-heavy pages often return a 200-300-char shell — archive copy is better).
    if not args.no_wayback and (source == "failed" or chars < 500):
        wb = _wayback_latest(args.url)
        if wb:
            wb_url, wb_ts, snap_url = wb
            wb_page = mod.visit_website_enhanced(wb_url, max_chars=args.max_chars)
            wb_source = wb_page.get("source", "")
            wb_body = wb_page.get("text") or wb_page.get("content") or ""
            wb_chars = len(wb_body)
            if wb_source != "failed" and wb_chars > 300:
                # Wayback succeeded — use this result
                page = wb_page
                source = "wayback"
                body = wb_body
                chars = wb_chars

    result = {
        "url": args.url,
        "title": page.get("title") or page.get("url") or args.url,
        "source": source,
        "chars": chars,
        "text": body,
        "links": [{"url": l.get("url"), "text": l.get("text")} for l in (page.get("links") or [])[:25]],
        "images": (page.get("fullsize_images") or [])[:12],
    }
    if source == "wayback":
        result["wayback_ts"] = wb_ts
        result["wayback_snapshot"] = snap_url

    if not args.no_cache:
        # Strong results cache long; weak/failed reads cache briefly so a later
        # call retries (and can hit the wayback fallback) instead of serving a
        # stale anti-bot shell.
        if source == "failed" or chars < 500:
            _cache_put("readweak", cache_key, result)
        else:
            _cache_put("read", cache_key, result)
    return result


def cmd_image(mod, args):
    cache_key = f"image:{args.query}"
    if not args.no_cache:
        cached = _cache_get("image", cache_key, IMAGE_TTL)
        if cached is not None:
            return cached

    out = mod.image_search(args.query)
    images = []
    for i in out.get("results", [])[:8]:
        images.append({
            "url": i.get("thumbnail") or i.get("page_url") or i.get("url"),
            "title": i.get("title"),
            "page_url": i.get("page_url"),
        })
    result = {"count": len(out.get("results", [])), "images": images}

    if not args.no_cache:
        _cache_put("image", cache_key, result)
    return result


def cmd_expand(mod, args):
    """Level-2 expansion: fetch new URLs found on source pages."""
    source_urls = [u.strip() for u in args.urls.split(",") if u.strip()]
    if not source_urls:
        return {"error": "no source URLs provided", "count": 0, "items": []}

    cache_key = f"expand:{args.query}:{','.join(source_urls)}:{args.max_new_links}"
    if not args.no_cache:
        cached = _cache_get("expand", cache_key, EXPAND_TTL)
        if cached is not None:
            return cached

    out = mod._safe_expand_and_fetch(
        query=args.query,
        source_urls=source_urls,
        max_new_links=args.max_new_links,
        max_chars=args.max_chars,
    )
    result = {
        "query": args.query,
        "candidates_count": out.get("candidates_count", 0),
        "fetched_count": out.get("fetched_count", 0),
        "items": out.get("items", [])[:args.max_new_links],
    }

    if not args.no_cache:
        _cache_put("expand", cache_key, result)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
#  Deno JS render (happy-dom)
# ═══════════════════════════════════════════════════════════════════════════════

def _fetch_raw_html(url):
    """Fetch raw HTML via the Hermes plugin fetcher chain (no text extraction).

    Returns (html, source) where source is the fetch method label or None.
    """
    m = sys.modules.get("plugins.web_tools.ddg.visit_website_enhanced")
    if m is None or not hasattr(m, "_fetch"):
        return None, None
    try:
        html = m._fetch(url)
        if html:
            return html, "direct"
    except Exception:
        pass
    return None, None


def _deno_render_call(html, page_url, max_chars, wait_ms, timeout):
    """One-shot Deno render: spawn worker, send HTML, read response line.

    Returns the result dict or None (fail-open: no binary, worker crash,
    timeout, JS exception).
    """
    deno = _find_deno_bin()
    cache = _find_deno_cache()
    if not deno or not cache:
        return None
    env = os.environ.copy()
    env["DENO_DIR"] = cache
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(
            [deno, "run", "--quiet", "--node-modules-dir=auto", RENDER_WORKER],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            env=env, **kwargs,
        )
    except Exception:
        return None
    try:
        request = json.dumps({
            "id": 0, "html": html, "pageUrl": page_url,
            "maxChars": max_chars, "waitMs": wait_ms,
        })
        try:
            proc.stdin.write(request + "\n")
            proc.stdin.flush()
        except Exception:
            return None
        # Read one line with a hard timeout (kills the worker on hang).
        holder = [None]
        def _reader():
            try:
                holder[0] = proc.stdout.readline()
            except Exception:
                holder[0] = None
        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        t.join(timeout=timeout)
        if t.is_alive():
            return None  # stuck — killed below
        line = holder[0]
        if not line:
            return None
        resp = json.loads(line.strip())
        if resp.get("error") or not isinstance(resp.get("result"), dict):
            return None
        return resp["result"]
    except Exception:
        return None
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def cmd_render(mod, args):
    """Fetch a page and execute its inline JS via Deno happy-dom, then extract
    the post-render text/links/images. Fail-open to the plain read path when
    Deno is unavailable or rendering fails."""
    cache_key = f"render:{args.url}:{args.max_chars}"
    if not args.no_cache:
        cached = (_cache_get("render", cache_key, READ_TTL)
                  or _cache_get("read", cache_key, READ_TTL)
                  or _cache_get("readweak", cache_key, READ_WEAK_TTL))
        if cached is not None:
            return cached

    result = None
    if _render_available():
        html, _src = _fetch_raw_html(args.url)
        if html:
            rendered = _deno_render_call(
                html, args.url, args.max_chars, args.wait_ms, args.render_timeout)
            if rendered:
                text = rendered.get("text") or ""
                result = {
                    "url": args.url,
                    "title": rendered.get("title") or args.url,
                    "source": "deno-render",
                    "chars": len(text),
                    "text": text,
                    "links": (rendered.get("links") or [])[:25],
                    "images": (rendered.get("images") or [])[:12],
                }

    if result is None:
        # Fail-open: fall back to the plain read path (direct → jina → wayback)
        return cmd_read(mod, args)

    if not args.no_cache:
        if result.get("chars", 0) < 500:
            _cache_put("readweak", cache_key, result)
        else:
            _cache_put("render", cache_key, result)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
#  argparse
# ═══════════════════════════════════════════════════════════════════════════════

def _build_parser():
    ap = argparse.ArgumentParser(description="Hermes Web Tools Bridge v2")
    ap.add_argument("cmd", choices=["search", "read", "image", "expand", "render"],
                    help="Subcommand")
    # shared
    ap.add_argument("--out", default="",
                    help="Output JSON path (default: {TEMP}/hermes_web_{cmd}_{ts}_{pid}.json)")
    ap.add_argument("--no-cache", action="store_true",
                    help="Skip disk cache")
    ap.add_argument("--proxy", action="store_true",
                    help="Enable NecoBox proxy retry (127.0.0.1:2080)")
    ap.add_argument("--proxy-url", default="http://127.0.0.1:2080",
                    help="Proxy URL (default: http://127.0.0.1:2080)")
    ap.add_argument("--impersonate", default="newest",
                    choices=["newest", "legacy"])
    # search
    ap.add_argument("--query", default="", help="Search query")
    ap.add_argument("--max-validate", type=int, default=8, help="Max URLs to validate")
    # read / render
    ap.add_argument("--url", default="", help="URL to fetch")
    ap.add_argument("--max-chars", type=int, default=8000, help="Max text chars")
    ap.add_argument("--no-wayback", action="store_true", help="Skip Wayback fallback")
    # render (Deno)
    ap.add_argument("--render-timeout", type=float, default=RENDER_TIMEOUT,
                    help="Deno render worker timeout in seconds (default 8.0)")
    ap.add_argument("--wait-ms", type=int, default=RENDER_WAIT_MS,
                    help="Grace period in ms for page scripts (default 500)")
    # expand
    ap.add_argument("--urls", default="", help="Comma-separated source URLs (for expand)")
    ap.add_argument("--max-new-links", type=int, default=10, help="Max new links to fetch")
    return ap


def _unique_out_path(cmd):
    ts = time.strftime("%Y%m%d_%H%M%S")
    pid = os.getpid()
    return os.path.join(tempfile.gettempdir(), f"hermes_web_{cmd}_{ts}_{pid}.json")


# ═══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    ap = _build_parser()
    args = ap.parse_args()

    _install_stub_registry()
    mod = _load_wrapper()
    _patch_modules(
        impersonate=args.impersonate,
        use_proxy=args.proxy,
        proxy_url=args.proxy_url,
    )

    # resolve output path
    out_path = args.out if args.out else _unique_out_path(args.cmd)

    result = {}
    try:
        if args.cmd == "search":
            result = cmd_search(mod, args)
        elif args.cmd == "read":
            result = cmd_read(mod, args)
        elif args.cmd == "image":
            result = cmd_image(mod, args)
        elif args.cmd == "expand":
            result = cmd_expand(mod, args)
        elif args.cmd == "render":
            result = cmd_render(mod, args)
    except Exception as e:
        result["error"] = repr(e)
        result["trace"] = traceback.format_exc()

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # ASCII-only status line (no cp1251 crashes)
    items = len(result.get("top") or result.get("images") or result.get("items") or [])
    status = "OK" if "error" not in result else "ERR"
    print("STATUS=%s cmd=%s items=%d" % (status, args.cmd, items), flush=True)


if __name__ == "__main__":
    main()