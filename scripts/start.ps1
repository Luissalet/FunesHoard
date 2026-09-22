#Requires -Version 5.1
<#
.SYNOPSIS
    Starts Funes's Hoard: creates the venv on first run, installs the
    pinned dependencies, builds the frontend if needed, and launches the
    app with the repo root as the working directory (required so
    faustus-plugin.json is found by its process working directory).
#>
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$Venv = Join-Path $RepoRoot ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"

if (-not (Test-Path $Python)) {
    Write-Host "Creating virtual environment..."
    python -m venv $Venv
}

Write-Host "Installing pinned dependencies..."
& $Python -m pip install --quiet --upgrade pip
& $Python -m pip install --quiet -r (Join-Path $RepoRoot "requirements-lock.txt")

$FrontendDist = Join-Path $RepoRoot "frontend\dist"
if (-not (Test-Path $FrontendDist)) {
    Write-Host "Building frontend (first run)..."
    Push-Location (Join-Path $RepoRoot "frontend")
    npm ci
    npm run build
    Pop-Location
}

Write-Host "Starting Funes's Hoard on http://127.0.0.1:8813 ..."
& $Python -m funes_hoard --port 8813
