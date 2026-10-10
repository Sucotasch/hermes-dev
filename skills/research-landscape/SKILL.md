---
name: research-landscape
description: >-
  Map the research landscape of a topic with the local zero-key Hermes web
  tools (bridge search/read/probe/expand): facet-derived parallel searches,
  bounded second passes, domain-diverse source selection, token-cheap claim
  verification, and a fixed synthesis template (Coverage / Key Sources /
  Subtopics / Recency / Gaps / Verdict). USE FOR: "map the field", "state
  of the art on X", "обзор поля", "что известно по теме", "карта
  направления", technology scans before build/buy decisions. NOT for a
  single quick fact — use hermes-web-tools directly for that.
---

# Research Landscape (recipe over hermes-web-tools)

Complements the `hermes-web-tools` skill: that one answers questions, this one
**maps a field** — coverage first, depth second, always honest about gaps.
All commands are the same bridge subcommands; nothing new is installed.

## Workflow (follow in order)

### Step 0 — Pre-flight (~10 s)
One `search --query "test" --max-validate 2` call. If it fails, run the
Doctor section of `hermes-web-tools` and stop: no landscape on a broken tool.

### Step 1 — Derive facets BEFORE searching
Split the topic into **3–6 orthogonal facets** — the axes a good answer must
cover (technologies, constraints, trade-offs, alternatives). Write them down
explicitly; add a recency term (current year) to facets where the field moves
fast. A facet is a QUESTION, not a keyword dump.

### Step 2 — Primary pass: one search per facet, ALL IN PARALLEL
One pwsh batch, one `search` call per facet, each with its own `--out`,
`--max-validate 6–8`. Parallel calls cost the same wall time as one.

**Query phrasing pitfall (found live):** engines tokenize tool names
literally — «llama.cpp» reads as «llama» and returns pages about the
animal / Meta Llama. Prefer distinctive feature terms (`llamacpp`,
`gguf`, `q8_0`, `kv cache`, `offload`) over ambiguous library names.

### Step 3 — Bounded second passes (the discipline)
For each facet count `alive:true` AND topically relevant results:
- **≥3 alive → facet is covered. Do NOT search it again.** Re-searching a rich
  facet wastes steps for no coverage gain.
- **<3 alive → ONE reworded retry** (different angle, e.g. tool name instead
  of concept). Still <3 → record the facet as **sparse** and move on. Never
  a third pass.

### Step 4 — Select carriers
Per facet pick 1–2 sources by: `alive`, higher `relevance`, **domain
diversity** (never let one domain carry two facets), recency when it matters.
Dedup by domain across facets. Prefer primary sources (docs, benchmarks,
repos, papers) over content farms.

### Step 5 — Deep read the load-bearers
`read` ONLY the 3–5 sources whose claims will carry the report. The search
`text` snippets are often enough for the rest — reading everything is a
token waste. If a page `read` fails → its snippet may still carry the point,
but mark it as snippet-only. **Table-heavy carriers** (guides with VRAM /
benchmark tables) need `--max-chars 12000–15000` — smaller caps truncate the
tables the verdict rests on (found live).

### Step 6 — Verify the keystone claims (token-cheap)
`probe` the 2–4 claims the verdict depends on (numbers, versions, "supports
X"). ~60 tokens per check vs a full re-read. A claim that fails probe gets
dropped or downgraded to "reported but unverified".

### Step 7 — Synthesize into the template (fixed order)
```
# Landscape: <topic>
**Volume & coverage** — how much solid material exists; which facets were
rich/sparse; how many distinct alive domains carry the answer.
**Key sources** — 3–6, one line each: what it establishes + URL/handle.
Sort by how much of the landscape it carries, not by rank.
**Active subtopics** — what practitioners are actually discussing now.
**Recency signal** — dates seen; is the field moving; are sources stale.
**Gaps & open questions** — facets with no solid source; honest "not found
online" instead of filler.
**Verdict** — 3–5 lines: the state of the field + what to do next.
```
Cite every factual claim to its source inline. Never fabricate a gap filler.

## Discipline
- **Coverage gate:** the verdict needs ≥3 distinct alive domains in total;
  fewer → say the coverage is thin, do not broaden silently.
- **Budget honesty:** record tool calls made; expect `1 pre-flight + one
  search per facet + ≤1 retry per sparse facet + 3–5 reads + 2–4 probes` —
  for a 4–6-facet landscape that is **~10–16 calls**. More means the bounds
  slipped; fewer means snippets carried more than usual.
- Search snippets ground most lines; `read` only what carries claims;
  `probe` what the verdict rests on.

## Honest limits
- Multi-engine DDG search biases toward indexable, English-heavy content;
  niche papers may need a facet named after them (e.g. "arXiv <topic>").
- `probe` is exact-substring on extracted body text — prefer short ASCII
  claims copied from the sentence; typographic dashes (–) and reworded
  numbers break the match. Rephrase to the page's wording once, then `read`.
- Subdomain walls: wall state is keyed per hostname, so `en.m.wikipedia.org`
  boxed ≠ `en.wikipedia.org` dead. When one subdomain fails validation, a
  single diagnostic `read` of a sibling tells whether the domain is truly
  gone (found live: 4 parallel validation pools also cause transient
  timeouts — treat single-facet dead results with skepticism once).
- Recency is judged from dates seen in sources, not from a clock: say what
  you saw, not what you assume.
