$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    $python = "python"
}

Set-Location $repoRoot
& $python "backend\manage.py" "runserver" "127.0.0.1:8010"
