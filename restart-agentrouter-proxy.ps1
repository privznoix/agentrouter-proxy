# ============================================================
# Restart AgentRouter Direct Proxy
# ============================================================

$ErrorActionPreference = "Stop"

$ProjectDir = "C:\Tools\agentrouter-proxy"

$StopScript = "$ProjectDir\stop-agentrouter-proxy.ps1"
$StartScript = "$ProjectDir\start-agentrouter-proxy.ps1"

# ------------------------------------------------------------
# Validate
# ------------------------------------------------------------

if (-not (Test-Path $StopScript)) {
    throw "Stop script not found: $StopScript"
}

if (-not (Test-Path $StartScript)) {
    throw "Start script not found: $StartScript"
}

# ------------------------------------------------------------
# Header
# ------------------------------------------------------------

Write-Host ""
Write-Host "========================================="
Write-Host " Restart AgentRouter Direct Proxy"
Write-Host "========================================="
Write-Host ""

# ------------------------------------------------------------
# Stop
# ------------------------------------------------------------

Write-Host "[1/2] Stopping proxy..."
Write-Host ""

& $StopScript

# ------------------------------------------------------------
# Small delay
# ------------------------------------------------------------

Start-Sleep -Seconds 1

# ------------------------------------------------------------
# Start
# ------------------------------------------------------------

Write-Host "[2/2] Starting proxy..."
Write-Host ""

& $StartScript

Write-Host ""
Write-Host "Restart completed."
Write-Host ""