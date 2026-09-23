#Requires -Version 5.1
<#
.SYNOPSIS
    Starts Funes's Hoard in the background and opens it in the browser.
.DESCRIPTION
    First run: creates .venv (Python 3.11+), installs requirements-lock.txt
    and builds the frontend if frontend\dist is missing. Later runs only
    reinstall when the lock file changed. The app is started with the repo
    root as working directory (Faustus reads faustus-plugin.json from the
    process working directory) and the script waits for /api/health.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Demo
#>
param(
    [int]$Port = 8813,
    [switch]$Demo,
    [switch]$NoBrowser
)
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot
$Url = "http://127.0.0.1:$Port"

function Test-Funes {
    try {
        $h = Invoke-RestMethod -Uri "$Url/api/health" -TimeoutSec 2
        return ($h.service -eq "funes-hoard")
    } catch {
        return $false
    }
}

function Invoke-Checked([string]$What, [scriptblock]$Block) {
    & $Block
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)." }
}

if (Test-Funes) {
    Write-Host "Funes's Hoard is already running at $Url"
    if (-not $NoBrowser) { Start-Process $Url }
    exit 0
}

# --- Python virtual environment ------------------------------------------
$Venv = Join-Path $RepoRoot ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $BaseExe = $null
    $BaseArgs = @()
    if (Test-Path -LiteralPath "C:\Python313\python.exe") {
        $BaseExe = "C:\Python313\python.exe"
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        $BaseExe = "py"; $BaseArgs = @("-3")
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $BaseExe = "python"
    } else {
        throw "Python 3.11 or newer was not found. Install it from python.org and run this again."
    }
    Write-Host "Creating the virtual environment with $BaseExe ..."
    Invoke-Checked "Creating the virtual environment" { & $BaseExe @BaseArgs -m venv $Venv }
}

$Lock = Join-Path $RepoRoot "requirements-lock.txt"
$Stamp = Join-Path $Venv ".lock-sha256"
# SHA-256 through .NET: Get-FileHash is missing when Windows PowerShell is started from PowerShell 7.
$LockHash = [System.BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash([System.IO.File]::ReadAllBytes($Lock))).Replace('-', '')
$Installed = if (Test-Path -LiteralPath $Stamp) { (Get-Content -LiteralPath $Stamp -Raw).Trim() } else { "" }
if ($Installed -ne $LockHash) {
    Write-Host "Installing pinned dependencies ..."
    Invoke-Checked "Upgrading pip" { & $Python -m pip install --quiet --disable-pip-version-check --upgrade pip }
    Invoke-Checked "Installing requirements-lock.txt" { & $Python -m pip install --quiet --disable-pip-version-check -r $Lock }
    Set-Content -LiteralPath $Stamp -Value $LockHash -Encoding ASCII
}

# --- Frontend -------------------------------------------------------------
$Index = Join-Path $RepoRoot "frontend\dist\index.html"
if (-not (Test-Path -LiteralPath $Index)) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw "The interface is not built and Node.js (npm) was not found. Install Node 22 and run this again."
    }
    Write-Host "Building the interface (first run) ..."
    Push-Location -LiteralPath (Join-Path $RepoRoot "frontend")
    try {
        Invoke-Checked "npm ci" { npm ci --no-audit --no-fund }
        Invoke-Checked "npm run build" { npm run build }
    } finally {
        Pop-Location
    }
}

# --- Start and wait for /api/health ---------------------------------------
$AppArgs = @("-m", "funes_hoard", "--no-browser", "--port", "$Port")
if ($Demo) { $AppArgs += "--demo" }
Write-Host "Starting Funes's Hoard on $Url ..."
$Proc = Start-Process -FilePath $Python -ArgumentList $AppArgs -WorkingDirectory $RepoRoot -WindowStyle Hidden -PassThru

$Deadline = (Get-Date).AddSeconds(45)
while ((Get-Date) -lt $Deadline) {
    if (Test-Funes) {
        Write-Host "Ready: $Url"
        if (-not $NoBrowser) { Start-Process $Url }
        exit 0
    }
    if ($Proc.HasExited) { break }
    Start-Sleep -Milliseconds 500
}

$DataDir = if ($Demo) { "data-demo" } else { "data" }
$Log = Join-Path $RepoRoot "$DataDir\logs\app.log"
Write-Host "Funes's Hoard did not become healthy on $Url." -ForegroundColor Red
if (Test-Path -LiteralPath $Log) {
    Write-Host "Last lines of $Log :"
    Get-Content -LiteralPath $Log -Tail 15
}
exit 1
