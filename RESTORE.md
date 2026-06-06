# Hermes Custom Tools — Autonomous Recovery

**Canonical repo:** `D:\Arx\Software Downloads\Hermes copy\hermes-dev`  
**CONTEXT:** `D:\Arx\Software Downloads\Hermes copy\hermes-dev\CONTEXT.md`  
**Restore script:** `D:\Arx\Software Downloads\Hermes copy\hermes-dev\restore.ps1`

## What this is
Production-grade recovery kit for Hermes custom DDG integration. Used when Hermes updates break or wipe context, or when resuming development after a session reset.

## What gets restored
`C:\Users\sucot\.hermes\` gets these files from repo:
- `hermes-agent/tools/ddg_search_tool.py`
- `hermes-agent/tools/browser_dialog_tool.py`
- `plugins/web-tools/ddg/ddg_search.py`
- `plugins/web-tools/ddg/visit_website_enhanced.py`
- `CONTEXT.md`
- `skills/restore-context/SKILL.md`

## Quick recovery
Run this to restore custom tools and context:
```powershell
powershell.exe -File 'D:\Arx\Software Downloads\Hermes copy\hermes-dev\restore.ps1'
```

Dry-run first (safe):
```powershell
powershell.exe -File 'D:\Arx\Software Downloads\Hermes copy\hermes-dev\restore.ps1' -DryRun -SkipBackup -NoStopHermes
```

## After restore
Verify with:
```powershell
cd C:\Users\sucot\.hermes
python -m py_compile plugins\web-tools\ddg\ddg_search.py
python -m py_compile plugins\web-tools\ddg\visit_website_enhanced.py
python -m py_compile hermes-agent\tools\ddg_search_tool.py
```

## Development workflow
1. Edit files in `D:\Arx\Software Downloads\Hermes copy\hermes-dev\` only
2. `git add/commit` in repo
3. Run restore.ps1 to apply

## Last known state
Date: 2026-06-06  
State: `web_search_deep` working, `visit_website_tool` working, `image_search` working.  
Classifier uses keyword-only logic; authoritative-domain list removed.
