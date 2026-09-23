param(
    [ValidateSet('NvDCF', 'NvDeepSORT', 'NvDCFReassoc')]
    [string]$Tracker = 'NvDCF',
    [ValidateSet('PeopleNet', 'PeopleNetV2', 'RTDETR')]
    [string]$Detector = 'PeopleNet',
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
$detectorKey = $Detector.ToLowerInvariant()
$containerEnvironment = @()
if ($Tracker -eq 'NvDCFReassoc') {
    $outputStem = 'nvdcf-reassoc'
    if ($Detector -ne 'PeopleNet') {
        $detectorConfig = if ($Detector -eq 'RTDETR') {
            'config_infer_rtdetr_warehouse.txt'
        } else {
            'config_infer_peoplenet_transformer_v2.txt'
        }
        $outputStem = "$detectorKey-nvdcf-reassoc"
        $containerEnvironment = @(
            '-e', "PRIMARY_GIE_CONFIG=/workspace/config/$detectorConfig",
            '-e', "OUTPUT_STEM=$outputStem"
        )
    }
    $detectionDirectoryName = "kitti-$outputStem"
    $trackDirectoryName = "kitti-track-$outputStem-person"
    $containerCommand = @('bash', '/workspace/config/run_nvdcf_reassoc.sh')
} else {
    if ($Detector -ne 'PeopleNet') {
        throw "$Detector is initially supported only with NvDCFReassoc"
    }
    $detectionDirectoryName = "kitti-$key"
    $trackDirectoryName = "kitti-track-$key-person"
    $configFile = Join-Path $configPath "deepstream_$key.txt"
    $containerCommand = @(
        'deepstream-app', '-c', "/workspace/config/$(Split-Path -Leaf $configFile)"
    )
}
$detectionDirectory = Join-Path $outputPath $detectionDirectoryName
$trackDirectory = Join-Path $outputPath $trackDirectoryName
$outputRootFull = [System.IO.Path]::GetFullPath($outputRoot)
$generatedDirectories = @($detectionDirectory, $trackDirectory)
foreach ($generatedDirectory in $generatedDirectories) {
    $generatedDirectoryFull = [System.IO.Path]::GetFullPath($generatedDirectory)
    if (-not $generatedDirectoryFull.StartsWith(
            $outputRootFull + [System.IO.Path]::DirectorySeparatorChar,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        throw "Refusing to replace DeepStream output outside $outputRootFull"
    }
    if (Test-Path -LiteralPath $generatedDirectory -PathType Container) {
        Remove-Item -LiteralPath $generatedDirectory -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $generatedDirectory | Out-Null
}

$image = 'nvcr.io/nvidia/deepstream:9.1-samples-multiarch@sha256:10eca409b3894e91c1bac915c9f1346307e56695e552487cbe8cf2f58a3f998f'
$dockerArguments = @(
    'run', '--rm', '--gpus', 'all'
)
$dockerArguments += $containerEnvironment
$dockerArguments += @(
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

$reportName = if ($Detector -eq 'PeopleNet') {
    "$key-report.json"
} else {
    "$detectorKey-$key-report.json"
}
$reportPath = Join-Path $outputPath $reportName
$selectedLabel = if ($Detector -eq 'RTDETR') { 'person' } else { 'Person' }
& $pythonPath (Join-Path $PSScriptRoot 'analyze_deepstream_tracks.py') `
    $trackDirectory --label $selectedLabel --output $reportPath
exit $LASTEXITCODE
