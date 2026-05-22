# Build the Cargo Hunters Save Editor standalone .exe.
# Produces dist\CargoHuntersSaveEditor.exe — a single file, no Python install
# required on the target machine.
#
# Usage:
#   pwsh -NoProfile -ExecutionPolicy Bypass -File .\build_exe.ps1
#
[CmdletBinding()]
param(
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

function Get-PythonCmd {
    foreach ($candidate in @(
            (Join-Path $PSScriptRoot '.venv\Scripts\python.exe'),
            'D:\Python312\python.exe',
            'D:\Python310\python.exe'
        )) {
        if (Test-Path $candidate) { return @{ Exe = $candidate; Args = @() } }
    }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { return @{ Exe = $py.Source; Args = @('-3') } }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) { return @{ Exe = $python.Source; Args = @() } }
    throw 'Python 3.10+ not found. Install Python or create .venv.'
}

$pyCmd = Get-PythonCmd
$pythonExe = $pyCmd.Exe
$pythonArgs = $pyCmd.Args
Write-Host ("Using Python: {0} {1}" -f $pythonExe, ($pythonArgs -join ' '))

# Make sure PyInstaller is available.
& $pythonExe @pythonArgs -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Installing PyInstaller...'
    & $pythonExe @pythonArgs -m pip install --upgrade pyinstaller
    if ($LASTEXITCODE -ne 0) { throw 'pip install pyinstaller failed.' }
}

if ($Clean) {
    Write-Host 'Cleaning previous build outputs...'
    Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
}

Write-Host 'Building CargoHuntersSaveEditor.exe...'
& $pythonExe @pythonArgs -m PyInstaller --noconfirm CargoHuntersSaveEditor.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }

$exePath = Join-Path $PSScriptRoot 'dist\CargoHuntersSaveEditor.exe'
if (Test-Path $exePath) {
    $size = [math]::Round((Get-Item $exePath).Length / 1MB, 1)
    Write-Host ''
    Write-Host "Build succeeded: $exePath  ($size MB)" -ForegroundColor Green
    Write-Host 'Distribute that single .exe to users. No Python install required.'
}
else {
    throw "Build finished but $exePath was not produced."
}
