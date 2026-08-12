# Restore custom Hermes deep-search pipeline from the dev repo.
#
# One-button tool: run it (directly, or via the GUI "Check & Restore"
# button) after a Hermes update/reinstall, or whenever the pipeline looks
# broken. It:
#   1. syncs custom tools, web plugins, skills and CONTEXT.md into ~/.hermes
#   2. installs missing Python packages into the Hermes venv (ddgs, bs4,
#      trafilatura, htmldate, lxml) - venv is often recreated by updates
#   3. py_compile-checks every synced file
#   4. runs restore_check.py for the final verdict: are all 5 tools
#      registered and are all deps importable? Prints OK/BROKEN.
#
# No backups in v3: the repo is the source of truth (and it is in git).
# -SkipBackup is accepted for GUI compatibility and does nothing.
# Hermes is NOT stopped by default (use -StopHermes); -NoStopHermes is
# accepted for GUI compatibility and does nothing either.
#
# Usage:
#   .\restore.ps1              # full check + restore
#   .\restore.ps1 -DryRun      # show what would happen, change nothing
#   .\restore.ps1 -RunSmoke    # also run the live compose smoke probe
#   .\restore.ps1 -NoStopHermes
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
$HadFailure = $false

Log "Repo: $RepoRoot"
Log "Hermes: $HermesHome"
if ($DryRun) { Log 'Running in DryRun mode; no changes applied.' }
if ($NoBackup) { Log 'Note: backups were removed in v3 (git repo is the source of truth).' }

# ---- Manifest: exactly what we sync (repo wins) ------------------------------
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

# ---- 1) Sync ----------------------------------------------------------------
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
        Copy-Item $src $dst -Force
        Log "Synced: $($f.Repo)"
    }
}

# ---- 2) Dependencies (only install what is missing; offline-safe reruns) -----
if (Test-Path $Venv) {
    $Deps = @(
        @{ Pkg = 'ddgs';          Mod = 'ddgs' }
        @{ Pkg = 'beautifulsoup4'; Mod = 'bs4' }
        @{ Pkg = 'trafilatura';   Mod = 'trafilatura' }
        @{ Pkg = 'htmldate';      Mod = 'htmldate' }
        @{ Pkg = 'lxml';          Mod = 'lxml' }
    )
    foreach ($d in $Deps) {
        & $Venv -c "import $($d.Mod)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            Log "dep OK: $($d.Mod)"
            continue
        }
        if ($DryRun) {
            Log "DRY-RUN would install: $($d.Pkg)"
            continue
        }
        Log "dep missing: $($d.Mod) - installing $($d.Pkg) ..."
        $pipOut = & $Venv -m pip install --disable-pip-version-check --timeout 30 $d.Pkg 2>&1
        if ($LASTEXITCODE -eq 0) {
            Log "dep installed: $($d.Mod)"
        } else {
            Log "dep INSTALL FAILED: $($d.Pkg)"
            $pipOut | Select-Object -Last 6 | ForEach-Object { Log "    $_" }
            $HadFailure = $true
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
    'plugins\web-tools\ddg\_coverage.py',
    'plugins\web-tools\ddg\_common.py',
    'hermes-agent\tools\ddg_search_tool.py',
    'hermes-agent\tools\browser_dialog_tool.py'
)
if (Test-Path $Venv) {
    foreach ($rel in $Targets) {
        $path = Join-Path $HermesHome $rel
        if (-not (Test-Path $path)) {
            Log "MISSING target for compile check: $rel"
            $HadFailure = $true
            continue
        }
        if ($DryRun) {
            Log "DRY-RUN py_compile: $rel"
            continue
        }
        # Direct invocation: Start-Process + -Redirect* swallows the child
        # exit code on some Windows builds. Plain & is reliable.
        $pyOutput = & $Venv -m py_compile $path 2>&1
        $compileExit = $LASTEXITCODE
        if ($compileExit -eq 0) {
            Log "py_compile OK: $rel"
        } else {
            Log "py_compile FAILED: $rel -> $pyOutput"
            $HadFailure = $true
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
