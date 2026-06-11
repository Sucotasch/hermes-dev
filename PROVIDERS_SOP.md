# Custom Provider SOP: Qwen + DeepSeek Free Proxies

Living reference for restoring, verifying, and reconnecting the two local free-API proxies used with Hermes.
Stored in external repo `D:\Arx\Software Downloads\Hermes copy\hermes-dev\`. Update skill alongside this file.

---

## 1. Qwen — FreeQwenApi

### What it is
Local OpenAI-compatible proxy. Uses a LiteLLM-style bridge (`qwen_light_proxy.py`) with the Qwen provider.
Working model on this account: **`qwen3.7-max`**.

### Files / paths
- Repo: `D:\Arx\Software Downloads\Hermes copy\FreeQwenApi\`
- Script: `qwen_light_proxy.py`
- Start script: `Qwen Proxy Start.bat`
- Stop script: `Qwen Proxy Stop.bat`
- Tokens: `session/tokens.json` (flat token array for the session)
- Port: **`3264`**
- Model endpoint: `http://127.0.0.1:3264/api/v2/chat/completions`

### Provider config for Hermes
- Provider key: **`custom:freeqwen`**
- Model: **`qwen3.7-max`**
- Constraint: do **not** put `tools:` under this provider entry. If tools are present, Hermes falls back to legacy/local dispatch and breaks tool-calling.
- The client **must** call with `stream=true`. `stream=false` on `/api/v2/chat/completions` returns `Bad_Request`. Hermes config must honor this.

### Start / restore (user)
1. Open PowerShell as the Windows user.
2. `cd D:\Arx\Software Downloads\Hermes copy\FreeQwenApi`
3. Run `Qwen Proxy Start.bat` — it launches `python qwen_light_proxy.py`.
4. Confirm health uses HTTP (not Chrome-only).
   - `curl -s http://127.0.0.1:3264/health`
   - Must contain `ok:true` and `account_loaded:true`.

### Failure modes (tested)
- `-block` in response: network/auth block. Do not retry the same way; inspect proxy logs for auth state.
- `not found` for model names older than current supported alias on the account. Use `qwen3.7-max`.
- Completion-style call with `stream=false` fails with `Bad_Request` due to provider constraint. Client must force `stream=true`.

### Stop (user)
- `Qwen Proxy Stop.bat` or Ctrl+C in the console where it was launched.

---

## 2. DeepSeek — FreeDeepseekAPI

### What it is
Node-based local proxy for DeepSeek Web Chat. Captures browser auth via Chrome and relays chat API requests under a saved session.

### Files / paths
- Repo: `D:\Arx\Software Downloads\Hermes copy\FreeDeepseekAPI\`
- Scripts: `scripts/deepseek_chrome_auth.js`
- Server: `server.js`
- Port: **`9655`**
- Auth file: `deepseek-auth.json` (token + cookies + hif headers)
- Models endpoint: `http://127.0.0.1:9655/v1/models`
- Chat endpoint: `http://127.0.0.1:9655/v1/chat/completions` (OpenAI-compatible)
- Additional shims: `POST /v1/messages` (Anthropic), `POST /v1/responses` (OpenAI Responses)

### Verify the auth file is fresh
- If `deepseek-auth.json` missing or token expired (~1-2 hours TTL), re-auth before running the server.
- Do not overwrite with a non-interactive run; the script needs a warm browser session, test message, and Enter.

### Re-auth procedure (user-required, minimal)
1. Open PowerShell **as the user** (not Hermes terminal) and `cd` to repo:
   ```powershell
   cd D:\Arx\Software Downloads\Hermes copy\FreeDeepseekAPI
   ```
2. Run one-shot:
   ```powershell
   $env:CHROME_PATH="C:\Program Files\Google\Chrome\Application\chrome.exe";
   $env:DEEPSEEK_REUSE_CHROME_PROFILE="1";
   $env:DEEPSEEK_KEEP_CHROME_PROFILE="1";
   node scripts/deepseek_chrome_auth.js
   ```
3. User tasks inside the opened Chrome window:
   - Log into `chat.deepseek.com`.
   - Send any test message (e.g. `ok`) to warm the session.
   - Return to the PowerShell and press **Enter**.
4. Expect: `deepseek-auth.json` written; script prints `token: OK (64 chars)` and a chat URL.

### Start the server (agent + user)
- Preferred non-interactive mode:
  ```powershell
  $env:SKIP_ACCOUNT_MENU="1"; $env:NON_INTERACTIVE="1";
  node server.js
  ```
- Do **not** launch `server.js` interactively via background terminal; it drops into the account/mode menu and blocks.

### Route rules for agentic use
- Always use **`deepseek-chat`** for tool-using / multi-step agentic use.
- `deepseek-reasoner`, `deepseek-r1`, `deepseek-v4-pro` are reasoning models with an incompatible format for agentic tool selection on this proxy. Do not use them for planner / coder agents.

### Hermes config
- Provider key: **`custom:freedeep`**
- Model: **`deepseek-chat`**

---

## 3. Verification matrix

| Check | Command | Expected |
|-------|---------|----------|
| Qwen health | `curl -s http://127.0.0.1:3264/health` | JSON with `ok:true`, `account_loaded:true` |
| Qwen chat | `curl -s -X POST http://127.0.0.1:3264/api/v2/chat/completions -H "Content-Type: application/json" -d "{\"model\":\"qwen3.7-max\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"stream\":true}"` | SSE stream with choices |
| DeepSeek root | `curl -s http://127.0.0.1:9655/` | `{"status":"ok","config_ready":true,...}` |
| DeepSeek models | `curl -s http://127.0.0.1:9655/v1/models` | `deepseek-chat` in `data[].id` |
| DeepSeek chat | `curl -s -X POST http://127.0.0.1:9655/v1/chat/completions -H "Content-Type: application/json" -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"OK"}],"stream":false}'` | OpenAI-format completion JSON |

---

## 4. Common mistakes (observed)

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Cannot find module .../scripts/deepseek_chrome_auth.js` | Ran `node` from `C:\WINDOWS\system32` with relative path, or path contained backslashes interpreted incorrectly in Git Bash/MSYS | Always `cd` into the repo first. Use quoted POSIX path `D:/Arx/...` or PowerShell `D:\Arx\...`. |
| `EPERM rename .chrome-for-testing-profile-deepseek` | A prior Chrome instance using that profile is still alive; script tries to delete it | Set `DEEPSEEK_REUSE_CHROME_PROFILE=1` and `DEEPSEEK_KEEP_CHROME_PROFILE=1` before running `node scripts/deepseek_chrome_auth.js`. |
| Auth script never finishes / no `deepseek-auth.json` | The script waits for Enter in the same terminal where it started | Press Enter after login in that PowerShell. |
| `Bad_Request` from Qwen chat with `stream=false` | Provider contract requires streaming | Always call with `"stream":true`. |
| DeepSeek menu loop / blocks background | `server.js` launched interactively; it shows the mode menu and waits for input | Use `SKIP_ACCOUNT_MENU=1` and `NON_INTERACTIVE=1` environment variables. |
| Mojibake in Windows console output | PowerShell / Node codepage vs UTF-8 | Ignore if curl health checks pass. |
| Port already in use after Hermes restart / duplicate launch | Leftover Node process or Hermes dev proxy bound to 9655/3264 | `tasklist | findstr node` then kill the orphan PID; or rerun the formal Start.bat for Qwen. |

---

## 5. Recovery after Hermes update or crash

1. Restore `~/.hermes` from the dev repo:
   ```powershell
   powershell -ExecutionPolicy Bypass -File "D:\Arx\Software Downloads\Hermes copy\hermes-dev\restore.ps1"
   ```
2. Restart both proxies per this SOP.
3. Re-validate with the table in §3.

Shared reference file:  
`D:\Arx\Software Downloads\Hermes copy\hermes-dev\PROVIDERS_SOP.md`
