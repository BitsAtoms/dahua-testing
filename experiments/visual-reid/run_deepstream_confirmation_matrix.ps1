param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$cases = @(
    'experiments/visual-reid/output/detector-benchmark/20260921T113306Z-interference_only/recepcion_terminator.mkv',
    'experiments/visual-reid/output/detector-benchmark/20260921T143019Z-person_near_interference/recepcion_terminator.mkv',
    'experiments/visual-reid/output/detector-benchmark/20260921T143345Z-person_present_mixed_pose/reuniones_terminator.mkv',
    'experiments/visual-reid/output/detector-benchmark/20260921T143706Z-person_present_seated_partial/reuniones_terminator.mkv',
    'experiments/visual-reid/output/detector-benchmark/20260921T144103Z-room_empty_with_displays/reuniones_terminator.mkv',
    'experiments/visual-reid/output/detector-benchmark/20260922T094309Z-two_person_crossing_occlusion/reuniones_terminator.mkv',
    'experiments/visual-reid/output/detector-benchmark/20260923T081054Z-two_person_crossing_occlusion_alternate_camera/recepcion_terminator.mkv'
)
$expectedEligibleTracks = @{
    '20260921T113306Z-interference_only-recepcion_terminator' = 0
    '20260921T143019Z-person_near_interference-recepcion_terminator' = 1
    '20260921T143345Z-person_present_mixed_pose-reuniones_terminator' = 1
    '20260921T143706Z-person_present_seated_partial-reuniones_terminator' = 1
    '20260921T144103Z-room_empty_with_displays-reuniones_terminator' = 0
    '20260922T094309Z-two_person_crossing_occlusion-reuniones_terminator' = 2
    '20260923T081054Z-two_person_crossing_occlusion_alternate_camera-recepcion_terminator' = 2
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$benchmark = Join-Path $PSScriptRoot 'run_deepstream_benchmark.ps1'
$analyzer = Join-Path $PSScriptRoot 'analyze_deepstream_confirmation.py'
$outputRoot = Join-Path $PSScriptRoot 'output\deepstream'

foreach ($recording in $cases) {
    $recordingPath = (Resolve-Path (Join-Path $repoRoot $recording)).Path
    $scenarioName = Split-Path -Leaf (Split-Path -Parent $recordingPath)
    $recordingName = [System.IO.Path]::GetFileNameWithoutExtension($recordingPath)
    $runName = "$scenarioName-$recordingName"
    $outputPath = Join-Path $outputRoot $runName
    $confirmationReport = Join-Path $outputPath 'peoplenetv2-confirmed-by-v1-report.json'
    if (-not $Force -and (Test-Path -LiteralPath $confirmationReport -PathType Leaf)) {
        Write-Host "skipped=$runName reason=confirmation-report-exists"
        continue
    }

    & $benchmark -Tracker NvDCFReassoc -Detector PeopleNetV2 -Recording $recording
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $benchmark -Tracker NvDCFReassoc -Detector PeopleNet -Recording $recording
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    & $pythonPath $analyzer `
        --tracks (Join-Path $outputPath 'kitti-track-peoplenetv2-nvdcf-reassoc-person') `
        --detections (Join-Path $outputPath 'kitti-nvdcf-reassoc') `
        --iou 0.3 --output $confirmationReport
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

$matrixRows = foreach ($runName in $expectedEligibleTracks.Keys | Sort-Object) {
    $reportPath = Join-Path (Join-Path $outputRoot $runName) 'peoplenetv2-confirmed-by-v1-report.json'
    if (-not (Test-Path -LiteralPath $reportPath -PathType Leaf)) {
        throw "Missing confirmation report: $reportPath"
    }
    $report = Get-Content -Raw $reportPath | ConvertFrom-Json
    $eligibleTracks = @($report.tracks | Where-Object maximum_consecutive_confirmations -ge 3)
    [ordered]@{
        scenario = $runName
        expected_eligible_tracks = $expectedEligibleTracks[$runName]
        candidate_tracks = @($report.tracks).Count
        eligible_tracks = $eligibleTracks.Count
        eligible_track_ids = @($eligibleTracks | ForEach-Object { $_.track_id })
        passed = $eligibleTracks.Count -eq $expectedEligibleTracks[$runName]
    }
}
$matrixReport = [ordered]@{
    schema_version = 'deepstream_confirmation_matrix.v1'
    confirmation_policy = [ordered]@{
        detector = 'PeopleNet Transformer v1.1'
        minimum_iou = 0.3
        minimum_consecutive_confirmations = 3
    }
    passed_cases = @($matrixRows | Where-Object passed).Count
    total_cases = @($matrixRows).Count
    cases = @($matrixRows)
}
$matrixReportPath = Join-Path $outputRoot 'peoplenetv2-confirmed-by-v1-matrix-report.json'
$matrixReport | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $matrixReportPath -Encoding utf8
Write-Host "matrix_report=$matrixReportPath passed=$($matrixReport.passed_cases)/$($matrixReport.total_cases)"
