param(
    [string]$EnvFile = ".env",
    [string]$OutputFile = "tests/dahua-events/output/cgi-events.log"
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Net.Http

function Read-DotEnv {
    param([string]$Path)

    $values = @{}
    $lineNumber = 0
    Get-Content -LiteralPath $Path | ForEach-Object {
        $lineNumber++
        $line = $_.Trim()
        if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith("#")) {
            return
        }
        if ($line -notmatch '^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            throw "Malformed .env line $lineNumber"
        }
        $key = $matches[1]
        $value = $matches[2].Trim().Trim('"').Trim("'")
        $values[$key] = $value
    }
    return $values
}

function Require-Value {
    param(
        [hashtable]$Values,
        [string]$Key
    )

    if (-not $Values.ContainsKey($Key) -or
        [string]::IsNullOrWhiteSpace($Values[$Key])) {
        throw "Missing required environment variable: $Key"
    }
    return $Values[$Key]
}

$config = Read-DotEnv -Path $EnvFile
$cameraHost = Require-Value -Values $config -Key "DAHUA_HOST"
$cameraUser = Require-Value -Values $config -Key "DAHUA_USER"
$cameraPassword = Require-Value -Values $config -Key "DAHUA_PASSWORD"
$httpPort = if ($config.ContainsKey("DAHUA_HTTP_PORT")) {
    [int]$config["DAHUA_HTTP_PORT"]
} else {
    80
}

$outputPath = [IO.Path]::GetFullPath($OutputFile)
$outputDirectory = [IO.Path]::GetDirectoryName($outputPath)
[IO.Directory]::CreateDirectory($outputDirectory) | Out-Null

$handler = [System.Net.Http.HttpClientHandler]::new()
$handler.Credentials = [System.Net.NetworkCredential]::new(
    $cameraUser,
    $cameraPassword
)
$client = [System.Net.Http.HttpClient]::new($handler)
$client.Timeout = [System.Threading.Timeout]::InfiniteTimeSpan
$requestUri = "http://${cameraHost}:${httpPort}/cgi-bin/eventManager.cgi?action=attach&codes=%5BAll%5D"
$request = [System.Net.Http.HttpRequestMessage]::new(
    [System.Net.Http.HttpMethod]::Get,
    $requestUri
)

$response = $null
$stream = $null
$reader = $null
$writer = $null

try {
    $response = $client.SendAsync(
        $request,
        [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead
    ).GetAwaiter().GetResult()
    $response.EnsureSuccessStatusCode() | Out-Null

    $stream = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
    $reader = [IO.StreamReader]::new($stream)
    $writer = [IO.StreamWriter]::new(
        $outputPath,
        $false,
        [Text.UTF8Encoding]::new($false)
    )
    $writer.AutoFlush = $true

    Write-Host "CGI event capture connected. Press Ctrl+C to stop."
    Write-Host "Output: $outputPath"

    while ($true) {
        $line = $reader.ReadLineAsync().GetAwaiter().GetResult()
        if ($null -eq $line) {
            break
        }
        $writer.WriteLine($line)
    }
} finally {
    if ($null -ne $writer) { $writer.Dispose() }
    if ($null -ne $reader) { $reader.Dispose() }
    if ($null -ne $stream) { $stream.Dispose() }
    if ($null -ne $response) { $response.Dispose() }
    $request.Dispose()
    $client.Dispose()
    $handler.Dispose()
}
