#Requires -Version 5.1
<#
.SYNOPSIS
    Stops the Funes's Hoard started from this folder.
.DESCRIPTION
    Uses the PID file the app writes into data\ (or data-demo\), after
    checking that the PID still belongs to a funes_hoard process. Falls back
    to whatever process listens on the port and answers /api/health as
    funes-hoard. On Windows the venv python.exe is a launcher around the
    real interpreter, so matching on the executable path alone misses it.
#>
param([int]$Port = 8813)
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Stopped = $false

function Stop-IfFunes([int]$ProcessId) {
    $p = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if ($p -and $p.CommandLine -and $p.CommandLine.Contains("funes_hoard") -and -not $p.CommandLine.Contains("mcp_server")) {
        Write-Host "Stopping Funes's Hoard (PID $ProcessId) ..."
        Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
        return $true
    }
    return $false
}

foreach ($Dir in @("data", "data-demo")) {
    $PidFile = Join-Path $RepoRoot "$Dir\funes.pid"
    if (-not (Test-Path -LiteralPath $PidFile)) { continue }
    try {
        $Info = Get-Content -LiteralPath $PidFile -Raw | ConvertFrom-Json
        if (Stop-IfFunes ([int]$Info.pid)) { $Stopped = $true }
    } catch {
        Write-Host "Ignoring unreadable $PidFile"
    }
    Remove-Item -LiteralPath $PidFile -ErrorAction SilentlyContinue
}

if (-not $Stopped) {
    try {
        $Health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2
    } catch {
        $Health = $null
    }
    if ($Health -and $Health.service -eq "funes-hoard") {
        $Conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($Conn -and (Stop-IfFunes ([int]$Conn.OwningProcess))) { $Stopped = $true }
    }
}

if ($Stopped) {
    Write-Host "Funes's Hoard stopped."
} else {
    Write-Host "Funes's Hoard is not running."
}
