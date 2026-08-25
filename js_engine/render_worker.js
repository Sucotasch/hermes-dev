// Deno render worker (happy-dom, JavaScript evaluation ON) for the harness
// web-tools bridge. Extracts post-JS content from a page's HTML without a
// headless browser: parses the document, runs the page's INLINE scripts in a
// sandboxed happy-dom Window, then returns title/text/links/images from the
// mutated DOM.
//
// Protocol: newline-delimited JSON over stdin/stdout, one request per line:
//   -> {"id":0,"html":"<page html>","pageUrl":"https://..","maxChars":8000,"waitMs":500}
//   <- {"id":0,"result":{"title":..,"text":..,"links":[..],"images":[..]}}
//   <- {"id":0,"error":"..."}   JS threw / happy-dom load failed / bad input
//
// Sandbox: spawned by Python with NO --allow-net/--allow-read/--allow-write/
// --allow-env (engine.py pattern). External <script src> do NOT load (no net);
// only inline scripts run. happy-dom resolves offline from the vendored
// DENO_DIR cache (web-media-parser's js_engine/deno_cache).
//
// Design notes:
//  - No waitUntilComplete(): a page script that arms setInterval/setTimeout
//    would never let it resolve. Instead: inline scripts run synchronously
//    during document.write/close, then we sleep a fixed waitMs grace period
//    for queued microtasks/short timers, then read the DOM. The Python side
//    kills the worker on a hard timeout anyway.
//  - console.* is routed to stderr — stdout is the JSON protocol channel.

import { Window } from "npm:happy-dom@15.11.7";

function cleanText(s, maxChars) {
  if (!s) return "";
  const t = String(s).replace(/\u00a0/g, " ").replace(/[ \t]+/g, " ")
    .replace(/\n{3,}/g, "\n\n").trim();
  return t.slice(0, maxChars || 8000);
}

function uniqueResolved(doc, selector, attr) {
  const out = [];
  const seen = new Set();
  for (const el of doc.querySelectorAll(selector)) {
    const raw = el.getAttribute(attr);
    if (!raw) continue;
    let abs;
    try { abs = new URL(raw, doc.baseURI).href; } catch (_) { continue; }
    if (abs.startsWith("javascript:") || abs.startsWith("data:")) continue;
    if (seen.has(abs)) continue;
    seen.add(abs);
    out.push(abs);
    if (out.length >= 50) break;
  }
  return out;
}

async function handle(line) {
  let req;
  try { req = JSON.parse(line); } catch (_) {
    return JSON.stringify({ id: null, error: "bad json" });
  }
  const { id, html, pageUrl, maxChars, waitMs } = req;
  if (typeof html !== "string") {
    return JSON.stringify({ id, error: "missing html" });
  }
  try {
    const win = new Window({
      url: pageUrl || "https://local.invalid/",
      settings: { enableJavaScriptEvaluation: true },
    });
    const doc = win.document;
    try {
      doc.write(html);
      doc.close();
    } catch (_) {
      try { doc.body.innerHTML = html; } catch (_) {}
    }
    // Grace period for microtasks / short timers / mutation effects.
    if (waitMs > 0) {
      await new Promise((r) => setTimeout(r, Math.min(waitMs, 3000)));
    }
    const text = cleanText(doc.body ? (doc.body.innerText || doc.body.textContent) : "", maxChars);
    const result = {
      title: doc.title || "",
      text: text,
      links: uniqueResolved(doc, "a[href]", "href"),
      images: uniqueResolved(doc, "img[src]", "src"),
    };
    return JSON.stringify({ id, result });
  } catch (e) {
    return JSON.stringify({ id, error: String((e && e.message) || e) });
  }
}

// console -> stderr (stdout is the JSON protocol channel)
const enc = new TextEncoder();
const toStderr = (...a) => {
  try { Deno.stderr.writeSync(enc.encode(a.map(String).join(" ") + "\n")); } catch (_) {}
};
console.log = console.info = console.warn = console.error = toStderr;

const buf = new Uint8Array(1 << 16);
let acc = "";
const decoder = new TextDecoder();
const encoder = new TextEncoder();
for (;;) {
  const n = Deno.stdin.readSync(buf);
  if (n === null) break; // EOF
  acc += decoder.decode(buf.subarray(0, n), { stream: true });
  let idx;
  while ((idx = acc.indexOf("\n")) >= 0) {
    const line = acc.slice(0, idx).trim();
    acc = acc.slice(idx + 1);
    if (!line) continue;
    Deno.stdout.writeSync(encoder.encode(await handle(line) + "\n"));
  }
}
