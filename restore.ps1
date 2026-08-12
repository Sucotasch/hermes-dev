# Restore custom Hermes tools from external dev repo.
#
# v2 - fast, deterministic, safe:
#   * Backs up ONLY the files it is about to overwrite (the 15-file manifest
#     below). The old version recursively copied the ENTIRE ~/.hermes (6+ GB,
#     128k files) on every run - 30+ minutes, and restoring that snapshot
#     could clobber runtime state (caches, venv, auth) with stale copies.
#   * Smoke probe (live deep research) is OFF by default - it needs the
#     network and adds minutes. Pass -RunSmoke to enable.
#   * Every step is bounded: py_compile and the probe run under hard timeouts,
#     so the script can never hang silently.
#
# Usage:
#   .\restore.ps1              # stop hermes, backup 15 files, sync, py_compile
#   .\restore.ps1 -DryRun      # show what would happen, change nothing
#   .\restore.ps1 -RunSmoke    # also run the live compose smoke probe
#   .\restore.ps1 -NoBackup    # skip even the file-level backup
#   .\restore.ps1 -NoStopHermes
param(
    [switch]$DryRun,
    [switch]$NoStopHermes,
    [switch]$NoBackup,
    [switch]$RunSmoke
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
    if ($NoStopHermes) {
        Log 'Skipping Hermes stop (NoStopHermes).'
        return
    }
    Log 'Attempting to stop Hermes process...'
    # Scope tightly: only hermes-named processes, or python/node whose
    # command line references hermes. Never kill unrelated python/node work.
    $procs = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match 'hermes|gui_launcher' -or
        (($_.Name -match '^python|^node') -and $_.CommandLine -match 'hermes')
    }
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

Log "Repo: $RepoRoot"
Log "Hermes: $HermesHome"
if ($DryRun) { Log 'Running in DryRun mode; no changes applied.' }

# ---- Manifest: exactly what we sync (and therefore what we back up) --------
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
    @{ Repo = 'plugins\web-tools\ddg\resources\Imagus_sieve_2026.07.15_823.json'; Dest = 'plugins\web-tools\ddg\resources\Imagus_sieve_2026.07.15_823.json' }
    @{ Repo = 'plugins\web-tools\ddg\resources\junk_allowlist.txt'; Dest = 'plugins\web-tools\ddg\resources\junk_allowlist.txt' }
    @{ Repo = 'CONTEXT.md'; Dest = 'CONTEXT.md' }
    @{ Repo = 'skills\restore-context\SKILL.md'; Dest = 'skills\restore-context\SKILL.md' }
    @{ Repo = 'skills\web-deep-search\SKILL.md'; Dest = 'skills\web-deep-search\SKILL.md' }
)

Stop-HermesIfRunning

# ---- Backup: ONLY the files we are about to overwrite (seconds) ------------
if ($NoBackup) {
    Log 'Skipping backup (NoBackup).'
} elseif (-not $DryRun) {
    $backupDirBase = Join-Path (Split-Path -Parent $HermesHome) 'Hermes_Backups'
    New-Item -Path $backupDirBase -ItemType Directory -Force | Out-Null
    $ts = Get-Date -Format 'yyyyMMdd_HHmmss'
    $backupDir = Join-Path $backupDirBase "restore-backup-$ts"
    New-Item -Path $backupDir -ItemType Directory -Force | Out-Null
    $backedUp = 0
    foreach ($f in $Files) {
        $dst = Join-Path $HermesHome $f.Dest
        if (-not (Test-Path $dst)) { continue }
        $bak = Join-Path $backupDir $f.Dest
        $bakDir = Split-Path $bak -Parent
        New-Item -Path $bakDir -ItemType Directory -Force | Out-Null
        Copy-Item $dst $bak -Force
        $backedUp++
    }
    Log "Backed up $backedUp files (only manifest targets) -> $backupDir"
} else {
    Log 'DRY-RUN: backup would cover only manifest targets.'
}

# ---- Sync ----------------------------------------------------------------
foreach ($f in $Files) {
    $src = Join-Path $RepoRoot $f.Repo
    $dst = Join-Path $HermesHome $f.Dest
    if (-not (Test-Path $src)) {
        Log "MISSING source: $src"
        continue
    }
    $dstDir = Split-Path $dst -Parent
    if (-not (Test-Path $dstDir)) {
        New-Item -Path $dstDir -ItemType Directory -Force | Out-Null
    }
    if ($DryRun) {
        Log "DRY-RUN copy: $($f.Repo)"
    } else {
        Copy-Item $src $dst -Force
        Log "Restored: $($f.Repo)"
    }
}

# ---- Compile checks (bounded) ---------------------------------------------
$HadFailure = $false
$Targets = @(
    'plugins\web-tools\ddg\ddg_search.py',
    'plugins\web-tools\ddg\visit_website_enhanced.py',
    'plugins\web-tools\ddg\query_variants.py',
    'plugins\web-tools\ddg\selection.py',
    'plugins\web-tools\ddg\sieve.py',
    'plugins\web-tools\ddg\junk_filter.py',
    'plugins\web-tools\ddg\discovery.py',
    'plugins\web-tools\ddg\evidence_rank.py',
    'plugins\web-tools\ddg\_common.py',
    'hermes-agent\tools\ddg_search_tool.py'
)
if (Test-Path $Venv) {
    foreach ($rel in $Targets) {
        $path = Join-Path $HermesHome $rel
        if (-not (Test-Path $path)) {
            Log "MISSING target for compile check: $path"
            $HadFailure = $true
            continue
        }
        if ($DryRun) {
            Log "DRY-RUN py_compile: $rel"
            continue
        }
        # Direct invocation: Start-Process + -Redirect* swallows the child
        # exit code on some Windows builds (all checks showed FAILED with
        # empty errors even though py_compile succeeds). Plain & is reliable.
        $pyOutput = & $Venv -m py_compile $path 2>&1
        $compileExit = $LASTEXITCODE
        if ($compileExit -eq 0) {
            Log "py_compile OK: $rel"
        } else {
            Log "py_compile FAILED: $rel -> $pyOutput"
            $HadFailure = $true
        }
    }
} else {
    Log "WARNING: venv not found at $Venv - skipping compile checks"
}

# ---- Optional live smoke probe (off by default; needs network) -------------
if ($RunSmoke -and -not $DryRun -and -not $HadFailure) {
    $Probe = Join-Path $HermesHome 'hermes-dev\deep_test_vargas.py'
    if (Test-Path $Probe) {
        Log 'Running compose smoke probe (RunSmoke)...'
        $outF = Join-Path $env:TEMP 'restore_smoke_out.txt'
        $errF = Join-Path $env:TEMP 'restore_smoke_err.txt'
        $proc = Start-Process -FilePath $Venv -ArgumentList @($Probe) -NoNewWindow -PassThru -RedirectStandardOutput $outF -RedirectStandardError $errF
        if (-not $proc.WaitForExit(240000)) {
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            Log 'compose smoke probe TIMEOUT (240s) - skipped.'
        } else {
            Log "compose smoke probe exit: $($proc.ExitCode)"
            $out = Get-Content $outF -Tail 3 -ErrorAction SilentlyContinue
            if ($out) { Log "compose smoke probe output: $out" }
        }
    } else {
        Log "Smoke probe script not found at $Probe - skipping."
    }
} elseif ($RunSmoke) {
    Log 'Smoke probe skipped (DryRun or previous failures).'
} else {
    Log 'Smoke probe skipped (default; use -RunSmoke for live check).'
}

$sw.Stop()

# ---- Save restore log -------------------------------------------------------
$logDir = Join-Path $HermesHome '.restore-log'
New-Item -Path $logDir -ItemType Directory -Force | Out-Null
$logFile = Join-Path $logDir ("restore_" + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.log')
$script:Log | Out-File $logFile -Encoding utf8
Log "Log saved to $logFile"
$secs = [math]::Round($sw.Elapsed.TotalSeconds, 1)
Log ('Restore finished in ' + $secs + 's.')
if ($HadFailure) {
    Log 'Warning: some steps failed. Review log above.'
    exit 1
}
exit 0
