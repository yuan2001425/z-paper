$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Backend = Join-Path $Root "backend"
$PortablePython = Join-Path $Backend "python\python.exe"
$VenvPython = Join-Path $Backend "venv\Scripts\python.exe"
$Python = $PortablePython
if (-not (Test-Path $Python) -and (Test-Path $VenvPython)) {
    $Python = $VenvPython
}

$Logs = Join-Path $Backend "logs"
$PidFile = Join-Path $Logs "z-paper.pid"
$TrayPidFile = Join-Path $Logs "z-paper-tray.pid"
$ExitSignalFile = Join-Path $Logs "z-paper.exit"
$IconPath = Join-Path $Root "z-paper.ico"
$Url = "http://127.0.0.1:8000"

function Join-Chars([int[]]$Codes) {
    return -join ($Codes | ForEach-Object { [char]$_ })
}

$LabelOpen = (Join-Chars @(0x6253, 0x5F00)) + " z-paper"
$LabelRestart = Join-Chars @(0x91CD, 0x542F, 0x670D, 0x52A1)
$LabelExit = (Join-Chars @(0x9000, 0x51FA)) + " z-paper"
$LabelStarting = "z-paper " + (Join-Chars @(0x542F, 0x52A8, 0x4E2D))
$LabelRunning = "z-paper " + (Join-Chars @(0x8FD0, 0x884C, 0x4E2D))
$LabelStopped = "z-paper " + (Join-Chars @(0x5DF2, 0x505C, 0x6B62))

function Ensure-ZPaperDirectories {
    New-Item -ItemType Directory -Force -Path $Logs | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $Backend "data") | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $Backend "uploads") | Out-Null
}

function Show-ZPaperMessage([string]$Message) {
    try {
        [System.Windows.Forms.MessageBox]::Show($Message, "z-paper") | Out-Null
    } catch {
        Write-Host $Message
    }
}

function Test-ZPaperServer {
    try {
        $response = Invoke-WebRequest -Uri "$Url/health" -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Get-PidProcess([string]$Path) {
    if (-not (Test-Path $Path)) {
        return $null
    }
    $rawPid = Get-Content $Path -ErrorAction SilentlyContinue | Select-Object -First 1
    $pidValue = 0
    if (-not [int]::TryParse($rawPid, [ref]$pidValue)) {
        return $null
    }
    return Get-Process -Id $pidValue -ErrorAction SilentlyContinue
}

function Open-ZPaper {
    Start-Process $Url
}

function Stop-ZPaperServer {
    $server = Get-PidProcess $PidFile
    if ($server) {
        Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

function Start-ZPaperServer {
    Ensure-ZPaperDirectories

    if (-not (Test-Path $Python)) {
        Show-ZPaperMessage "Bundled Python runtime was not found. Please reinstall z-paper."
        return $false
    }

    if (Test-ZPaperServer) {
        return $true
    }

    $server = Get-PidProcess $PidFile
    if (-not $server) {
        $env:PYTHONUTF8 = "1"
        $env:PYTHONUNBUFFERED = "1"
        $stdout = Join-Path $Logs "server.out.log"
        $stderr = Join-Path $Logs "server.err.log"
        $arguments = @("-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000")

        $server = Start-Process `
            -FilePath $Python `
            -ArgumentList $arguments `
            -WorkingDirectory $Backend `
            -WindowStyle Hidden `
            -RedirectStandardOutput $stdout `
            -RedirectStandardError $stderr `
            -PassThru

        Set-Content -Encoding ASCII -Path $PidFile -Value $server.Id
    }

    for ($i = 0; $i -lt 45; $i++) {
        Start-Sleep -Seconds 1
        if (Test-ZPaperServer) {
            return $true
        }
        if ($server.HasExited) {
            break
        }
    }

    Show-ZPaperMessage "z-paper failed to start. Please check backend\logs\server.err.log in the install folder."
    return $false
}

function Restart-ZPaperServer {
    Stop-ZPaperServer
    if (Start-ZPaperServer) {
        Open-ZPaper
    }
}

function Exit-ZPaper {
    Stop-ZPaperServer
    Remove-Item $TrayPidFile -Force -ErrorAction SilentlyContinue
    Remove-Item $ExitSignalFile -Force -ErrorAction SilentlyContinue
    if ($script:NotifyIcon) {
        $script:NotifyIcon.Visible = $false
        $script:NotifyIcon.Dispose()
    }
    [System.Windows.Forms.Application]::Exit()
}

Ensure-ZPaperDirectories

$existingTray = Get-PidProcess $TrayPidFile
if ($existingTray -and $existingTray.Id -ne $PID) {
    Open-ZPaper
    exit 0
}
Set-Content -Encoding ASCII -Path $TrayPidFile -Value $PID
Remove-Item $ExitSignalFile -Force -ErrorAction SilentlyContinue

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

[System.Windows.Forms.Application]::EnableVisualStyles()

$script:NotifyIcon = New-Object System.Windows.Forms.NotifyIcon
if (Test-Path $IconPath) {
    $script:NotifyIcon.Icon = New-Object System.Drawing.Icon($IconPath)
} else {
    $script:NotifyIcon.Icon = [System.Drawing.SystemIcons]::Application
}
$script:NotifyIcon.Text = $LabelStarting
$script:NotifyIcon.Visible = $true

$menu = New-Object System.Windows.Forms.ContextMenuStrip
$openItem = $menu.Items.Add($LabelOpen)
$restartItem = $menu.Items.Add($LabelRestart)
$separator = New-Object System.Windows.Forms.ToolStripSeparator
[void]$menu.Items.Add($separator)
$exitItem = $menu.Items.Add($LabelExit)

$openItem.add_Click({ Open-ZPaper })
$restartItem.add_Click({ Restart-ZPaperServer })
$exitItem.add_Click({ Exit-ZPaper })

$script:NotifyIcon.ContextMenuStrip = $menu
$script:NotifyIcon.add_MouseClick({
    param($sender, $eventArgs)
    if ($eventArgs.Button -eq [System.Windows.Forms.MouseButtons]::Left) {
        Open-ZPaper
    }
})

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 2000
$timer.add_Tick({
    if (Test-Path $ExitSignalFile) {
        Exit-ZPaper
        return
    }
    if (Test-ZPaperServer) {
        $script:NotifyIcon.Text = $LabelRunning
    } else {
        $server = Get-PidProcess $PidFile
        if ($server) {
            $script:NotifyIcon.Text = $LabelStarting
        } else {
            $script:NotifyIcon.Text = $LabelStopped
        }
    }
})
$timer.Start()

if (Start-ZPaperServer) {
    $script:NotifyIcon.Text = $LabelRunning
    Open-ZPaper
} else {
    $script:NotifyIcon.Text = $LabelStopped
}

[System.Windows.Forms.Application]::Run()
