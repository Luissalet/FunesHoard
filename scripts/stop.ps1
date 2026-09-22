#Requires -Version 5.1
<#
.SYNOPSIS
    Stops any Funes's Hoard process started from this repo's venv.
#>
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"

$procs = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine.Contains("funes_hoard") -and $_.ExecutablePath -eq $Python }

if (-not $procs) {
    Write-Host "Funes's Hoard is not running."
    exit 0
}

foreach ($p in $procs) {
    Write-Host "Stopping PID $($p.ProcessId)..."
    Stop-Process -Id $p.ProcessId -Force
}
