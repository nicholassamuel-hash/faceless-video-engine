<#
    start-n8n.ps1 — launch a LOCAL n8n on http://localhost:5678

    Runs the npm-installed (global) n8n with local-friendly defaults.
    Workflows, credentials, and the encryption key persist in:
        C:\Users\<you>\.n8n

    Usage:
        pwsh -File scripts/start-n8n.ps1            # start + open the editor
        pwsh -File scripts/start-n8n.ps1 -NoBrowser # start without opening a browser
        Ctrl+C                                      # stop (when run in a window)

    For an always-on setup that survives logoff/reboot, register this with
    Windows Task Scheduler (see scripts/install-n8n-service.ps1).

    Note: this LOCAL setup is separate from the Docker VPS stack in ai-agency/.
#>
param([switch]$NoBrowser)

$ErrorActionPreference = 'Stop'

# --- Local-friendly environment ---------------------------------------------
$env:N8N_SECURE_COOKIE           = 'false'   # allow login over plain http://localhost
$env:N8N_HOST                    = 'localhost'
$env:N8N_PORT                    = '5678'
$env:N8N_PROTOCOL                = 'http'
$env:N8N_RUNNERS_ENABLED         = 'true'    # use task runners (recommended)
$env:N8N_DIAGNOSTICS_ENABLED     = 'false'   # no telemetry on a local box
$env:N8N_PERSONALIZATION_ENABLED = 'false'

# Resolve n8n's entry point WITHOUT depending on PATH (works under Task Scheduler).
$n8nBin = Join-Path $env:APPDATA 'npm\node_modules\n8n\bin\n8n'
if (-not (Test-Path $n8nBin)) {
    $cmd = (Get-Command n8n -ErrorAction SilentlyContinue).Source
    if (-not $cmd) {
        Write-Host "n8n is not installed. Install it with:  npm install -g n8n" -ForegroundColor Red
        exit 1
    }
    # Fall back to whatever PATH resolves.
    & $cmd start
    exit $LASTEXITCODE
}

Write-Host "Starting local n8n -> http://localhost:5678   (press Ctrl+C to stop)" -ForegroundColor Cyan

# Open the editor in the default browser once the server responds.
if (-not $NoBrowser) {
    Start-Job -ScriptBlock {
        for ($i = 0; $i -lt 90; $i++) {
            try {
                Invoke-WebRequest 'http://localhost:5678' -UseBasicParsing -TimeoutSec 2 | Out-Null
                Start-Process 'http://localhost:5678'
                break
            } catch { Start-Sleep -Seconds 2 }
        }
    } | Out-Null
}

# Run n8n in the foreground (blocks until Ctrl+C / task stop).
& node $n8nBin start
