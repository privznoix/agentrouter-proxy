# ============================================================
# Start AgentRouter Direct Proxy
# ============================================================

$ErrorActionPreference = "Stop"

# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------

$ProjectDir = "C:\Tools\agentrouter-proxy"

$Python = "$ProjectDir\.venv\Scripts\python.exe"

$HostAddress = "127.0.0.1"
$Port = 4020

$LogDir = "$ProjectDir\logs"

$StdOutLog = "$LogDir\uvicorn.log"
$StdErrLog = "$LogDir\uvicorn-error.log"

# ------------------------------------------------------------
# Validate paths
# ------------------------------------------------------------

if (-not (Test-Path $ProjectDir)) {
    throw "Project directory not found: $ProjectDir"
}

if (-not (Test-Path $Python)) {
    throw "Python executable not found: $Python"
}

if (-not (Test-Path $LogDir)) {
    New-Item `
        -ItemType Directory `
        -Path $LogDir `
        -Force |
        Out-Null
}

# ------------------------------------------------------------
# Check permanent environment variables
# ------------------------------------------------------------

if ([string]::IsNullOrWhiteSpace($env:AGENTROUTER_API_KEY)) {
    throw "AGENTROUTER_API_KEY is not available in this environment."
}

if ([string]::IsNullOrWhiteSpace($env:AGENTROUTER_PROXY_API_KEY)) {
    $env:AGENTROUTER_PROXY_API_KEY = "local-agentrouter"
}

if ([string]::IsNullOrWhiteSpace($env:AGENTROUTER_BASE_URL)) {
    $env:AGENTROUTER_BASE_URL = "https://agentrouter.org/v1"
}

if ([string]::IsNullOrWhiteSpace($env:AGENTROUTER_USER_AGENT)) {
    $env:AGENTROUTER_USER_AGENT = "codex_cli_rs/1.0.0 (Windows; x86_64)"
}

# ------------------------------------------------------------
# Helper: test port
# ------------------------------------------------------------

function Test-Port {
    param(
        [string]$TargetHost,
        [int]$TargetPort
    )

    $client = $null

    try {
        $client = New-Object System.Net.Sockets.TcpClient

        $asyncResult = $client.BeginConnect(
            $TargetHost,
            $TargetPort,
            $null,
            $null
        )

        $connected = $asyncResult.AsyncWaitHandle.WaitOne(1000)

        if ($connected -and $client.Connected) {
            $client.EndConnect($asyncResult)
            return $true
        }

        return $false
    }
    catch {
        return $false
    }
    finally {
        if ($null -ne $client) {
            $client.Close()
            $client.Dispose()
        }
    }
}

# ------------------------------------------------------------
# Header
# ------------------------------------------------------------

Write-Host ""
Write-Host "========================================="
Write-Host " AgentRouter Direct Proxy"
Write-Host "========================================="
Write-Host ""

# ------------------------------------------------------------
# Already running?
# ------------------------------------------------------------

if (Test-Port $HostAddress $Port) {

    Write-Host "AgentRouter proxy already running."
    Write-Host "Endpoint: http://$HostAddress`:$Port/v1"
    Write-Host ""

    exit 0
}

# ------------------------------------------------------------
# Start Uvicorn
# ------------------------------------------------------------

Write-Host "Starting AgentRouter proxy..."

Start-Process `
    -FilePath $Python `
    -ArgumentList @(
        "-m",
        "uvicorn",
        "agentrouter-proxy:app",
        "--host", $HostAddress,
        "--port", $Port,
        "--workers", "1",
        "--no-access-log"
    ) `
    -WorkingDirectory $ProjectDir `
    -RedirectStandardOutput $StdOutLog `
    -RedirectStandardError $StdErrLog `
    -WindowStyle Hidden

# ------------------------------------------------------------
# Wait for readiness
# ------------------------------------------------------------

$maxAttempts = 30
$attempt = 0

while (-not (Test-Port $HostAddress $Port)) {

    Start-Sleep -Seconds 1
    $attempt++

    if ($attempt -ge $maxAttempts) {

        Write-Host ""
        Write-Host "Proxy failed to start."
        Write-Host ""
        Write-Host "Check:"
        Write-Host "  $StdOutLog"
        Write-Host "  $StdErrLog"
        Write-Host ""

        exit 1
    }
}

# ------------------------------------------------------------
# Final status
# ------------------------------------------------------------

Write-Host ""
Write-Host "AgentRouter proxy is READY."
Write-Host ""
Write-Host "Endpoint:"
Write-Host "  http://$HostAddress`:$Port/v1"
Write-Host ""
Write-Host "Logs:"
Write-Host "  $LogDir"
Write-Host ""