# DSH reasoning_content 400 Bug — refined diagnosis & fix

Status: **FIXED by patching pi-ai v0.82.1 in the installed DSH runtime**.
Recorded: 2026-08-24 (initial diagnosis), 2026-08-26 (refined diagnosis + fix applied).

## Symptom

DeepSeek Harness (DSH) sessions fail mid-turn with:

```
This turn failed 400: {"message":"The reasoning_content in the thinking mode
must be passed back to the API.","type":"invalid_request_error"}
```

The whole turn is lost (files on disk survive). The error appears when an
assistant message that originally had reasoning_content (even whitespace-only)
is replayed in a subsequent request without the field.

## Root cause (refined, from real session data + pi-ai code)

**Primary trigger**: The model produces `reasoning_content: " "` (single space)
on tool-call-only turns — stored in the durable session as
`{"type":"reasoning","text":" "}`. On replay, pi-ai's serialization filter
`block.thinking.trim().length > 0` drops this whitespace-only thinking block
entirely, so `assistantMsg.reasoning_content` is never set on the outbound
message. DeepSeek's API (behind any proxy) requires the field to be passed back
verbatim → 400.

**Mechanism** (in `openai-completions.js` lines 848-856, pi-ai v0.82.1):

```js
// nonEmptyThinkingBlocks filter drops whitespace-only reasoning blocks:
const nonEmptyThinkingBlocks = msg.content
    .filter(isThinkingContentBlock)
    .filter((block) => block.thinking.trim().length > 0);
```

For tool-call assistant messages, the model emits `" "` as reasoning_content.
The `" ".trim().length > 0` check is false → block dropped. Then:
- If `isDeepSeek` = true (direct deepseek provider): the fallback at line 924-928
  sets `reasoning_content = ""` (empty string) — but DeepSeek rejects empty too.
- If `isDeepSeek` = false (proxy like bai, tokenrouter): the fallback never fires
  → `reasoning_content` is **entirely absent** from the request → 400.

**Compaction is NOT the cause**: session data shows 16 whitespace-only reasoning
blocks (length 1, single space `" "`) and 0 truly-empty blocks. The bug is
purely in pi-ai's serialization filter, not in compaction.

**Configurations affected**: any route using DeepSeek reasoning models through
the `@earendil-works/pi-ai` `openai-completions` adapter — including proxies
(bai, tokenrouter, openrouter) that forward to DeepSeek's API. The `isDeepSeek`
detection (`provider === "deepseek" || baseUrl.includes("deepseek.com")`) fails
for proxies, so `requiresReasoningContentOnAssistantMessages` is false and the
field is absent rather than empty.

## Fix applied (2026-08-26)

**File**: `D:\Works\DSH Desktop\resources\app.asar.unpacked\node_modules\@earendil-works\pi-ai\dist\api\openai-completions.js`
(line 848-856)

**Change**: Keep thinking blocks that carry a `thinkingSignature` (e.g. DeepSeek's
`"reasoning_content"`) even when their text is whitespace-only:

```js
// Before (buggy):
.filter((block) => block.thinking.trim().length > 0);

// After (fixed):
.filter((block) => block.thinking.trim().length > 0 || block.thinkingSignature);
```

This ensures the whitespace-only reasoning block is included in
`nonEmptyThinkingBlocks`, so the existing code at line 875-881 sets
`assistantMsg.reasoning_content = " "` (the verbatim original whitespace
text) — satisfying DeepSeek's "pass reasoning_content back" rule.

**Why this works**: The `thinkingSignature` ("reasoning_content") is the signal
that the block came from a DeepSeek-style reasoning field that must be
round-tripped. Blocks without a signature (e.g. plain llama.cpp thinking) are
still filtered out when whitespace-only — no behavior change for other providers.

**Backup created**: `openai-completions.js.bak-2026-08-26` (same directory).

## Requires restart

DSH caches the patched module in memory. The fix takes effect next time the
DSH Desktop app is restarted. Node module resolution is from
`app.asar.unpacked\node_modules\@earendil-works\pi-ai` — the only copy.

## Risks

- **DSH auto-update** may overwrite the patched file. The fix is in pi-ai
  v0.82.1's `dist/` (compiled output); an update that replaces the node_modules
  tree will revert it. Re-apply after updates.
- **The fix lives in the installed DSH runtime**, not in the Hermes repo.
  Documented here as `openai-completions.js.patch`.
- **No regression for other providers**: the extra `|| block.thinkingSignature`
  condition only fires for blocks with a signature AND whitespace-only text.
  Normal reasoning blocks (non-whitespace) are unaffected. Blocks without a
  signature are still dropped when whitespace-only (same as before).

## Verification (session data, 2026-08-26)

- Decompressed current session: 7,184,345 bytes, 671 reasoning blocks
- 16 whitespace-only reasoning blocks (length 1, single space `" "`)
- 0 truly-empty reasoning blocks
- The 400 error at turn 2, step 44 was preceded by whitespace-only reasoning
  blocks in the message history
- Session 06b51b9f (14 turns, completed cleanly) had no whitespace-only
  reasoning → no trigger

## References

- pi-ai issues: #3636, #3693, #3705 (https://github.com/earendil-works/pi)
- DSH discussions: #1850, #3857, #2865 (https://github.com/deepseek-ai/deepseek-harness/discussions)
- DeepSeek docs: thinking-mode reasoning_content pass-back requirement
  (https://api-docs.deepseek.com/guides/thinking_mode/)

## Files touched in this repo (all pushed)

- `webtools_bridge.py` — anti-bot challenge detection (`_is_challenge_text`,
  `challenge: true` in read output), wayback fallback on interstitials.
- `js_engine/render_worker.js` — Deno happy-dom render worker (v3).
- `deno/` — vendored Deno 2.7.7 + happy-dom cache (gitignored, ~130 MB).
- `README.md`, `knowledge.md`, `AGENTS.md` — documented the third execution
  mode (DeepSeek Harness bridge) and git sandbox fix.
- `SKILL.md` (`~/.dsh/skills/hermes-web-tools/`) — render, challenge field.
