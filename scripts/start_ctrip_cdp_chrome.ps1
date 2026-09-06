param(
    [int]$Port = 9222
)

$ErrorActionPreference = 'Stop'
$chrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
if (-not (Test-Path -LiteralPath $chrome)) {
    throw "未找到 Chrome：$chrome"
}

$profile = Join-Path $PSScriptRoot '..\runtime\ctrip-cdp-chrome-profile'
$profile = [System.IO.Path]::GetFullPath($profile)
New-Item -ItemType Directory -Force -Path $profile | Out-Null

# 使用独立、可见的配置目录，避免修改或复制日常 Chrome 的登录配置。
# 调试地址固定到回环网卡，禁止局域网或公网连接。
$args = @(
    "--remote-debugging-address=127.0.0.1",
    "--remote-debugging-port=$Port",
    "--user-data-dir=$profile",
    '--new-window',
    'https://www.ctrip.com/?allianceid=564348&sid=18845228'
)
Start-Process -FilePath $chrome -ArgumentList $args
Write-Host "已打开专用的可见 Chrome。请在该窗口中自行登录携程。"
Write-Host "完成后，在项目根目录运行："
Write-Host "`$env:CTRIP_CDP_URL = 'http://127.0.0.1:$Port'"
Write-Host ".\.venv\Scripts\python.exe .\scripts\test_ctrip_cdp_connection.py"
