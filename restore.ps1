# Restore custom Hermes tools from external dev repo
param(
    [switch]$DryRun,
    [switch]$NoStopHermes,
    [switch]$SkipBackup
)

$ErrorActionPreference = 'SilentlyContinue'
$script:Log = @()
function Log($msg) {
    $ts = Get-Date -Format 'HH:mm:ss'
    $script:Log += "[$ts] $msg"
    Write-Host $msg
}

function Stop-HermesIfRunning {
    if ($NoStopHermes) {
        Log 'Skipping Hermes stop (NoStopHermes).'
        return
    }
    Log 'Attempting to stop Hermes process...'
    $procs = Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'hermes|python|node' }
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

function New-TimestampDir {
    param([string]$Base)
    $ts = Get-Date -Format 'yyyyMMdd_HHmmss'
    $dir = Join-Path $Base "backup-$ts"
    New-Item -Path $dir -ItemType Directory -Force | Out-Null
    return $dir
}

$RepoRoot = $PSScriptRoot
if (-not $RepoRoot) { $RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $RepoRoot) { $RepoRoot = Get-Location }
$HermesHome = Join-Path $env:USERPROFILE '.hermes'

if (-not (Test-Path $RepoRoot)) {
    Log "ERROR: Repo not found at $RepoRoot"
    exit 1
}
if (-not (Test-Path $HermesHome)) {
    Log "ERROR: Hermes home not found at $HermesHome"
    exit 1
}

$Venv = Join-Path $HermesHome 'hermes-agent\venv\Scripts\python.exe'

Log "Repo: $RepoRoot"
Log "Hermes: $HermesHome"
if ($DryRun) { Log 'Running in DryRun mode; no changes applied.' }

Stop-HermesIfRunning

# Backup current .hermes if requested
if (-not $SkipBackup) {
    $backupDirBase = Join-Path (Split-Path -Parent $HermesHome) 'Hermes_Backups'
    $backupDir = New-TimestampDir -Base $backupDirBase
    Log "Backing up $HermesHome -> $backupDir"
    if (-not $DryRun) {
        Copy-Item $HermesHome $backupDir -Recurse -Force
    }
} else {
    Log 'Skipping backup (SkipBackup).'
}

# Pre-backup changed skill files before overwrite (best-effort, non-fatal)
$SkillFiles = @(
    @{ Repo = 'skills\restore-context\SKILL.md'; Dest = 'skills\restore-context\SKILL.md' }
    @{ Repo = 'skills\web-deep-search\SKILL.md'; Dest = 'skills\web-deep-search\SKILL.md' }
)

foreach ($s in $SkillFiles) {
    $src = Join-Path $RepoRoot $s.Repo
    $dst = Join-Path $HermesHome $s.Dest
    if (-not (Test-Path $dst) -or -not (Test-Path $src)) {
        continue
    }
    try {
        $srcHash = (Get-FileHash $src -Algorithm SHA1).Hash
        $dstHash = (Get-FileHash $dst -Algorithm SHA1).Hash
        if ($srcHash -ne $dstHash) {
            $ts = Get-Date -Format 'yyyyMMdd_HHmmss'
            $bakName = [IO.Path]::GetFileNameWithoutExtension($s.Dest) + ".pre-restore-$ts" + [IO.Path]::GetExtension($s.Dest)
            $bakDir = Join-Path $HermesHome '.restore-log'
            New-Item -Path $bakDir -ItemType Directory -Force | Out-Null
            $bakPath = Join-Path $bakDir $bakName
            Copy-Item $dst $bakPath -Force
            Log "Skill pre-backup: $dst -> $bakPath"
        }
    } catch {
        Log "Skill pre-backup skipped for $($s.Dest): $_"
    }
}

# Copy files from repo to Hermes paths
$Files = @(
    @{ Repo = 'hermes-agent\tools\ddg_search_tool.py'; Dest = 'hermes-agent\tools\ddg_search_tool.py' }
    @{ Repo = 'hermes-agent\tools\browser_dialog_tool.py'; Dest = 'hermes-agent\tools\browser_dialog_tool.py' }
    @{ Repo = 'plugins\web-tools\ddg\ddg_search.py'; Dest = 'plugins\web-tools\ddg\ddg_search.py' }
    @{ Repo = 'plugins\web-tools\ddg\visit_website_enhanced.py'; Dest = 'plugins\web-tools\ddg\visit_website_enhanced.py' }
    @{ Repo = 'CONTEXT.md'; Dest = 'CONTEXT.md' }
    @{ Repo = 'skills\restore-context\SKILL.md'; Dest = 'skills\restore-context\SKILL.md' }
    @{ Repo = 'skills\web-deep-search\SKILL.md'; Dest = 'skills\web-deep-search\SKILL.md' }
)

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
        Log "DRY-RUN copy: $src -> $dst"
    } else {
        Copy-Item $src $dst -Force
        Log "Restored: $($f.Repo)"
    }
}

# Basic compile checks
$HadFailure = $false
$Targets = @(
    @('plugins\web-tools\ddg\ddg_search.py', $false),
    @('plugins\web-tools\ddg\visit_website_enhanced.py', $false),
    @('hermes-agent\tools\ddg_search_tool.py', $false)
)
foreach ($t in $Targets) {
    $rel = $t[0]
    $path = Join-Path $HermesHome $rel
    if (-not (Test-Path $path)) {
        Log "MISSING target for compile check: $path"
        $HadFailure = $true
        continue
    }
    if ($DryRun) {
        Log "DRY-RUN py_compile: $path"
    } else {
        try {
            $pyOutput = & $Venv -m py_compile $path 2>&1
            $compileExit = $LASTEXITCODE
            if ($compileExit -eq 0) {
                Log "py_compile OK: $path"
            } else {
                Log "py_compile FAILED: $path -> $pyOutput (exit $compileExit)"
                $HadFailure = $true
            }
        } catch {
            Log "py_compile EXCEPTION: $path -> $_"
            $HadFailure = $true
        }
    }
}

# Post-restore smoke checks: compose contract (best-effort)
if (-not $DryRun -and $HadFailure -eq $false) {
    $Probe = Join-Path $HermesHome 'hermes-dev\deep_test_vargas.py'
    if (-not (Test-Path $Probe)) {
        $ProbeContent = @"
import sys, json
sys.path.insert(0, r'$HermesHome')
sys.path.insert(0, r'$HermesHome\hermes-agent')
sys.path.insert(0, r'$HermesHome\hermes-agent\tools')
from tools.registry import discover_builtin_tools, registry
discover_builtin_tools()
entry = registry.get_entry('web_deep_research')
out = entry.handler({'query':'Alberto Vargas pinup artist','max_validate':100,'max_new_links':20,'max_chars':3000,'compose':True})
print(json.dumps({'output_type': type(out).__name__, 'compose_used': True}, ensure_ascii=False))
"@
        $ProbeDir = Split-Path $Probe -Parent
        if (-not (Test-Path $ProbeDir)) {
            New-Item -Path $ProbeDir -ItemType Directory -Force | Out-Null
        }
        Set-Content -Path $Probe -Value $ProbeContent -Encoding utf8
    }
    if (Test-Path $Probe) {
        Log 'Running compose smoke probe...'
        try {
            $smoke = & $Venv $Probe 2>&1
            $smokeExit = $LASTEXITCODE
            Log "compose smoke probe exit: $smokeExit"
            if ($smoke) {
                Log "compose smoke probe output: $smoke"
            }
        } catch {
            Log "compose smoke probe exception: $_"
        }
    }
}

# Save restore log
$logDir = Join-Path $HermesHome '.restore-log'
New-Item -Path $logDir -ItemType Directory -Force | Out-Null
$logFile = Join-Path $logDir ("restore_" + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.log')
$script:Log | Out-File $logFile -Encoding utf8
Log "Log saved to $logFile"

Log 'Restore finished.'
if ($HadFailure) {
    Log 'Warning: some steps failed. Review log: $logFile'
    exit 1
}
exit 0
