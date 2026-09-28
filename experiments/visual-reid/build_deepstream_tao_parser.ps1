param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$revision = '0a63ef8be554346820e01c96eaea390d04962939'
$expectedSha256 = 'F92E905806E25F5303C290C2D98CA835DF14EB842530C505156FB2A29F1398F2'
$image = 'nvcr.io/nvidia/deepstream:9.1-triton-multiarch@sha256:fd31f5b44ababdbdee8cd397a375e888191b49e402ac237254a4cdc239130f5b'
$vendorRoot = Join-Path $PSScriptRoot 'output\vendor\DeepStream'
$parserRelativePath = 'src/apps/reference_apps/deepstream-tracker-3d-multi-view/models/RTDETR/custom_parser'
$parserSource = Join-Path $vendorRoot $parserRelativePath
$destination = Join-Path $PSScriptRoot 'models\deepstream\rtdetr_parser'
$library = Join-Path $destination 'libnvds_infercustomparser_tao.so'

if (-not $Force -and (Test-Path -LiteralPath $library -PathType Leaf)) {
    $actualSha256 = (Get-FileHash -LiteralPath $library -Algorithm SHA256).Hash
    if ($actualSha256 -eq $expectedSha256) {
        Write-Host "verified=$library"
        exit 0
    }
}

if (-not (Test-Path -LiteralPath (Join-Path $vendorRoot '.git') -PathType Container)) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $vendorRoot) | Out-Null
    & git clone --filter=blob:none --no-checkout https://github.com/NVIDIA/DeepStream.git $vendorRoot
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

& git -C $vendorRoot sparse-checkout init --cone
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& git -C $vendorRoot sparse-checkout set $parserRelativePath
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& git -C $vendorRoot fetch --depth 1 origin $revision
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& git -C $vendorRoot checkout --detach $revision
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

New-Item -ItemType Directory -Force -Path $destination | Out-Null
& docker run --rm `
    -v "${parserSource}:/workspace/parser" `
    -v "${destination}:/workspace/output" `
    -w /workspace/parser `
    $image `
    bash -lc 'make clean && make && cp libnvds_infercustomparser_tao.so /workspace/output/'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$actualSha256 = (Get-FileHash -LiteralPath $library -Algorithm SHA256).Hash
if ($actualSha256 -ne $expectedSha256) {
    throw "Unexpected parser checksum: $actualSha256"
}
Write-Host "built=$library"
