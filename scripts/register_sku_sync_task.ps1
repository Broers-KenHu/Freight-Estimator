$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$SyncScript = Join-Path $ProjectRoot "scripts\sync_sku_from_wms.ps1"
$TaskName = "CourieDelivery SKU Sync"
$UserId = "$env:USERDOMAIN\$env:USERNAME"

$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$SyncScript`""

$Trigger = New-ScheduledTaskTrigger -Daily -At 3:00AM
$Principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description "Sync calculation-related SKU data from data_raw.wms.bas_sku into CourieDelivery every day at 03:00 local Sydney time." `
    -Force | Out-Null

Write-Host "Registered scheduled task: $TaskName for 03:00 local Sydney time."
