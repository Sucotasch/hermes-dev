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

$RepoRoot = 'D:\Arx\Software Downloads\Hermes copy\hermes-dev'
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
    $backupDir = New-TimestampDir -Base (Join-Path $HermesHome '.restore-backups')
    Log "Backing up $HermesHome -> $backupDir"
    if (-not $DryRun) {
        Copy-Item $HermesHome $backupDir -Recurse -Force
    }
} else {
    Log 'Skipping backup (SkipBackup).'
}

# Copy files from repo to Hermes paths
$Files = @(
    @{ Repo = 'hermes-agent\tools\ddg_search_tool.py'; Dest = 'hermes-agent\tools\ddg_search_tool.py' },
    @{ Repo = 'hermes-agent\tools\browser_dialog_tool.py'; Dest = 'hermes-agent\tools\browser_dialog_tool.py' },
    @{ Repo = 'plugins\web-tools\ddg\ddg_search.py'; Dest = 'plugins\web-tools\ddg\ddg_search.py' },
    @{ Repo = 'plugins\web-tools\ddg\visit_website_enhanced.py'; Dest = 'plugins\web-tools\ddg\visit_website_enhanced.py' }
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
            $pyOutput = & $Py -m py_compile $path 2>&1
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
