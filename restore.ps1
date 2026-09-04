# Restore custom Hermes deep-search pipeline from the dev repo.
#
# One-button tool: run it (directly, or via the GUI "Check & Restore"
# button) after a Hermes update/reinstall, or whenever the pipeline looks
# broken. It:
#   1. syncs custom tools, web plugins and skills into ~/.hermes
#   2. installs missing Python packages into the Hermes venv — FAST-FAIL:
#      one pip attempt per package with short timeouts; a package that
#      can't be installed is reported, never hung on (pip default retries
#      stack minutes of silent network timeouts per package)
#   3. py_compile-checks every synced file
#   4. runs restore_check.py for the verdict: are all 5 tools registered
#      and are all deps importable? Prints OK/BROKEN.
#   5. -RunSmoke: live network probe — one web_search_deep call through
#      the REAL Hermes registry (replaces the dead deep_test_vargas.py
#      reference; verifies the whole chain incl. network + backend).
#
# No backups (v3+): the repo is the source of truth (and it is in git).
# -SkipBackup is accepted for GUI compatibility and does nothing.
# Hermes is NOT stopped by default (use -StopHermes); -NoStopHermes is
# accepted for GUI compatibility and does nothing either.
#
# Usage:
#   .\restore.ps1              # full check + restore
#   .\restore.ps1 -DryRun      # show what would happen, change nothing
#   .\restore.ps1 -RunSmoke    # also run the live network smoke probe
#   .\restore.ps1 -StopHermes  # try to stop Hermes processes first
param(
    [switch]$DryRun,
    [switch]$NoStopHermes,
    [switch]$StopHermes,
    [switch]$RunSmoke,
    [Alias('SkipBackup')] [switch]$NoBackup
)

$ErrorActionPreference = 'SilentlyContinue'
$script:Log = @()
function Log($msg) {
    $ts = Get-Date -Format 'HH:mm:ss'
    $script:Log += "[$ts] $msg"
    Write-Host $msg
}

$sw = [System.Diagnostics.Stopwatch]::StartNew()

function Stop-HermesIfRunning {
    # Opt-in: WMI (Get-CimInstance) is unreliable on some systems, so the
    # default path never touches it. Files are loaded at import time and are
    # safe to overwrite while Hermes runs. The GUI passes -NoStopHermes.
    if (-not $StopHermes) {
        Log 'Not stopping Hermes (default; use -StopHermes to stop first).'
        return
    }
    if ($DryRun) {
        Log 'Skipping Hermes stop (DryRun - nothing changes).'
        return
    }
    Log 'Attempting to stop Hermes process...'
    # Bounded: run the query in a background job with a 15s cap, continue
    # either way so the script can never hang on process enumeration.
    $job = Start-Job -ScriptBlock {
        Get-CimInstance Win32_Process | Where-Object {
            $_.Name -match 'hermes|gui_launcher' -or
            (($_.Name -match '^python|^node') -and $_.CommandLine -match 'hermes')
        }
    }
    $procs = $null
    if (Wait-Job -Id $job.Id -Timeout 15 -ErrorAction SilentlyContinue) {
        $procs = Receive-Job -Id $job.Id -ErrorAction SilentlyContinue
    } else {
        Log 'Process query timed out (WMI) - skipping stop, continuing.'
        Stop-Job -Id $job.Id -ErrorAction SilentlyContinue
    }
    Remove-Job -Id $job.Id -Force -ErrorAction SilentlyContinue
    if ($procs) {
        foreach ($p in $procs | Select-Object -First 5) {
            Log "Found process: $($p.Name) (PID $($p.ProcessId))"
            try {
                Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
                Log "Stopped PID $($p.ProcessId)"
            } catch {
                Log "Could not stop PID $($p.ProcessId): $_"
            }
        }
    } else {
        Log 'No matching processes found.'
    }
}

$RepoRoot = $PSScriptRoot
if (-not $RepoRoot) { $RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $RepoRoot) { $RepoRoot = Get-Location }
$HermesHome = Join-Path $env:USERPROFILE '.hermes'

if (-not (Test-Path $RepoRoot))   { Log "ERROR: Repo not found at $RepoRoot"; exit 1 }
if (-not (Test-Path $HermesHome)) { Log "ERROR: Hermes home not found at $HermesHome"; exit 1 }

$Venv = Join-Path $HermesHome 'hermes-agent\venv\Scripts\python.exe'
$CheckPy = Join-Path $RepoRoot 'restore_check.py'
$SmokePy = Join-Path $RepoRoot 'restore_smoke.py'
$HadFailure = $false

Log "Repo: $RepoRoot"
Log "Hermes: $HermesHome"
if ($DryRun) { Log 'Running in DryRun mode; no changes applied.' }
if ($NoBackup) { Log 'Note: backups were removed in v3 (git repo is the source of truth).' }

# ---- Manifest: exactly what we sync (repo wins) ------------------------------
# compose.py added v4: ddg_search.py imports it lazily when the agent calls
# web_deep_research(..., compose=True) — a live copy without it silently
# falls back to raw JSON.
$Files = @(
    @{ Repo = 'hermes-agent\tools\ddg_search_tool.py'; Dest = 'hermes-agent\tools\ddg_search_tool.py' }
    @{ Repo = 'hermes-agent\tools\browser_dialog_tool.py'; Dest = 'hermes-agent\tools\browser_dialog_tool.py' }
    @{ Repo = 'plugins\web-tools\ddg\ddg_search.py'; Dest = 'plugins\web-tools\ddg\ddg_search.py' }
    @{ Repo = 'plugins\web-tools\ddg\visit_website_enhanced.py'; Dest = 'plugins\web-tools\ddg\visit_website_enhanced.py' }
    @{ Repo = 'plugins\web-tools\ddg\query_variants.py'; Dest = 'plugins\web-tools\ddg\query_variants.py' }
    @{ Repo = 'plugins\web-tools\ddg\selection.py'; Dest = 'plugins\web-tools\ddg\selection.py' }
    @{ Repo = 'plugins\web-tools\ddg\_coverage.py'; Dest = 'plugins\web-tools\ddg\_coverage.py' }
    @{ Repo = 'plugins\web-tools\ddg\_common.py'; Dest = 'plugins\web-tools\ddg\_common.py' }
    @{ Repo = 'plugins\web-tools\ddg\sieve.py'; Dest = 'plugins\web-tools\ddg\sieve.py' }
    @{ Repo = 'plugins\web-tools\ddg\junk_filter.py'; Dest = 'plugins\web-tools\ddg\junk_filter.py' }
    @{ Repo = 'plugins\web-tools\ddg\discovery.py'; Dest = 'plugins\web-tools\ddg\discovery.py' }
    @{ Repo = 'plugins\web-tools\ddg\evidence_rank.py'; Dest = 'plugins\web-tools\ddg\evidence_rank.py' }
    @{ Repo = 'plugins\web-tools\ddg\compose.py'; Dest = 'plugins\web-tools\ddg\compose.py' }
    @{ Repo = 'plugins\web-tools\ddg\resources\Imagus_sieve_2026.07.15_823.json'; Dest = 'plugins\web-tools\ddg\resources\Imagus_sieve_2026.07.15_823.json' }
    @{ Repo = 'plugins\web-tools\ddg\resources\junk_allowlist.txt'; Dest = 'plugins\web-tools\ddg\resources\junk_allowlist.txt' }
    @{ Repo = 'skills\restore-context\SKILL.md'; Dest = 'skills\restore-context\SKILL.md' }
    @{ Repo = 'skills\web-deep-search\SKILL.md'; Dest = 'skills\web-deep-search\SKILL.md' }
)

Stop-HermesIfRunning

# ---- 1) Sync (fast: ~18 small files, milliseconds) ---------------------------
foreach ($f in $Files) {
    $src = Join-Path $RepoRoot $f.Repo
    $dst = Join-Path $HermesHome $f.Dest
    if (-not (Test-Path $src)) {
        Log "WARNING: missing in repo (skipping): $($f.Repo)"
        continue
    }
    $dstDir = Split-Path $dst -Parent
    if (-not (Test-Path $dstDir)) {
        New-Item -Path $dstDir -ItemType Directory -Force | Out-Null
    }
    if ($DryRun) {
        Log "DRY-RUN copy: $($f.Repo)"
    } else {
        # Copy only when bytes differ — keeps mtimes stable for reruns.
        $same = $false
        if (Test-Path $dst) {
            $srcHash = (Get-FileHash $src -Algorithm MD5).Hash
            $dstHash = (Get-FileHash $dst -Algorithm MD5).Hash
            $same = ($srcHash -eq $dstHash)
        }
        if ($same) {
            Log "unchanged: $($f.Repo)"
        } else {
            Copy-Item $src $dst -Force
            Log "Synced: $($f.Repo)"
        }
    }
}

# ---- 2) Dependencies: one combined import probe, then one fast-fail pip pass --
if (Test-Path $Venv) {
    $Deps = @(
        @{ Pkg = 'ddgs';          Mod = 'ddgs' }
        @{ Pkg = 'beautifulsoup4'; Mod = 'bs4' }
        @{ Pkg = 'trafilatura';   Mod = 'trafilatura' }
        @{ Pkg = 'htmldate';      Mod = 'htmldate' }
        @{ Pkg = 'lxml';          Mod = 'lxml' }
        @{ Pkg = 'curl_cffi';     Mod = 'curl_cffi' }
        @{ Pkg = 'httpx';         Mod = 'httpx' }
        @{ Pkg = 'pillow';        Mod = 'PIL' }
        @{ Pkg = 'pypdf';         Mod = 'pypdf' }
    )
    # Single python process checks ALL deps at once (~1s instead of 7-9
    # interpreter startups at 200-800ms each), prints one line per dep.
    $probeCode = @'
import importlib, json, sys
mods = sys.argv[1].split(',')
out = {m: True for m in mods}
for m in mods:
    try:
        importlib.import_module(m)
    except Exception:
        out[m] = False
print(json.dumps(out))
'@
    $modsArg = ($Deps | ForEach-Object { $_.Mod }) -join ','
    $probeOut = & $Venv -c $probeCode $modsArg 2>$null | Select-Object -Last 1
    $depState = @{}
    try { $depState = $probeOut | ConvertFrom-Json } catch {}
    $missing = @()
    foreach ($d in $Deps) {
        $present = $depState.($d.Mod)
        if ($present) {
            Log "dep OK: $($d.Mod)"
        } else {
            Log "dep missing: $($d.Mod) (pip: $($d.Pkg))"
            $missing += $d.Pkg
        }
    }
    if ($missing.Count -gt 0) {
        if ($DryRun) {
            Log "DRY-RUN would install (one fast-fail pip pass): $($missing -join ', ')"
        } else {
            Log "Installing $($missing.Count) missing package(s): $($missing -join ', ') ..."
            # FAST-FAIL: single pip invocation for ALL missing packages with
            # one retry and short socket timeout. pip's defaults (--retries 5
            # x 75s connect) stack minutes of silent hangs per package when
            # the network/index is unreachable — that is what made restore
            # appear to 'copy files extremely slowly'.
            $pipArgs = @('-m', 'pip', 'install', '--disable-pip-version-check',
                          '--retries', '1', '--timeout', '15', '--') + $missing
            $pipOut = & $Venv @pipArgs 2>&1
            if ($LASTEXITCODE -eq 0) {
                Log "deps installed: $($missing -join ', ')"
            } else {
                Log 'dep INSTALL FAILED (offline or pip error) - pipeline still synced;'
                Log '  install manually when online:  venv\Scripts\pip install ' + ($missing -join ' ')
                $pipOut | Select-Object -Last 6 | ForEach-Object { Log "    $_" }
                $HadFailure = $true
            }
        }
    }
} else {
    Log "WARNING: venv not found at $Venv - cannot install deps"
    $HadFailure = $true
}

# ---- 3) Compile checks (bounded) ---------------------------------------------
$Targets = @(
    'plugins\web-tools\ddg\ddg_search.py',
    'plugins\web-tools\ddg\visit_website_enhanced.py',
    'plugins\web-tools\ddg\query_variants.py',
    'plugins\web-tools\ddg\selection.py',
    'plugins\web-tools\ddg\sieve.py',
    'plugins\web-tools\ddg\junk_filter.py',
    'plugins\web-tools\ddg\discovery.py',
    'plugins\web-tools\ddg\evidence_rank.py',
    'plugins\web-tools\ddg\compose.py',
    'plugins\web-tools\ddg\_coverage.py',
    'plugins\web-tools\ddg\_common.py',
    'hermes-agent\tools\ddg_search_tool.py',
    'hermes-agent\tools\browser_dialog_tool.py'
)
if (Test-Path $Venv) {
    # One python process compiles ALL targets (~1.5s instead of 13 separate
    # interpreter startups, ~200ms each).
    $compileCode = @'
import py_compile, sys
failed = []
for path in sys.argv[1:]:
    try:
        py_compile.compile(path, doraise=True)
    except Exception as e:
        failed.append((path, str(e)[:200]))
if failed:
    for p, e in failed:
        print('FAIL\t' + p + '\t' + e)
    sys.exit(1)
print('COMPILED\t' + str(len(sys.argv) - 1))
'@
    $targetPaths = @()
    foreach ($rel in $Targets) {
        $path = Join-Path $HermesHome $rel
        if (-not (Test-Path $path)) {
            Log "MISSING target for compile check: $rel"
            $HadFailure = $true
            continue
        }
        $targetPaths += $path
    }
    if ($targetPaths.Count -gt 0) {
        if ($DryRun) {
            foreach ($p in $targetPaths) { Log "DRY-RUN py_compile: $p" }
        } else {
            $compileOut = & $Venv -c $compileCode @targetPaths 2>&1
            if ($LASTEXITCODE -eq 0) {
                Log "py_compile OK: $($targetPaths.Count) files"
            } else {
                foreach ($line in $compileOut) {
                    if ($line -match '^FAIL\t(.+)\t(.+)$') {
                        Log "py_compile FAILED: $($Matches[1]) -> $($Matches[2])"
                    } else {
                        Log "    $line"
                    }
            }
                $HadFailure = $true
            }
        }
    }
}

# ---- 4) Final verdict: are the tools really live? ----------------------------
if (Test-Path $CheckPy) {
    if ($DryRun) {
        Log 'DRY-RUN would run: pipeline health check (restore_check.py)'
    } elseif (Test-Path $Venv) {
        Log 'Running pipeline health check...'
        $chkOut = & $Venv $CheckPy 2>&1
        $chkExit = $LASTEXITCODE
        foreach ($line in $chkOut) { Log "  $line" }
        if ($chkExit -ne 0) {
            Log 'VERDICT: pipeline NOT restored - review log above'
            $HadFailure = $true
        } else {
            Log 'VERDICT: pipeline OK - all tools registered, deps present'
        }
    } else {
        Log 'VERDICT: cannot check (no venv)'
        $HadFailure = $true
    }
} else {
    Log "MISSING restore_check.py next to restore.ps1 ($CheckPy)"
    $HadFailure = $true
}

# ---- Optional live smoke probe (off by default; needs network) ---------------
# One real web_search_deep call through the REAL Hermes registry (stub-free):
# loads the wrapper exactly like Hermes does, discovers builtin tools, calls
# the tool handler with validate=5, expects >0 results. Verifies the whole
# chain: registry -> wrapper -> plugins -> network -> backend.
if ($RunSmoke -and -not $DryRun -and -not $HadFailure) {
    if (Test-Path $SmokePy) {
        Log 'Running live smoke probe (web_search_deep, ~30-60s)...'
        $smokeOut = & $Venv $SmokePy 2>&1
        $smokeExit = $LASTEXITCODE
        foreach ($line in ($smokeOut | Select-Object -Last 6)) { Log "  $line" }
        if ($smokeExit -eq 0) {
            Log 'smoke probe: OK'
        } else {
            Log 'smoke probe: FAILED'
            $HadFailure = $true
        }
    } else {
        Log "Smoke probe script not found ($SmokePy) - skipping."
    }
} elseif ($RunSmoke) {
    Log 'Smoke probe skipped (DryRun or previous failures).'
} else {
    Log 'Smoke probe skipped (default; use -RunSmoke for live check).'
}

$sw.Stop()

# ---- Save restore log --------------------------------------------------------
$logDir = Join-Path $HermesHome '.restore-log'
New-Item -Path $logDir -ItemType Directory -Force | Out-Null
$logFile = Join-Path $logDir ("restore_" + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.log')
$script:Log | Out-File $logFile -Encoding utf8
Log "Log saved to $logFile"
$secs = [math]::Round($sw.Elapsed.TotalSeconds, 1)
Log ('Restore finished in ' + $secs + 's.')
if ($HadFailure) {
    Log 'Result: BROKEN - see log. Run again with internet if deps failed.'
    exit 1
}
Log 'Result: OK'
exit 0
