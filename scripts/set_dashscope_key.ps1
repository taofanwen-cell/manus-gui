# Save a DashScope API key in a local, Git-ignored file without echoing it.
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$keyPath = Join-Path $projectRoot "config\.dashscope_api_key"

$secureKey = Read-Host "Paste the current DashScope API key (input hidden)" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $plainKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr).Trim()
    if ([string]::IsNullOrWhiteSpace($plainKey)) {
        throw "No key entered; nothing was written."
    }
    [IO.File]::WriteAllText($keyPath, $plainKey, [Text.UTF8Encoding]::new($false))
}
finally {
    if ($bstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
    Remove-Variable plainKey -ErrorAction SilentlyContinue
}

# Restrict the credential file to the current Windows user where possible.
$account = [Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls $keyPath /inheritance:r /grant:r "$account`:(R,W)" | Out-Null
Write-Host "Saved local credential file: $keyPath"
Write-Host "The file is Git-ignored. Run scripts\test_dashscope_connection.py next."
