# -*- coding: utf-8 -*-
"""Live smoke probe for restore.ps1 -RunSmoke.

Loads the wrapper EXACTLY like Hermes does (real tools.registry, no stub),
discovers builtin tools from the live hermes-agent/tools/, then calls
web_search_deep with a small budget and expects results. Verifies the
whole chain: registry -> wrapper -> plugins -> network -> backend.

Exit 0 = chain works live. Exit 1 = broken somewhere.
Designed to finish in ~30-60s (one DDG query, 5 URL validations).
"""
import os
import sys

HERMES = os.path.join(os.path.expanduser("~"), ".hermes")
AGENT = os.path.join(HERMES, "hermes-agent")
TOOLS = os.path.join(AGENT, "tools")
WRAPPER = os.path.join(TOOLS, "ddg_search_tool.py")

if not os.path.exists(WRAPPER):
    print("FAIL  wrapper missing:", WRAPPER)
    sys.exit(1)

sys.path.insert(0, AGENT)
sys.path.insert(0, TOOLS)
# plugins dir must be importable (the wrapper loads plugins by path, but
# plugin-internal imports like "from compose import ..." need the dir).
sys.path.insert(0, os.path.join(HERMES, "plugins", "web-tools", "ddg"))

try:
    from tools.registry import discover_builtin_tools, registry
except Exception as e:
    print("FAIL  registry import:", type(e).__name__, str(e)[:200])
    sys.exit(1)

discover_builtin_tools(TOOLS)

entry = registry.get_entry("web_search_deep")
if entry is None:
    print("FAIL  web_search_deep not registered after discovery")
    sys.exit(1)
print("OK    tool registered:", entry.name if hasattr(entry, "name") else "web_search_deep")

# Invoke the real handler (network). Small budget so it stays fast.
handler = getattr(entry, "handler", None) or getattr(entry, "func", None)
if handler is None and callable(entry):
    handler = entry
if handler is None:
    print("FAIL  registry entry has no callable handler")
    sys.exit(1)

try:
    out = handler({"query": "what is python programming language", "validate": True,
                   "max_validate": 5, "max_chars": 800}, toolset="web")
except TypeError:
    out = handler({"query": "what is python programming language", "validate": True,
                   "max_validate": 5, "max_chars": 800})

results = None
if isinstance(out, dict):
    results = out.get("results") or out.get("sources") or []
    if not results and out.get("text"):
        results = [out]
else:
    results = [out]

if results:
    n = len(results)
    first = ""
    r0 = results[0]
    if isinstance(r0, dict):
        first = (r0.get("title") or r0.get("url") or "")[:60]
    print("OK    live search returned {} result(s); first: {}".format(n, first))
    print("SMOKE OK")
    sys.exit(0)
else:
    print("FAIL  live search returned 0 results")
    sys.exit(1)
