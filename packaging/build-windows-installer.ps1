$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$BuildRoot = Join-Path $Root "packaging\build"
$Stage = Join-Path $BuildRoot "app"
$InstallerDir = Join-Path $Root "installer"
$AppVersion = "2.2.1"

function Copy-Tree($Source, $Destination, [string[]]$ExcludeDirs = @(), [string[]]$ExcludeFiles = @()) {
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    $excludeDirArgs = @()
    if ($ExcludeDirs.Count -gt 0) {
        $excludeDirArgs = @("/XD") + $ExcludeDirs
    }
    $excludeFileArgs = @()
    if ($ExcludeFiles.Count -gt 0) {
        $excludeFileArgs = @("/XF") + $ExcludeFiles
    }
    robocopy $Source $Destination /E /NFL /NDL /NJH /NJS /NP @excludeDirArgs @excludeFileArgs | Out-Null
    if ($LASTEXITCODE -gt 7) {
        throw "Failed to copy directory: $Source -> $Destination"
    }
}

function Get-InnoCompiler {
    $candidates = @(
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe",
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }
    $cmd = Get-Command iscc.exe -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }
    throw "Inno Setup compiler ISCC.exe was not found. Please install Inno Setup 6 first."
}

function Remove-RuntimeGeneratedContent($AppStage) {
    $backend = Join-Path $AppStage "backend"
    Remove-Item -Recurse -Force `
        (Join-Path $backend "data"), `
        (Join-Path $backend "uploads"), `
        (Join-Path $backend "logs"), `
        (Join-Path $backend "results") `
        -ErrorAction SilentlyContinue

    Get-ChildItem $AppStage -Directory -Filter "__pycache__" -Recurse -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Get-ChildItem $AppStage -File -Include "*.pyc", "*.pyo" -Recurse -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

function Get-BasePythonDir($VenvPython) {
    if (-not (Test-Path $VenvPython)) {
        throw "Backend virtual environment Python was not found: $VenvPython"
    }
    $basePrefix = & $VenvPython -c "import sys; print(sys.base_prefix)"
    if ($LASTEXITCODE -ne 0 -or -not $basePrefix) {
        throw "Failed to resolve base Python runtime from $VenvPython"
    }
    $pythonExe = Join-Path $basePrefix "python.exe"
    if (-not (Test-Path $pythonExe)) {
        throw "Resolved base Python runtime is invalid: $basePrefix"
    }
    return $basePrefix
}

function Set-UInt16LE([byte[]]$Bytes, [int]$Offset, [int]$Value) {
    $Bytes[$Offset] = [byte]($Value -band 0xff)
    $Bytes[($Offset + 1)] = [byte](($Value -shr 8) -band 0xff)
}

function Set-UInt32LE([byte[]]$Bytes, [int]$Offset, [int]$Value) {
    $Bytes[$Offset] = [byte]($Value -band 0xff)
    $Bytes[($Offset + 1)] = [byte](($Value -shr 8) -band 0xff)
    $Bytes[($Offset + 2)] = [byte](($Value -shr 16) -band 0xff)
    $Bytes[($Offset + 3)] = [byte](($Value -shr 24) -band 0xff)
}

function Get-PngDimensions([byte[]]$PngBytes) {
    if ($PngBytes.Length -lt 24) {
        throw "Invalid PNG data: too short"
    }
    $width = ([int]$PngBytes[16] -shl 24) -bor ([int]$PngBytes[17] -shl 16) -bor ([int]$PngBytes[18] -shl 8) -bor [int]$PngBytes[19]
    $height = ([int]$PngBytes[20] -shl 24) -bor ([int]$PngBytes[21] -shl 16) -bor ([int]$PngBytes[22] -shl 8) -bor [int]$PngBytes[23]
    return @{ Width = $width; Height = $height }
}

function New-IcoFromPngBytes([byte[]]$PngBytes, [string]$IcoPath) {
    $dimensions = Get-PngDimensions $PngBytes
    $widthByte = if ($dimensions.Width -ge 256) { 0 } else { $dimensions.Width }
    $heightByte = if ($dimensions.Height -ge 256) { 0 } else { $dimensions.Height }

    $header = New-Object byte[] 22
    Set-UInt16LE $header 0 0
    Set-UInt16LE $header 2 1
    Set-UInt16LE $header 4 1
    $header[6] = [byte]$widthByte
    $header[7] = [byte]$heightByte
    $header[8] = [byte]0
    $header[9] = [byte]0
    Set-UInt16LE $header 10 1
    Set-UInt16LE $header 12 32
    Set-UInt32LE $header 14 $PngBytes.Length
    Set-UInt32LE $header 18 $header.Length

    $icoBytes = New-Object byte[] ($header.Length + $PngBytes.Length)
    [System.Buffer]::BlockCopy($header, 0, $icoBytes, 0, $header.Length)
    [System.Buffer]::BlockCopy($PngBytes, 0, $icoBytes, $header.Length, $PngBytes.Length)
    [System.IO.File]::WriteAllBytes($IcoPath, $icoBytes)
}

function New-ZPaperIcon([string]$SvgPath, [string]$PngFallbackPath, [string]$IcoPath) {
    if (Test-Path $SvgPath) {
        $svg = Get-Content -LiteralPath $SvgPath -Raw
        $match = [regex]::Match($svg, 'data:image/png;base64,([^"]+)')
        if ($match.Success) {
            New-IcoFromPngBytes ([System.Convert]::FromBase64String($match.Groups[1].Value)) $IcoPath
            return
        }
    }

    if (Test-Path $PngFallbackPath) {
        New-IcoFromPngBytes ([System.IO.File]::ReadAllBytes($PngFallbackPath)) $IcoPath
        return
    }

    throw "App icon source was not found. Expected $SvgPath or $PngFallbackPath"
}

Remove-Item -Recurse -Force $BuildRoot -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $Stage, $InstallerDir | Out-Null

Write-Host "[1/6] Building frontend..."
Push-Location (Join-Path $Root "frontend")
try {
    npm.cmd run build
} finally {
    Pop-Location
}

Write-Host "[2/6] Preparing clean app staging folder..."
Copy-Item (Join-Path $Root "README.md") (Join-Path $Stage "README.md")
Copy-Item (Join-Path $Root "README_EN.md") (Join-Path $Stage "README_EN.md")
Copy-Tree (Join-Path $Root "packaging\runtime") $Stage

$BackendStage = Join-Path $Stage "backend"
New-Item -ItemType Directory -Force -Path $BackendStage | Out-Null
Copy-Tree (Join-Path $Root "backend\app") (Join-Path $BackendStage "app") `
    -ExcludeDirs @("__pycache__") `
    -ExcludeFiles @("*.pyc", "*.pyo")
Copy-Tree (Join-Path $Root "backend\alembic") (Join-Path $BackendStage "alembic") `
    -ExcludeDirs @("__pycache__") `
    -ExcludeFiles @("*.pyc", "*.pyo")
Copy-Item (Join-Path $Root "backend\alembic.ini") (Join-Path $BackendStage "alembic.ini")
Copy-Item (Join-Path $Root "backend\requirements.txt") (Join-Path $BackendStage "requirements.txt")

$FrontendStage = Join-Path $Stage "frontend"
New-Item -ItemType Directory -Force -Path $FrontendStage | Out-Null
Copy-Tree (Join-Path $Root "frontend\dist") (Join-Path $FrontendStage "dist")

Write-Host "[3/6] Preparing Windows app icon..."
New-ZPaperIcon `
    (Join-Path $Root "frontend\public\favicon.svg") `
    (Join-Path $Root "frontend\public\favicon.png") `
    (Join-Path $Stage "z-paper.ico")

Write-Host "[4/6] Copying bundled Python runtime..."
$VenvPython = Join-Path $Root "backend\venv\Scripts\python.exe"
$BasePythonDir = Get-BasePythonDir $VenvPython
$PortablePythonDir = Join-Path $BackendStage "python"
$SitePackages = Join-Path $Root "backend\venv\Lib\site-packages"
$BasePythonExcludeDirs = @(
    "__pycache__",
    (Join-Path $BasePythonDir "Lib\site-packages"),
    (Join-Path $BasePythonDir "Scripts"),
    (Join-Path $BasePythonDir "include"),
    (Join-Path $BasePythonDir "libs"),
    (Join-Path $BasePythonDir "tcl"),
    (Join-Path $BasePythonDir "Lib\ensurepip"),
    (Join-Path $BasePythonDir "Lib\idlelib"),
    (Join-Path $BasePythonDir "Lib\lib2to3"),
    (Join-Path $BasePythonDir "Lib\test"),
    (Join-Path $BasePythonDir "Lib\tkinter"),
    (Join-Path $BasePythonDir "Lib\turtledemo"),
    (Join-Path $BasePythonDir "Lib\venv")
)
Copy-Tree $BasePythonDir $PortablePythonDir `
    -ExcludeDirs $BasePythonExcludeDirs `
    -ExcludeFiles @("*.pyc", "*.pyo", "_test*.pyd", "_tkinter.pyd", "tcl86t.dll", "tk86t.dll")
Copy-Tree $SitePackages (Join-Path $PortablePythonDir "Lib\site-packages") `
    -ExcludeDirs @("__pycache__") `
    -ExcludeFiles @("*.pyc", "*.pyo")

Remove-Item -Recurse -Force `
    (Join-Path $BackendStage "data"), `
    (Join-Path $BackendStage "uploads"), `
    (Join-Path $BackendStage "logs"), `
    (Join-Path $BackendStage "results") `
    -ErrorAction SilentlyContinue

Write-Host "[5/6] Verifying staged runtime..."
Push-Location $BackendStage
try {
    & (Join-Path $BackendStage "python\python.exe") -c "import fastapi, uvicorn, fitz; from app.main import app; print('stage runtime ok')"
} finally {
    Pop-Location
}
Remove-RuntimeGeneratedContent $Stage

Write-Host "[6/6] Compiling Inno Setup installer..."
$Iscc = Get-InnoCompiler
$Iss = Join-Path $Root "packaging\z-paper.iss"
& $Iscc $Iss "/DSourceDir=$Stage" "/DOutputDir=$InstallerDir" "/DAppVersion=$AppVersion"

$Installer = Join-Path $InstallerDir "z-paper-$AppVersion-setup.exe"
if (-not (Test-Path $Installer)) {
    throw "Installer was not generated: $Installer"
}

Write-Host "DONE: $Installer"
