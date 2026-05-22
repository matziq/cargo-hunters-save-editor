<#
.SYNOPSIS
Exports Cargo Hunters item/icon PNGs from Unity AssetBundles.

.EXAMPLE
./extract_item_icons.ps1 -InstallDeps

.EXAMPLE
./extract_item_icons.ps1 -DryRun

.EXAMPLE
./extract_item_icons.ps1 -AllBundles -OutputDir .\exported_icons_all
#>
[CmdletBinding()]
param(
    [string]$GameDir = 'D:\Games\Cargo.Hunters.v0.26.26.43',
    [string]$OutputDir = '.\exported_icons',
    [switch]$InstallDeps,
    [switch]$DryRun,
    [switch]$AllBundles,
    [int]$Limit = 0,
    [int]$MinSize = 8,
    [switch]$Overwrite,
    [switch]$IncludeTechnicalMaps,
    [string[]]$Pattern
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$pythonCandidates = @()
$pythonCandidates += (Join-Path $ScriptDir '.venv\Scripts\python.exe')
$pythonCandidates += 'D:\Python\python.exe'
$pythonCandidates += 'py'
$pythonCandidates += 'python'

$python = $null
foreach ($candidate in $pythonCandidates) {
    try {
        if ($candidate -like '*\*' -and -not (Test-Path $candidate)) {
            continue
        }
        & $candidate --version *> $null
        if ($LASTEXITCODE -eq 0) {
            $python = $candidate
            break
        }
    }
    catch {
        continue
    }
}

if (-not $python) {
    throw 'Could not find Python. Install Python or create .venv\Scripts\python.exe first.'
}

$argsList = @()
$argsList += (Join-Path $ScriptDir 'extract_item_icons.py')
$argsList += '--game-dir'
$argsList += $GameDir
$argsList += '--out'
$argsList += $OutputDir
$argsList += '--min-size'
$argsList += [string]$MinSize

if ($InstallDeps) { $argsList += '--install-deps' }
if ($DryRun) { $argsList += '--dry-run' }
if ($AllBundles) { $argsList += '--all-bundles' }
if ($Overwrite) { $argsList += '--overwrite' }
if ($IncludeTechnicalMaps) { $argsList += '--include-technical-maps' }
if ($Limit -gt 0) { $argsList += @('--limit', [string]$Limit) }
if ($Pattern) {
    foreach ($p in $Pattern) {
        $argsList += @('--pattern', $p)
    }
}

Write-Host "Using Python: $python" -ForegroundColor Cyan
Write-Host "Output: $OutputDir" -ForegroundColor Cyan
& $python @argsList
exit $LASTEXITCODE
