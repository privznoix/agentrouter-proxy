# ============================================================
# Install AgentRouter Proxy Auto Start
# ============================================================

$ErrorActionPreference = "Stop"

# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------

$ProjectDir = "C:\Tools\agentrouter-proxy"

$StartScript = "$ProjectDir\start-agentrouter-proxy.ps1"

$TaskName = "AgentRouter Direct Proxy"

# ------------------------------------------------------------
# Validate
# ------------------------------------------------------------

if (-not (Test-Path $StartScript)) {
    throw "Start script not found: $StartScript"
}

# ------------------------------------------------------------
# Remove previous task if exists
# ------------------------------------------------------------

$existingTask = Get-ScheduledTask `
    -TaskName $TaskName `
    -ErrorAction SilentlyContinue

if ($null -ne $existingTask) {

    Write-Host "Removing existing task..."

    Unregister-ScheduledTask `
        -TaskName $TaskName `
        -Confirm:$false
}

# ------------------------------------------------------------
# Action
# ------------------------------------------------------------

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument (
        "-NoProfile -ExecutionPolicy Bypass " +
        "-File `"$StartScript`""
    ) `
    -WorkingDirectory $ProjectDir

# ------------------------------------------------------------
# Trigger
# ------------------------------------------------------------
#
# Start when the current Windows user logs in.
# This ensures user environment variables such as
# AGENTROUTER_API_KEY are available.
#
# ------------------------------------------------------------

$trigger = New-ScheduledTaskTrigger `
    -AtLogOn `
    -User $env:USERNAME

# ------------------------------------------------------------
# Principal
# ------------------------------------------------------------

$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

# ------------------------------------------------------------
# Settings
# ------------------------------------------------------------

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (
        New-TimeSpan -Hours 1
    )

# ------------------------------------------------------------
# Register
# ------------------------------------------------------------

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "Start AgentRouter Direct Proxy on Windows login." |
    Out-Null

Write-Host ""
Write-Host "========================================="
Write-Host " Auto Start Installed"
Write-Host "========================================="
Write-Host ""
Write-Host "Task:"
Write-Host "  $TaskName"
Write-Host ""
Write-Host "Trigger:"
Write-Host "  At user logon"
Write-Host ""
Write-Host "Script:"
Write-Host "  $StartScript"
Write-Host ""