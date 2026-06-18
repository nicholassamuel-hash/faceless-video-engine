<#
    install-n8n-service.ps1 — run local n8n via Windows Task Scheduler

    Registers a scheduled task ("n8n-local") that launches scripts/start-n8n.ps1
    at logon and keeps it running, INDEPENDENT of any terminal or Claude session.
    Run once. n8n will then be available at http://localhost:5678 whenever you're
    logged in.

    Install / (re)start now:   pwsh -File scripts/install-n8n-service.ps1
    Remove it:                 pwsh -File scripts/install-n8n-service.ps1 -Uninstall

    Manual control afterwards:
        Start-ScheduledTask -TaskName n8n-local
        Stop-ScheduledTask  -TaskName n8n-local
#>
param([switch]$Uninstall)

$ErrorActionPreference = 'Stop'
$TaskName = 'n8n-local'
$script   = Join-Path $PSScriptRoot 'start-n8n.ps1'
$pwsh     = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
if (-not $pwsh) { $pwsh = (Get-Command powershell).Source }

if ($Uninstall) {
    Stop-ScheduledTask  -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed scheduled task '$TaskName'." -ForegroundColor Yellow
    return
}

$action    = New-ScheduledTaskAction -Execute $pwsh `
                -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" -NoBrowser"
$trigger   = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
                -LogonType Interactive -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
                -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "Registered scheduled task '$TaskName' (auto-starts n8n at logon)." -ForegroundColor Green

Start-ScheduledTask -TaskName $TaskName
Write-Host "Started n8n. It will be at http://localhost:5678 shortly." -ForegroundColor Green
