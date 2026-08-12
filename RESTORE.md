# Hermes Custom Tools - One-Button Restore

**Dev repo:** `D:\Arx\Software Downloads\Hermes copy\hermes-dev`
**Restore script:** `D:\Arx\Software Downloads\Hermes copy\hermes-dev\restore.ps1`
**Health check:** `D:\Arx\Software Downloads\Hermes copy\hermes-dev\restore_check.py`

## What this is

A one-button recovery kit for the custom deep-search pipeline in Hermes.
The running Hermes loads its tools, plugins and skills from
`C:\Users\sucot\.hermes\`. After a Hermes update or reinstall that pipeline
is gone or broken. Running restore.ps1 re-connects it from this repo (the
source of truth, also in git) and verifies the result.

No backups are made (v3): the repo is the source of truth. Roll back a bad
sync with `git checkout` + re-run restore.

## What the script does

1. Sync the manifest files into `~/.hermes`: tools (ddg_search_tool,
   browser_dialog_tool), the DDG web plugins (plugins/web-tools/ddg),
   resources, and skills.
2. Install missing Python packages into the Hermes venv (ddgs,
   beautifulsoup4, trafilatura, htmldate, lxml) - updates often recreate
   the venv and wipe them.
3. py_compile every synced file.
4. Run restore_check.py for the verdict: all 5 tools registered
   (web_search_deep, web_expand_and_fetch, visit_website_tool, image_search,
   web_deep_research) and all deps importable. Prints OK / BROKEN,
   exit code 0/1.

## Usage

```
powershell.exe -File "...\restore.ps1"          # full check + restore
powershell.exe -File "...\restore.ps1" -DryRun  # show actions, change nothing
powershell.exe -File "...\restore.ps1" -RunSmoke  # also live compose probe
```

The GUI (standalone/gui.py, "Check & Restore" button) calls the same script
(`-SkipBackup` is accepted for compatibility and does nothing).

## Development workflow

1. Edit files in this repo only.
2. `git add/commit` in the repo.
3. Run restore.ps1 (or the GUI button) to apply to Hermes.

## Verify without running the script

```
C:\Users\sucot\.hermes\hermes-agent\venv\Scripts\python.exe restore_check.py
```
