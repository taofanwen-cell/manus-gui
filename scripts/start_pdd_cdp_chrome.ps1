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
$chromeArgs = @(
    "--remote-debugging-address=127.0.0.1",
    "--remote-debugging-port=$Port",
    "--user-data-dir=`"$profile`"",
    '--no-first-run',
    '--no-default-browser-check',
    '--new-window',
    'https://mobile.yangkeduo.com/'
)

# IMPORTANT: `Start-Process` alone is not enough. When this script runs from a
# non-interactive shell (automation / agent), the spawned Chrome is a child of
# this PowerShell session and gets reaped the moment the session ends — the
# debug port then disappears and `connect_over_cdp` hangs for 180s.
# `cmd /c start ""` hands the process to Windows' shell, fully detaching it.
$cmdLine = '"' + $chrome + '" ' + ($chromeArgs -join ' ')
& cmd.exe /c "start `"PDD-CDP-Chrome`" $cmdLine"

# Wait until the debug port actually answers, so the caller knows it is usable.
$ready = $false
for ($i = 1; $i -le 30; $i++) {
    Start-Sleep -Seconds 1
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/json/version" -TimeoutSec 2 -UseBasicParsing
        if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
}

if (-not $ready) {
    throw "Chrome started but the debug port $Port did not answer within 30s. Check for an existing Chrome using this profile."
}

Write-Host "Opened a dedicated visible Chrome on port $Port. Log into PDD (pinduoduo) in that window."
Write-Host "When logged in, run:"
Write-Host "`$env:PDD_CDP_URL = 'http://127.0.0.1:$Port'"
Write-Host ".\.venv\Scripts\python.exe .\scripts\pdd_cdp_extract.py"
