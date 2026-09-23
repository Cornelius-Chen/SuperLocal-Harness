$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3.12 -m superlocal_harness doctor
} else {
    & python -m superlocal_harness doctor
}

