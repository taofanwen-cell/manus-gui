param(
    [int]$Port = 9223
)

$ErrorActionPreference = 'Stop'
$chrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
if (-not (Test-Path -LiteralPath $chrome)) {
    throw "Chrome not found: $chrome"
}

# Dedicated profile so we never touch your daily Chrome login/cookies.
$profile = Join-Path $PSScriptRoot '..\runtime\pdd-cdp-chrome-profile'
$profile = [System.IO.Path]::GetFullPath($profile)
New-Item -ItemType Directory -Force -Path $profile | Out-Null

# Loopback-only debug address; never exposed to LAN or internet.
$args = @(
    "--remote-debugging-address=127.0.0.1",
    "--remote-debugging-port=$Port",
    "--user-data-dir=$profile",
    '--new-window',
    'https://mobile.yangkeduo.com/'
)
Start-Process -FilePath $chrome -ArgumentList $args

Write-Host "Opened a dedicated visible Chrome. Log into PDD (pinduoduo) in that window."
Write-Host "When logged in, run:"
Write-Host "`$env:PDD_CDP_URL = 'http://127.0.0.1:$Port'"
Write-Host ".\.venv\Scripts\python.exe .\scripts\pdd_cdp_extract.py"
