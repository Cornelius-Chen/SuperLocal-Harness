$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$Python = Get-Command py -ErrorAction SilentlyContinue
if ($Python) {
    $PythonArgs = @("-3.12")
    $PythonExe = "py"
} else {
    $PythonExe = "python"
    $PythonArgs = @()
}

$VersionText = & $PythonExe @PythonArgs --version 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "Python 3.12+ was not found. Install Python 3.12 or uv first."
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from the safe template." -ForegroundColor Green
} else {
    Write-Host ".env already exists; it was not overwritten." -ForegroundColor Yellow
}

New-Item -ItemType Directory -Force "data" | Out-Null

Write-Host "`nChecking configuration..." -ForegroundColor Cyan
& $PythonExe @PythonArgs -m ironman_harness config | Out-Host

Write-Host "`nSetup complete." -ForegroundColor Green
Write-Host "1. Open .env and set HARNESS_PROJECT_ROOTS plus DEEPSEEK_API_KEY if wanted."
Write-Host "2. Run: .\scripts\run-local.ps1"

