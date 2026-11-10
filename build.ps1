[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$BuildDirectory = (Join-Path $PSScriptRoot ".build")
)

$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "Run this build script on Windows."
}

$source = Join-Path $PSScriptRoot "snow_effect.py"
$icon = Join-Path $PSScriptRoot "snowflake.ico"
$versionInfo = Join-Path $PSScriptRoot "version_info.txt"
$projectLicense = Join-Path $PSScriptRoot "LICENSE"
$dist = Join-Path $PSScriptRoot "dist"
$executableName = "QysqyDala"
foreach ($file in @($source, $icon, $versionInfo, $projectLicense)) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
        throw "Required project file missing: $file"
    }
}

& $Python -I -c "import sys; sys.exit(0 if (3, 10, 1) <= sys.version_info[:3] < (3, 16) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "Building requires CPython 3.10.1 through 3.15."
}

New-Item -Path $BuildDirectory -ItemType Directory -Force | Out-Null
$buildRoot = (Resolve-Path -LiteralPath $BuildDirectory).Path
$venv = Join-Path $buildRoot "venv"
$venvPython = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    & $Python -I -m venv $venv
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create the build environment."
    }
}

$licenseJson = & $venvPython -I -c "import json, sys; from pathlib import Path; print(json.dumps(str(Path(sys.base_prefix) / 'LICENSE.txt')))"
if ($LASTEXITCODE -ne 0) {
    throw "Could not locate the base Python installation."
}
$pythonLicense = ConvertFrom-Json -InputObject $licenseJson
if (-not (Test-Path -LiteralPath $pythonLicense -PathType Leaf)) {
    throw "Python's LICENSE.txt was not found. Use the standard CPython distribution from python.org."
}

& $venvPython -I -m pip --isolated --disable-pip-version-check --no-cache-dir install `
    --index-url https://pypi.org/simple --only-binary=:all: PyInstaller==6.22.3
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller installation failed."
}

$oldCache = $env:PYINSTALLER_CONFIG_DIR
$env:PYINSTALLER_CONFIG_DIR = Join-Path $buildRoot "cache"
try {
    & $venvPython -I -m PyInstaller `
        --onefile --windowed --noupx --name $executableName `
        --icon $icon --version-file $versionInfo --add-data "${icon}:." `
        --add-data "${projectLicense}:licenses/qysqy-dala" `
        --add-data "${pythonLicense}:licenses/python" `
        --distpath $dist --workpath (Join-Path $buildRoot "obj") --specpath $buildRoot `
        $source
    if ($LASTEXITCODE -ne 0) {
        throw "$executableName.exe build failed. Close the running app before rebuilding."
    }
} finally {
    $env:PYINSTALLER_CONFIG_DIR = $oldCache
}

Write-Host "Built $(Join-Path $dist "$executableName.exe")"
