# DSH Context-Overflow Bug — diagnosis & workarounds

Status: **diagnosed, not fixed** (fix belongs in the DSH app itself).
Recorded: 2026-08-24 (session kept dying on this error).

## Symptom

Long DeepSeek Harness (DSH) sessions periodically die mid-turn with:

```
This turn failed 400: {"message":"The reasoning_content in the thinking mode
must be passed back to the API.","type":"invalid_request_error"}
```

The whole turn is lost (files on disk survive). Happens when the session
context overflows and DSH compacts/trims history.

## Root cause (from DSH sources, `D:\Works\DSH Desktop\resources\app.asar.unpacked\`)

1. `node_modules\@earendil-works\pi-ai\dist\api\openai-completions.js` lines 924-928:
   when `compat.requiresReasoningContentOnAssistantMessages` is set (it is, for
   DeepSeek — line 1155 `isDeepSeek`) and the model uses `reasoning`, DSH forces
   `assistantMsg.reasoning_content = ""` when the field is missing.
2. DeepSeek's API rejects that: in thinking mode every prior assistant message
   must carry its real `reasoning_content` back — an empty string is not enough
   → 400 invalid_request_error.
3. The missing field appears after context compaction/trimming: old messages get
   pruned and their `reasoning_content` is dropped (see
   `pi-ai\dist\utils\estimate.js` — "compaction summary"). The next request then
   sends `""` for DeepSeek → API rejects → turn dies.

Also relevant: `openai-completions.js` 929-938 skips assistant messages with no
content and no tool_calls — aborted responses may be dropped here.

## Workarounds (do before the error hits)

- **Compact the session manually, early** (before near-full): the GUI's manual
  compact/checkpoint prevents the destructive auto-trim.
- **Keep sessions lean**: prefer subagents for heavy research; keep tool-output
  payloads small; commit often (git is the durable memory).
- Check DSH settings for context limit / auto-compact behavior.

## Proper fix (upstream, in the DSH app)

- In `openai-completions.js` ~line 924: for DeepSeek, do NOT synthesize an empty
  `reasoning_content`. Either preserve `reasoning_content` during compaction or
  skip such assistant messages entirely (the existing skip at 937 handles
  contentless ones — extend it to reasoning-only ones before serialization).
- Preserve `reasoning_content` across context trimming in the session store.

## Files touched in this repo (all pushed)

- `webtools_bridge.py` — anti-bot challenge detection (`_is_challenge_text`,
  `challenge: true` in read output), wayback fallback on interstitials.
- `js_engine/render_worker.js` — Deno happy-dom render worker (v3).
- `deno/` — vendored Deno 2.7.7 + happy-dom cache (gitignored, ~130 MB).
- `README.md`, `knowledge.md`, `AGENTS.md` — documented the third execution
  mode (DeepSeek Harness bridge) and git sandbox fix.
- `SKILL.md` (`~/.dsh/skills/hermes-web-tools/`) — render, challenge field.
