---
name: restore-context
description: Restore Hermes custom tooling context after an update or context wipe. Loads canonical state from the external dev repo and applies it.
triggers:
  - recovery
  - restore
  - context lost
  - after hermes update
  - resume development
---

# Restore Context

Use this skill when the current session lost context about custom Hermes tooling
or after a Hermes update that may have removed custom integrations.

## Source of truth

- Repo: `D:\Arx\Software Downloads\Hermes copy\hermes-dev`
- Project knowledge: `D:\Arx\Software Downloads\Hermes copy\hermes-dev\knowledge.md`
- Restore script: `D:\Arx\Software Downloads\Hermes copy\hermes-dev\restore.ps1`

## Recovery flow

1. Read `D:\Arx\Software Downloads\Hermes copy\hermes-dev\knowledge.md`.
2. Run restoration:
   - Dry run first: `powershell.exe -File 'D:\Arx\Software Downloads\Hermes copy\hermes-dev\restore.ps1' -DryRun`
   - Then real restore: `powershell.exe -File 'D:\Arx\Software Downloads\Hermes copy\hermes-dev\restore.ps1'`
3. After restore, verify:
   - `python -m py_compile` for key files
   - registry discovery shows `web_search_deep`, `visit_website_tool`, `image_search`
4. Continue development from repo, not from live `~/.hermes` edits.

## Rules

- Always edit files in the external repo first.
- Commit before applying changes.
- Never edit `~/.hermes` custom files directly without mirroring to the repo.
