#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Imagus Sieve static engine — thumbnail → full-size URL transformation.

Ported (slimmed) from Temp/web-media-parser/src/parser/site_pattern_manager.py
(proven, tested code). Only the *static* subset is ported:

  * sieve JSON loading, domain-indexed + global rules
  * `img` + `to` regex substitution ($1..$n, $& → \\g<n>)
  * JS `to`-rules converted to Python callables where possible (no Deno)
  * `#ext#` variant expansion (image.#jpg png# → two URLs)
  * WordPress size-suffix strip (-300x200.jpg → .jpg)
  * fail-open: any error / missing file → url returned unchanged

Rules needing a real DOM (this.node, document., querySelector, fetch, …)
cannot run here and are skipped (fail-open) — exactly like the source project
without its Deno engine. This module has zero external dependencies.
"""

import os
import re
import json
import logging
import threading
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

_SIEVE_FILENAME = "Imagus_sieve_2026.07.15_823.json"
_RESOURCES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources")


# DOM keywords that indicate a rule needs browser context → cannot convert.
_DOM_KEYWORDS = (
    "this.node", "this.TRG", "this.find", "this.set", "this.prepare",
    "this.getImages", "document.", "window.", "location.",
    "Port.send", "XMLHttpRequest", "fetch(", "addEventListener",
    "querySelector", "getElementById", "getElementsBy",
    "createElement", "appendChild", "innerHTML", "outerHTML",
    "sessionStorage", "localStorage",
)


# --------------------------------------------------------------------------
# JS rule → Python callable (pure Python subset, no Deno)
# --------------------------------------------------------------------------

def _needs_dom(js_code):
    """True if JS code requires browser DOM/context to execute."""
    return any(kw in js_code for kw in _DOM_KEYWORDS)


def _split_js_concat(expr):
    """Split a JS concatenation expression by '+', respecting strings and $[n]."""
    parts = []
    current = ""
    depth = 0
    in_string = None
    i = 0
    while i < len(expr):
        ch = expr[i]
        if in_string:
            current += ch
            if ch == in_string and expr[i - 1:i] != "\\":
                in_string = None
            i += 1
            continue
        if ch in ('"', "'"):
            in_string = ch
            current += ch
            i += 1
            continue
        if ch == "[":
            depth += 1
            current += ch
            i += 1
            continue
        if ch == "]":
            depth -= 1
            current += ch
            i += 1
            continue
        if ch == "+" and depth == 0:
            parts.append(current)
            current = ""
            i += 1
            continue
        current += ch
        i += 1
    if current.strip():
        parts.append(current)
    return parts


def _js_expr_to_python_single(expr):
    """Convert a single JS expression (no concat, no ternary) to Python."""
    import re as _re

    expr = expr.strip()

    # $[n] → g(n)
    if _re.fullmatch(r"\$\[(\d+)\]", expr):
        n = _re.search(r"\d+", expr).group()
        return f"g({n})"

    # String literal
    if (expr.startswith('"') and expr.endswith('"')) or \
       (expr.startswith("'") and expr.endswith("'")):
        inner = expr[1:-1]
        return "'" + inner.replace("'", "\\'") + "'"

    # .replace(/pattern/, 'replacement')
    replace_match = _re.match(r"(.+?)\.replace\s*\(\s*/(.+?)/([gimsuy]*)\s*,\s*(.+?)\s*\)\s*$", expr)
    if replace_match:
        target, pattern, flags, replacement = replace_match.groups()
        py_target = _js_expr_to_python_single(target)
        py_repl = _js_expr_to_python_single(replacement)
        if py_target and py_repl:
            flag_str = "re.IGNORECASE" if "i" in flags else "0"
            return f"_re.sub(r'{pattern}', {py_repl}, {py_target}, flags={flag_str})"

    # Math operations
    if "Math." in expr:
        py_expr = expr
        py_expr = _re.sub(r"Math\.ceil\((.+?)\)", r'int(__import__("math").ceil(\1))', py_expr)
        py_expr = _re.sub(r"Math\.random\(\)", "__import__('math').random()", py_expr)
        py_expr = _re.sub(r"Math\.floor\((.+?)\)", r'int(__import__("math").floor(\1))', py_expr)
        if py_expr != expr:
            return py_expr

    # atob(...) → base64 decode
    atob_match = _re.match(r"atob\((.+?)\)", expr)
    if atob_match:
        inner = atob_match.group(1)
        py_inner = _js_expr_to_python_single(inner)
        if py_inner:
            return f"__import__('base64').b64decode({py_inner}).decode()"

    # decodeURIComponent(...)
    dec_match = _re.match(r"decodeURIComponent\((.+?)\)", expr)
    if dec_match:
        inner = dec_match.group(1)
        py_inner = _js_expr_to_python_single(inner)
        if py_inner:
            return f"__import__('urllib.parse').unquote({py_inner})"

    return None


def _js_expr_to_python(expr):
    """Convert a JS expression to a Python expression string. None if too complex."""
    import re as _re

    # Template literal: `prefix${expr}suffix`
    tl_match = _re.match(r"`(.+)`$", expr, _re.DOTALL)
    if tl_match:
        template = tl_match.group(1)
        py_template = _re.sub(r"\$\{(\$?\[(\d+)\])", lambda m: "{g(" + m.group(2) + ")}", template)
        py_template = _re.sub(r"\$\{([^}]+)\}", lambda m: "{" + m.group(1).replace("$[", "g(").replace("]", ")") + "}", py_template)
        return "f'" + py_template.replace("'", "\\'") + "'"

    # Ternary: cond ? a : b
    ternary_match = _re.match(r"(.+?)\s*\?\s*(.+?)\s*:\s*(.+)$", expr)
    if ternary_match:
        cond, if_true, if_false = ternary_match.groups()
        py_cond = _js_expr_to_python(cond.strip())
        py_true = _js_expr_to_python(if_true.strip())
        py_false = _js_expr_to_python(if_false.strip())
        if py_cond and py_true and py_false:
            return f"({py_true} if {py_cond} else {py_false})"

    # Concatenation
    parts = _split_js_concat(expr)
    if parts and len(parts) > 1:
        py_parts = []
        for part in parts:
            py_part = _js_expr_to_python_single(part.strip())
            if py_part is None:
                return None
            py_parts.append(py_part)
        return " + ".join(py_parts)

    return _js_expr_to_python_single(expr)


def _build_js_callable(expr):
    """Build a Python callable(match) from a JS return expression."""
    import re as _re

    py_expr = _js_expr_to_python(expr)
    if py_expr is None:
        return None

    func_src = """
def _transform(m):
    import re as _re
    g = lambda n: m.group(n) if m.lastindex and n <= m.lastindex else ''
    try:
        result = """ + py_expr + """
        if result is None or result is False:
            return None
        return str(result)
    except Exception:
        return None
"""
    namespace = {}
    try:
        exec(func_src, namespace)
        return namespace["_transform"]
    except Exception:
        return None


def _try_parse_imagus_js(js_code):
    """Convert an Imagus JS `to` rule (starting with ':') to a Python callable."""
    if not js_code or not js_code.startswith(":"):
        return None
    js_code = js_code[1:].strip()
    if _needs_dom(js_code):
        return None
    return_match = re.search(r"return\s+(.+?)(?:;?\s*$)", js_code, re.MULTILINE | re.DOTALL)
    if not return_match:
        return None
    return _build_js_callable(return_match.group(1).strip().rstrip(";").strip())


def _sanitize_imagus_target(target):
    """Convert Imagus $1/$2 to Python \\g<1>/\\g<2> to avoid ambiguity with numbers."""
    if not isinstance(target, str):
        return target
    return re.sub(r"(?<!\\)\$(\d+)", lambda m: f"\\g<{m.group(1)}>", target)


def _expand_variants(text):
    """Expand Imagus '#ext1 ext2#' syntax into multiple strings (recursive)."""
    if not text:
        return []

    def expand_line(l):
        match = re.search(r"#([^#]+)#", l)
        if not match:
            return [l]
        prefix = l[:match.start()]
        options = match.group(1).split()
        suffix = l[match.end():]
        res = []
        for opt in options:
            res.extend(expand_line(f"{prefix}{opt}{suffix}"))
        return res

    all_variants = []
    for line in text.split("\n"):
        line = line.strip()
        if line:
            all_variants.extend(expand_line(line))
    return all_variants


# --------------------------------------------------------------------------
# Sieve loading & application
# --------------------------------------------------------------------------

_WORDPRESS_RE = re.compile(
    r"(-\d{2,4}x\d{2,4})(\.(?:jpe?g|png|webp|avif))(?=$|\?)",
    re.IGNORECASE,
)


def _extract_domain_from_regex(regex_str):
    """Heuristically extract a plain domain from a regex like '^(media\\.admagazine\\.ru/'."""
    if not regex_str:
        return None
    match = re.search(r"\^?\s*\(?\s*(?:www\.)?([a-z0-9-]+(?:\\[.][a-z0-9-]+)+)", regex_str, re.I)
    if match:
        return match.group(1).replace(r"\.", ".").lower()
    return None


class _Sieve:
    """Lazy-loaded Imagus sieve rule set with domain indexing."""

    def __init__(self, path=None):
        self._path = path or os.path.join(_RESOURCES_DIR, _SIEVE_FILENAME)
        self._domain_rules = {}   # domain -> [rule]
        self._global_rules = []   # rules without a recognizable domain
        self._all_rules = []      # every rule (fallback scan when domain index misses)
        self._link_rules = []     # rules with a usable `link` regex (INT-3 discovery)
        self._loaded = False

    def load(self):
        if self._loaded:
            return
        self._loaded = True
        if not os.path.exists(self._path):
            logger.debug(f"sieve: rules file not found at {self._path} — disabled (fail-open)")
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning(f"sieve: failed to load {self._path}: {e} — disabled (fail-open)")
            return

        converted = skipped = regex = 0
        for rule_name, rule_data in data.items():
            if not isinstance(rule_data, dict):
                continue
            to_rule = rule_data.get("to", "")
            if isinstance(to_rule, str) and to_rule.startswith(":"):
                callable_fn = _try_parse_imagus_js(to_rule)
                if callable_fn:
                    rule_data["to_callable"] = callable_fn
                    converted += 1
                else:
                    skipped += 1  # needs DOM or too complex — fail-open skip
                    continue
            elif isinstance(to_rule, str) and to_rule:
                rule_data["to_python"] = _sanitize_imagus_target(to_rule)
                regex += 1

            self._all_rules.append(rule_data)
            domain = _extract_domain_from_regex(rule_data.get("link", ""))
            if not domain:
                domain = _extract_domain_from_regex(rule_data.get("img", ""))
            if domain:
                self._domain_rules.setdefault(domain, []).append(rule_data)
            else:
                self._global_rules.append(rule_data)

        # Precompile `link` regexes once (globals first, then per-domain rules —
        # same match order as the source project) for fast get_link_rule().
        self._link_rules = []
        ordered = list(self._global_rules)
        for domain_rules in self._domain_rules.values():
            ordered.extend(domain_rules)
        for rule in ordered:
            link_re = rule.get("link")
            if not isinstance(link_re, str) or not link_re:
                continue
            try:
                rule["_link_re"] = re.compile(link_re, re.I)
            except re.error:
                continue
            self._link_rules.append(rule)
        logger.info(
            f"sieve: loaded {len(data)} rules from {os.path.basename(self._path)} "
            f"({regex} regex, {converted} JS-converted, {skipped} JS skipped DOM/complex)"
        )

    def _rules_for(self, url):
        """Domain-indexed + global rules applicable to a URL (with www. handling)."""
        try:
            from urllib.parse import urlparse
            host = (urlparse(url).hostname or "").lower()
        except Exception:
            host = ""
        rules = list(self._global_rules)
        if not host:
            return rules
        rules.extend(self._domain_rules.get(host, []))
        if host.startswith("www."):
            rules.extend(self._domain_rules.get(host[4:], []))
        return rules

    def _try_rule(self, rule, url_variations, url, results):
        """Apply one sieve rule; returns True when a usable transform was appended.

        Matches like the original (which accumulates results from ALL matching
        rules): a rule that matches but produces no new URL must NOT stop the
        scan — a later rule may hold the real fullsize transform.
        """
        img_regex = rule.get("img", "")
        if not img_regex:
            return False
        transformed = False
        for v_url in url_variations:
            match = re.search(img_regex, v_url, re.I)
            if not match:
                continue
            callable_fn = rule.get("to_callable")
            if callable_fn:
                try:
                    substituted = callable_fn(match)
                    if substituted and substituted != v_url:
                        for variant in _expand_variants(substituted):
                            variant = variant.strip()
                            if not variant:
                                continue
                            if variant.startswith("//"):
                                variant = "https:" + variant
                            elif "://" not in variant:
                                scheme = url.split("://", 1)[0]
                                variant = f"{scheme}://{variant}"
                            if variant != url:
                                results.append(variant)
                                transformed = True
                    break
                except Exception:
                    break

            to_pattern = rule.get("to_python", "")
            if not to_pattern:
                continue
            try:
                substituted = re.sub(
                    img_regex,
                    lambda m, pat=to_pattern: m.expand(pat),
                    v_url,
                    flags=re.IGNORECASE,
                )
            except Exception:
                substituted = re.sub(img_regex, to_pattern, v_url, flags=re.IGNORECASE)
            if "://" not in substituted and substituted != url:
                scheme = url.split("://", 1)[0]
                substituted = f"{scheme}://{substituted}"
            if substituted != url:
                results.extend(_expand_variants(substituted))
                transformed = True
            break
        return transformed

    def transform_candidates(self, url, source_url=""):
        """Return a deduplicated list of candidate URLs: [original, ...transformed].

        Fail-open: on any error returns [url]. No rule match → [url] unchanged.
        Applies: sieve img+to rules (regex + JS-converted) — domain-indexed
        first for speed, then a full fallback scan for rules whose extracted
        domain was imprecise; then the global WordPress size-suffix strip.
        Unlike a first-match-wins design, ALL matching rules contribute their
        candidates (matching the original SitePatternManager behavior).
        """
        results = [url]
        url_variations = [url]
        if "://" in url:
            url_variations.append(url.split("://", 1)[1])

        # Fast path: domain-indexed + global rules only
        indexed = self._rules_for(source_url or url) + self._rules_for(url)
        any_transformed = False
        for rule in indexed:
            try:
                if self._try_rule(rule, url_variations, url, results):
                    any_transformed = True
            except Exception:
                continue

        # Fallback: full scan (rules whose regex-embedded domain was imprecise,
        # e.g. 'photo.2gis' vs host 'i2.photo.2gis.ru'). Only when the fast path
        # produced nothing.
        if not any_transformed and len(self._all_rules) != len(indexed):
            for rule in self._all_rules:
                try:
                    if self._try_rule(rule, url_variations, url, results):
                        any_transformed = True
                except Exception:
                    continue

        # WordPress-style size suffix strip (e.g. -300x200.jpg → .jpg)
        if len(results) == 1:
            stripped = _WORDPRESS_RE.sub(r"\2", results[0], count=1)
            if stripped != results[0]:
                results = [stripped]

        # Deduplicate preserving order
        seen = set()
        final_list = []
        for u in results:
            if u not in seen:
                final_list.append(u)
                seen.add(u)
        return final_list


    # --- Sieve link->url->res chain (INT-3: thumbnail transitions) ----------
    #
    # Many sieve rules are not pure img+to transforms but page-fetch chains:
    # match the `link` regex on a thumbnail's parent href, apply the `url`
    # template (string transform; content after " :" is POSTed form data,
    # e.g. imx.to 'imgContinue='), fetch that page, then extract the fullsize
    # image via the rule's `res` regex(es) with an <img src> fallback. Only the
    # STATIC subset is ported — JS `url`/`res` expressions (':' prefix) need
    # the Deno DOM engine and fail open (return None / no match), exactly like
    # the source project without its engine.

    def get_link_rule(self, link_url):
        """Find a sieve rule whose `link` regex matches the given URL.

        Returns (rule_data, match_object) or None. Matches the scheme-stripped
        URL first, then the full URL (extension behaviour).
        """
        if not link_url:
            return None
        stripped = re.sub(r"^https?://", "", link_url, flags=re.I)
        for variant in (stripped, link_url):
            for rule in self._link_rules:
                compiled = rule.get("_link_re")
                if compiled is None:
                    continue
                m = compiled.search(variant)
                if m:
                    return rule, m
        return None

    def apply_link_url_transform(self, rule, match, page_url=""):
        """Build (fetch_url, post_data) from a rule's `url` template.

        Substitutes $1..$n / $& from the regex match (descending order so $10
        isn't corrupted by $1). Content after " :" becomes form POST data.
        JS templates (':') and data: URLs return None (nothing to fetch).
        Returns None when no fetchable transform exists (fail-open).
        """
        url_tpl = rule.get("url")
        if not url_tpl or not isinstance(url_tpl, str):
            return None
        if url_tpl.lstrip().startswith(":"):
            return None  # JS url needs the DOM engine — fail-open
        post_match = re.search(r"\s+:(.+)$", url_tpl)
        template = url_tpl[:post_match.start()] if post_match else url_tpl
        post_data = post_match.group(1).strip() if post_match else None
        result = template
        for i in range(len(match.groups()), 0, -1):
            placeholder = f"${i}"
            if placeholder in result and match.group(i) is not None:
                result = result.replace(placeholder, match.group(i))
        if "$&" in result:
            result = result.replace("$&", match.group(0) or "")
        # POST data gets the same $n substitution (the source project left the
        # raw '$1' in post_data — fixed here; static POST rules are rare but
        # must send the real value, e.g. imx.to 'imgContinue=abc').
        if post_data:
            for i in range(len(match.groups()), 0, -1):
                placeholder = f"${i}"
                if placeholder in post_data and match.group(i) is not None:
                    post_data = post_data.replace(placeholder, match.group(i))
            if "$&" in post_data:
                post_data = post_data.replace("$&", match.group(0) or "")
        if not result:
            return None
        if result.startswith("data:"):
            return None  # in-page data-URL shims are not real fetches
        if result.startswith("//"):
            result = "https:" + result
        elif not result.startswith(("http://", "https://")):
            result = "https://" + result
        return result, post_data

    def extract_res_urls(self, rule, html, page_url="", groups=None, href=""):
        """Extract fullsize URLs from a linked page's HTML via `res` regexes.

        Group 1 of each match is the URL. JS res expressions (':') need the DOM
        worker — skipped fail-open. If no pattern yields results, falls back to
        a plain <img src> scan filtered to photo extensions.
        """
        if not html:
            return []
        urls, seen = [], set()
        res = rule.get("res")
        patterns = res if isinstance(res, list) else [res]
        for pat in patterns:
            if not pat or not isinstance(pat, str):
                continue
            if pat.lstrip().startswith(":"):
                continue  # JS res needs DOM — skipped
            try:
                for m in re.finditer(pat, html, re.I):
                    if m.lastindex and m.group(1):
                        u = m.group(1)
                        if u.startswith("//"):
                            u = "https:" + u
                        elif not u.startswith("http"):
                            u = urljoin(page_url or "", u)
                        if u.startswith(("http://", "https://")) and u not in seen:
                            seen.add(u)
                            urls.append(u)
            except re.error:
                continue
            if urls:
                break
        if not urls:
            for m in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.I):
                u = m.group(1)
                if u.startswith("//"):
                    u = "https:" + u
                elif not u.startswith("http"):
                    u = urljoin(page_url or "", u)
                # png added: the source project's fallback regex omitted it,
                # but png is a core gallery format and must not be lost.
                if (u.startswith("http") and u not in seen and
                        re.search(r"\.(jpe?g|png|webp|avif|heic|bmp|tiff?)$", u, re.I)):
                    seen.add(u)
                    urls.append(u)
        return urls


_SINGLETON = None
_LOCK = threading.Lock()


def _get_sieve():
    """Thread-safe lazy singleton (first call may come from a pool worker)."""
    global _SINGLETON
    if _SINGLETON is None:
        with _LOCK:
            if _SINGLETON is None:
                inst = _Sieve()
                inst.load()
                _SINGLETON = inst
    return _SINGLETON


def apply(url, source_url=""):
    """Upgrade a thumbnail URL to full-size via Imagus sieve rules.

    Returns the first candidate different from the input, else the input
    unchanged. Fail-open: never raises, never returns None.
    """
    if not url:
        return url
    try:
        candidates = _get_sieve().transform_candidates(url, source_url or "")
    except Exception:
        return url
    for c in candidates:
        if c and c != url:
            return c
    return url


def candidates(url, source_url=""):
    """All sieve candidates for a URL (fail-open → [url])."""
    try:
        return _get_sieve().transform_candidates(url, source_url or "")
    except Exception:
        return [url]


def loaded_count():
    """Number of loaded rules (0 when file missing/disabled). For tests/logs."""
    return len(_get_sieve()._global_rules) + sum(
        len(r) for r in _get_sieve()._domain_rules.values()
    )


if __name__ == "__main__":
    # Smoke check: try a known thumbnail pattern
    logging.basicConfig(level=logging.INFO)
    n = loaded_count()
    print(f"rules loaded: {n}")
    test = "https://i.imgur.com/abc123b.jpg"
    print(f"apply({test}) -> {apply(test)}")
