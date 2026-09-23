$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3.12 -m ironman_harness serve
} else {
    & python -m ironman_harness serve
}

