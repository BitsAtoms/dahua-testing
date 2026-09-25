[CmdletBinding()]
param(
    [switch]$CpuOnly,
    [string]$Python = "py"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$venv = Join-Path $root ".venv"
$bootstrapTemp = Join-Path $root "output\bootstrap-temp"
New-Item -ItemType Directory -Force -Path $bootstrapTemp | Out-Null
$env:TEMP = $bootstrapTemp
$env:TMP = $bootstrapTemp

if ($Python -eq "py") {
    & py -3.12 -m venv $venv
} else {
    & $Python -m venv $venv
}
if ($LASTEXITCODE -ne 0) { throw "Python failed to create the virtual environment (exit $LASTEXITCODE)." }

$venvPython = Join-Path $venv "Scripts\python.exe"
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip self-upgrade failed (exit $LASTEXITCODE)." }

if ($CpuOnly) {
    & $venvPython -m pip install -r (Join-Path $root "requirements-cpu.txt")
    if ($LASTEXITCODE -ne 0) { throw "CPU dependency installation failed (exit $LASTEXITCODE)." }
} else {
    if ([Environment]::OSVersion.Version.Build -lt 26100) {
        throw "Windows ML execution providers require Windows 11 24H2 build 26100 or newer."
    }
    & $venvPython -m pip install -r (Join-Path $root "requirements-windowsml.txt")
    if ($LASTEXITCODE -ne 0) { throw "Windows ML dependency installation failed (exit $LASTEXITCODE)." }
}

& $venvPython (Join-Path $root "benchmark.py") inspect
if ($LASTEXITCODE -ne 0) { throw "Benchmark inventory failed (exit $LASTEXITCODE)." }
