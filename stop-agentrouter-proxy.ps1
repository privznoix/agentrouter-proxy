# ============================================================
# Stop AgentRouter Direct Proxy
# ============================================================

$ErrorActionPreference = "Stop"

# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------

$ProjectDir = "C:\Tools\agentrouter-proxy"

$HostAddress = "127.0.0.1"
$Port = 4020

# ------------------------------------------------------------
# Helper: get process using port
# ------------------------------------------------------------

function Get-PortProcessIds {
    param(
        [int]$TargetPort
    )

    @(
        Get-NetTCPConnection `
            -LocalPort $TargetPort `
            -State Listen `
            -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    )
}

# ------------------------------------------------------------
# Header
# ------------------------------------------------------------

Write-Host ""
Write-Host "========================================="
Write-Host " Stop AgentRouter Direct Proxy"
Write-Host "========================================="
Write-Host ""

# ------------------------------------------------------------
# Stop port owner
# ------------------------------------------------------------

$pids = Get-PortProcessIds $Port

if ($pids.Count -gt 0) {

    foreach ($pidValue in $pids) {

        $process = Get-Process `
            -Id $pidValue `
            -ErrorAction SilentlyContinue

        if ($null -ne $process) {

            Write-Host `
                "Stopping PID $pidValue ($($process.ProcessName))..."

            Stop-Process `
                -Id $pidValue `
                -Force `
                -ErrorAction SilentlyContinue
        }
    }

}
else {

    Write-Host "No process is listening on port $Port."
}

# ------------------------------------------------------------
# Extra cleanup:
# remove Uvicorn process from this project if it still exists
# ------------------------------------------------------------

$projectProcesses = @(
    Get-CimInstance Win32_Process `
        -ErrorAction SilentlyContinue |
    Where-Object {
        $_.CommandLine -and
        (
            $_.CommandLine -like "*C:\Tools\agentrouter-proxy*agentrouter-proxy:app*"
        )
    }
)

foreach ($processInfo in $projectProcesses) {

    try {

        Write-Host `
            "Stopping remaining proxy PID $($processInfo.ProcessId)..."

        Stop-Process `
            -Id $processInfo.ProcessId `
            -Force `
            -ErrorAction SilentlyContinue
    }
    catch {
        Write-Warning `
            "Could not stop PID $($processInfo.ProcessId)"
    }
}

Start-Sleep -Milliseconds 500

Write-Host ""
Write-Host "AgentRouter proxy stopped."
Write-Host ""