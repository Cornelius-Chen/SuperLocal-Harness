$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (-not (Test-Path ".env")) {
    throw "Run .\scripts\setup.ps1 first, then configure .env."
}

$EnvText = Get-Content ".env" -Raw
if ($EnvText -notmatch '(?m)^HARNESS_ACCESS_TOKEN=.{20,}$') {
    throw "Set HARNESS_ACCESS_TOKEN in .env to a random value of at least 20 characters before remote launch."
}

$env:HARNESS_BIND = "0.0.0.0"
Write-Host "Starting on all interfaces. Connect only through your Tailnet and keep the Windows firewall scoped." -ForegroundColor Yellow

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3.12 -m ironman_harness serve
} else {
    & python -m ironman_harness serve
}

