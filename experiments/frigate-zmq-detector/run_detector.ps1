# Starts the pinned Frigate ZMQ detector client on Windows loopback.
# Frigate (Docker) reaches it at tcp://host.docker.internal:5555.
# Only DirectML is requested by default so a GPU failure is loud instead of a
# silent CPU fallback.
param(
    [string]$Endpoint = "tcp://127.0.0.1:5555",
    [string[]]$Providers = @("DmlExecutionProvider")
)

$ErrorActionPreference = "Stop"
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$client = Join-Path $PSScriptRoot "vendor\detector\zmq_onnx_client.py"
if (-not (Test-Path $python)) { throw "Missing $python. Follow the setup in README.md." }
if (-not (Test-Path $client)) { throw "Missing $client. Run fetch_client.py first." }

& $python $client --endpoint $Endpoint --model AUTO --providers @Providers
exit $LASTEXITCODE
