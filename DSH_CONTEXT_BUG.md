# DSH reasoning_content 400 Bug — refined diagnosis & final fix (settings.yaml)

Status: **FIXED via user-config (settings.yaml), verified across a LONG
context-heavy session on 2026-08-26** — dozens of tool calls, thousands of
lines of docs/code read, zero 400s (exactly the workload that used to trigger
the bug).
Recorded: 2026-08-24 (initial diagnosis), 2026-08-26 (refined diagnosis + config fix + live verification).

## Symptom

DeepSeek Harness (DSH) sessions fail mid-turn with:

```
This turn failed 400: {"message":"The reasoning_content in the thinking mode
must be passed back to the API.","type":"invalid_request_error"}
```

The whole turn is lost (files on disk survive). The error appears when an
assistant message that originally had reasoning_content (even whitespace-only)
is replayed in a subsequent request without the field.

## Root cause (from real session data + pi-ai code)

**Primary trigger**: The model produces `reasoning_content: " "` (single space)
on tool-call-only turns — stored in the durable session as
`{"type":"reasoning","text":" "}`. On replay, pi-ai's serialization filter
`block.thinking.trim().length > 0` drops this whitespace-only thinking block
entirely, so `assistantMsg.reasoning_content` is never set on the outbound
message. DeepSeek's API (behind any proxy) requires the field to be passed back
verbatim → 400.

**Second trigger (found 2026-08-26)**: *Every* assistant message without a
reasoning block (tool-call-only messages that produced no reasoning at all) also
lacks `reasoning_content` on replay. pi-ai only patches the field to `""` when
`compat.requiresReasoningContentOnAssistantMessages && model.reasoning` (see
`openai-completions.js` fallback). That compat flag defaults to `isDeepSeek`
(`provider === "deepseek" || baseUrl.includes("deepseek.com")`), which is
**false for proxies** (bai, tokenrouter, openrouter, etc.), and `model.reasoning`
is also false for a proxy route whose model isn't in pi-ai's catalog. Result:
the field is **entirely absent** from the request → 400.

**Compaction is NOT the cause**: session data shows 16 whitespace-only reasoning
blocks (length 1, single space `" "`) and 0 truly-empty blocks; all replayable
assistant messages carry a valid `replayState`. The loss happens purely in
pi-ai's serialization, not in compaction or replay state.

**Configurations affected**: any route using DeepSeek reasoning models through
the `@earendil-works/pi-ai` `openai-completions` adapter — including proxies
(bai, tokenrouter, openrouter) that forward to DeepSeek's API. The `isDeepSeek`
detection fails for proxies, so `requiresReasoningContentOnAssistantMessages` is
false and the field is absent rather than empty.

## Why the pi-ai patch attempt was abandoned (2026-08-26)

An earlier fix patched `@earendil-works/pi-ai/dist/api/openai-completions.js`
filter to keep whitespace-only thinking blocks that carry a `thinkingSignature`:

```js
.filter((block) => block.thinking.trim().length > 0 || block.thinkingSignature);
```

This was loaded and working in the runtime, but the **400 still recurred** — it
only covered the whitespace case, not the tool-call-only-without-reasoning case
(the fallback never fired because `model.reasoning` was false for bai). Also,
DSH's self-update (2026-08-26) moved pi-ai **inside `app.asar`** (no longer in
`app.asar.unpacked`), so patching the unpacked file stopped being possible and
any asar patch would be wiped by the next update anyway. The patch file was
retired to `~/.dsh/backups-20260826/patches/`.

## Final fix: settings.yaml (user config, survives updates)

**File**: `~/.dsh/settings.yaml` — provider `bai`, model `deepseek-v4-flash`:

```yaml
- id: deepseek-v4-flash
  reasoningEfforts:
    off: null
    low: "low"
    medium: "medium"
    high: "high"
  compat:
    requiresReasoningContentOnAssistantMessages: true
    supportsDeveloperRole: false
```

**Why this works**:
- `reasoningEfforts` → `model.reasoning = true` → pi-ai's fallback
  `assistantMsg.reasoning_content = ""` now fires for every assistant message
  that has no reasoning_content (whitespace blocks dropped by the filter AND
  tool-call-only messages without reasoning). DeepSeek accepts `""` as "passed
  back". Also enables the reasoning-effort selector in the DSH UI.
- `off: null` → `model.thinkingLevelMap.off === null` → pi-ai does **not** send
  `reasoning_effort: "none"` when no effort is chosen → the model keeps its
  server-side default thinking. No wire change when the user leaves the level
  unset.
- `requiresReasoningContentOnAssistantMessages: true` → forces the fallback on
  (it is normally gated on `isDeepSeek`, which is false for bai).
- `supportsDeveloperRole: false` → **critical**: with `model.reasoning = true`,
  pi-ai would otherwise send the system prompt with role `developer`
  (`useDeveloperRole = model.reasoning && compat.supportsDeveloperRole`), and
  b.ai rejects that role (`unknown variant 'developer'` → 400). Setting it
  false keeps role `system`. This was the missing piece that broke the first
  attempt (2026-08-26, reverted).

**Schema rule learned the hard way**: in `dsh-llm-pi-ai`'s strict Zod schema,
`reasoningEfforts` wire values must be strings for every level **except** `off`,
which is the only one allowed to be `null` (`only "off" may leave it empty`).
Writing `low: null` fails validation of the whole `llm-pi-ai` plugin → all
custom providers disappear and the "add custom provider" UI breaks. Validate
provider edits against the schema, not by eye.

## Verification (live session, 2026-08-26)

- After restart with the fix: reasoning-effort selector appeared for
  bai/deepseek-v4-flash (model.reasoning became true).
- No `developer`-role 400 (supportsDeveloperRole: false held).
- **Long-session stress test passed**: a context-heavy working session (deep
  study of a large codebase — many assistant tool-call messages, thousands of
  lines read) produced ZERO reasoning_content 400s. This workload is exactly
  what used to trigger the bug before the fix.
- Occasional "retried model request 2/5" is dsh-llm-retry on transient
  network/load (5xx, timeouts) — pre-existing, unrelated to this bug.
- Backup of the pre-fix file: `~/.dsh/settings.yaml.pre-fix-20260826`.

## Configuration to apply (active fix)

`~/.dsh/settings.yaml`, provider `bai`, model `deepseek-v4-flash`:

```yaml
- id: deepseek-v4-flash
  reasoningEfforts:
    off: null
    low: "low"
    medium: "medium"
    high: "high"
  compat:
    requiresReasoningContentOnAssistantMessages: true
    supportsDeveloperRole: false
```

> **Note**: a first attempt WITHOUT `supportsDeveloperRole: false` broke every
> request with `unknown variant 'developer'` (pi-ai sends the system prompt
> with role `developer` whenever `model.reasoning && compat.supportsDeveloperRole`,
> and b.ai does not accept that role). The complete block above — with
> `supportsDeveloperRole: false` — is the working fix.

## Upstream issues worth opening (pi-ai / DSH)

Both are real pi-ai bugs affecting any proxy user, not just bai:
1. `supportsDeveloperRole` defaults to `true` for any unknown
   OpenAI-compatible provider, but many proxies don't accept the `developer`
   role. Detection should be conservative (opt-in), not opt-out.
2. `isDeepSeek` detection (`provider === "deepseek" ||
   baseUrl.includes("deepseek.com")`) misses DeepSeek models served through
   proxies (`model.id` contains "deepseek", baseUrl doesn't). Reasoning pass-back
   requirement should key off the model id, not the endpoint.

## References

- pi-ai issues: #3636, #3693, #3705 (https://github.com/earendil-works/pi)
- DSH discussions: #1850, #3857, #2865 (https://github.com/deepseek-ai/deepseek-harness/discussions)
- DeepSeek docs: thinking-mode reasoning_content pass-back requirement
  (https://api-docs.deepseek.com/guides/thinking_mode/)

## Files touched in this repo (all pushed)

- `DSH_CONTEXT_BUG.md` — this document.
- `webtools_bridge.py` — anti-bot challenge detection (`_is_challenge_text`,
  `challenge: true` in read output), wayback fallback on interstitials.
- `js_engine/render_worker.js` — Deno happy-dom render worker (v3).
- `deno/` — vendored Deno 2.7.7 + happy-dom cache (gitignored, ~130 MB).
- `README.md`, `knowledge.md`, `AGENTS.md` — documented the third execution
  mode (DeepSeek Harness bridge) and git sandbox fix.
- `SKILL.md` (`~/.dsh/skills/hermes-web-tools/`) — render, challenge field.
