$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $ProjectRoot "backend"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $ProjectRoot "logs"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

$LogFile = Join-Path $LogDir ("sku-sync-{0}.log" -f (Get-Date -Format "yyyyMMdd"))

Set-Location $BackendDir
"[{0}] Starting SKU sync from data_raw.wms.bas_sku" -f (Get-Date -Format "s") | Tee-Object -FilePath $LogFile -Append
& $Python manage.py sync_sku_from_wms *>> $LogFile
$ExitCode = $LASTEXITCODE
"[{0}] Finished SKU sync with exit code {1}" -f (Get-Date -Format "s"), $ExitCode | Tee-Object -FilePath $LogFile -Append
exit $ExitCode
