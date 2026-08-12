# -*- coding: utf-8 -*-
"""Pipeline health check for restore.ps1 and the GUI.

Checks that the custom deep-search pipeline is LIVE in the Hermes home:
  1. the 5 custom tools are registered in the tools registry
  2. the third-party packages the pipeline needs are importable in the
     Hermes venv (the python that runs this script)

Exit 0 = pipeline healthy. Exit 1 = something is broken.

restore.ps1 runs this with the Hermes venv python after syncing files and
installing deps, and uses the exit code as the final verdict. The GUI can
also call it directly (`<venv>\\python.exe restore_check.py`) to replace its
inline check.
"""
import importlib
import os
import sys

HERMES = os.path.join(os.path.expanduser("~"), ".hermes")
AGENT = os.path.join(HERMES, "hermes-agent")
TOOLS = os.path.join(AGENT, "tools")

# Same tool names the GUI "Check & Restore" button verifies.
TOOL_NAMES = [
    "web_search_deep",
    "web_expand_and_fetch",
    "visit_website_tool",
    "image_search",
    "web_deep_research",
]
# Module names (== pip package names except beautifulsoup4/bs4).
DEPS = ["ddgs", "bs4", "trafilatura", "htmldate", "lxml"]

ok = True

# ---- 1) tools registered -----------------------------------------------------
try:
    sys.path.insert(0, AGENT)
    sys.path.insert(0, TOOLS)
    from tools.registry import discover_builtin_tools, registry  # noqa: E402

    discover_builtin_tools(TOOLS)
    for name in TOOL_NAMES:
        present = registry.get_entry(name) is not None
        if not present:
            ok = False
        print(("OK   " if present else "MISS "), "tool:", name)
except Exception as e:  # registry itself broken / hermes not installed
    ok = False
    print("FAIL  registry:", type(e).__name__, str(e)[:200])

# ---- 2) deps importable in the running python --------------------------------
for mod in DEPS:
    try:
        importlib.import_module(mod)
        print("OK   dep:", mod)
    except Exception as e:
        ok = False
        print("FAIL  dep:", mod, "-", type(e).__name__)

print("VERDICT:", "OK" if ok else "BROKEN")
sys.exit(0 if ok else 1)
