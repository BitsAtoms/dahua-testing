[CmdletBinding()]
param(
    [ValidateSet("migraphx", "directml")]
    [string]$Provider = "migraphx",
    [int]$GpuA = 0,
    [int]$GpuB = 1,
    [int]$Warmup = 10,
    [int]$Iterations = 100
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root ".venv\Scripts\python.exe"
$stamp = Get-Date -Format "yyyyMMddTHHmmss"
$report = Join-Path $root "output\benchmark-$stamp.jsonl"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Environment not found. Run .\experiments\windows-onnx-gpu\bootstrap.ps1 first."
}

& $python (Join-Path $root "benchmark.py") inspect --prepare-provider $Provider --output $report
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $python (Join-Path $root "benchmark.py") run --provider cpu --warmup $Warmup --iterations $Iterations --output $report
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $python (Join-Path $root "benchmark.py") run --provider $Provider --device-index $GpuA --warmup $Warmup --iterations $Iterations --output $report
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $python (Join-Path $root "benchmark.py") run --provider $Provider --device-index $GpuB --warmup $Warmup --iterations $Iterations --output $report
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $python (Join-Path $root "benchmark.py") dual --provider $Provider --device-index $GpuA --device-index $GpuB --warmup $Warmup --iterations $Iterations --output $report
exit $LASTEXITCODE
