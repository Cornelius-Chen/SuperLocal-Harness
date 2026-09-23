$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3.12 -m unittest discover -s tests -v
} else {
    & python -m unittest discover -s tests -v
}

