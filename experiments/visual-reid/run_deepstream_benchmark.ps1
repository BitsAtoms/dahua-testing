param(
    [ValidateSet('NvDCF', 'NvDeepSORT', 'NvDCFReassoc')]
    [string]$Tracker = 'NvDCF',
    [string]$Recording = 'experiments/visual-reid/output/detector-benchmark/20260922T094309Z-two_person_crossing_occlusion/reuniones_terminator.mkv'
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$recordingPath = (Resolve-Path (Join-Path $repoRoot $Recording)).Path
$configPath = (Resolve-Path (Join-Path $PSScriptRoot 'deepstream')).Path
$modelPath = (Resolve-Path (Join-Path $PSScriptRoot 'models\deepstream')).Path
$outputRoot = Join-Path $PSScriptRoot 'output\deepstream'
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Visual ReID Python environment not found: $pythonPath"
}
$scenarioName = Split-Path -Leaf (Split-Path -Parent $recordingPath)
$recordingName = [System.IO.Path]::GetFileNameWithoutExtension($recordingPath)
$runName = "$scenarioName-$recordingName"
$outputPath = Join-Path $outputRoot $runName
New-Item -ItemType Directory -Force -Path $outputPath | Out-Null

$key = $Tracker.ToLowerInvariant()
if ($Tracker -eq 'NvDCFReassoc') {
    $trackDirectoryName = 'kitti-track-nvdcf-reassoc-person'
    $containerCommand = @('bash', '/workspace/config/run_nvdcf_reassoc.sh')
} else {
    $trackDirectoryName = "kitti-track-$key-person"
    $configFile = Join-Path $configPath "deepstream_$key.txt"
    $containerCommand = @(
        'deepstream-app', '-c', "/workspace/config/$(Split-Path -Leaf $configFile)"
    )
}
$trackDirectory = Join-Path $outputPath $trackDirectoryName
$outputRootFull = [System.IO.Path]::GetFullPath($outputRoot)
$trackDirectoryFull = [System.IO.Path]::GetFullPath($trackDirectory)
if (-not $trackDirectoryFull.StartsWith(
        $outputRootFull + [System.IO.Path]::DirectorySeparatorChar,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
    throw "Refusing to replace DeepStream output outside $outputRootFull"
}
if (Test-Path -LiteralPath $trackDirectory -PathType Container) {
    Remove-Item -LiteralPath $trackDirectory -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $trackDirectory | Out-Null

$image = 'nvcr.io/nvidia/deepstream:9.1-samples-multiarch@sha256:10eca409b3894e91c1bac915c9f1346307e56695e552487cbe8cf2f58a3f998f'
$dockerArguments = @(
    'run', '--rm', '--gpus', 'all',
    '-v', "${configPath}:/workspace/config:ro",
    '-v', "${modelPath}:/workspace/models",
    '-v', "${modelPath}:/opt/nvidia/deepstream/deepstream/samples/models/Tracker",
    '-v', "${recordingPath}:/workspace/input/reuniones_terminator.mkv:ro",
    '-v', "${outputPath}:/workspace/output",
    $image
)
$dockerArguments += $containerCommand

& docker @dockerArguments
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$reportPath = Join-Path $outputPath "$key-report.json"
& $pythonPath (Join-Path $PSScriptRoot 'analyze_deepstream_tracks.py') `
    $trackDirectory --output $reportPath
exit $LASTEXITCODE
