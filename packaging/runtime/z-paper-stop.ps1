$ErrorActionPreference = "SilentlyContinue"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Logs = Join-Path $Root "backend\logs"
$PidFile = Join-Path $Logs "z-paper.pid"
$TrayPidFile = Join-Path $Logs "z-paper-tray.pid"
$ExitSignalFile = Join-Path $Logs "z-paper.exit"

function Get-PidProcess([string]$Path) {
    if (-not (Test-Path $Path)) {
        return $null
    }
    $rawPid = Get-Content $Path | Select-Object -First 1
    $pidValue = 0
    if (-not [int]::TryParse($rawPid, [ref]$pidValue)) {
        return $null
    }
    return Get-Process -Id $pidValue
}

$tray = Get-PidProcess $TrayPidFile
if ($tray) {
    New-Item -ItemType Directory -Force -Path $Logs | Out-Null
    Set-Content -Encoding ASCII -Path $ExitSignalFile -Value "exit"
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 500
        if (-not (Get-Process -Id $tray.Id)) {
            break
        }
    }
    if (Get-Process -Id $tray.Id) {
        Stop-Process -Id $tray.Id -Force
    }
}

if (Test-Path $PidFile) {
    $serverPid = (Get-Content $PidFile | Select-Object -First 1)
    if ($serverPid) {
        Stop-Process -Id ([int]$serverPid) -Force
    }
    Remove-Item $PidFile -Force
}

Remove-Item $TrayPidFile -Force
Remove-Item $ExitSignalFile -Force
