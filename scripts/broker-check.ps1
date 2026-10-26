# Run in your own local PowerShell terminal; never paste credentials into chat.
param([switch]$UseClipboard)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Create .venv and install the package first.' }
$oldEnv = $env:ATLAS_ENV
$oldKey = $env:TRADING212_API_KEY
$oldSecret = $env:TRADING212_API_SECRET
try {
    $env:ATLAS_ENV = 'demo'
    Write-Host 'Enter the two raw Invest Practice values separately. Input is hidden.'
    Write-Host 'Values only: no quotes, variable names, Basic prefix or key:secret combination.'
    if (-not $UseClipboard) {
        Write-Host 'If Ctrl+V fails at the masked prompt, try Shift+Insert or run with -UseClipboard.'
    }
    foreach ($entry in @(@('TRADING212_API_KEY', 'Demo API Key'), @('TRADING212_API_SECRET', 'Demo API Secret'))) {
        if ($UseClipboard) {
            $confirmation = Read-Host "Copy ONLY the $($entry[1]) from Trading 212, then press Enter here WITHOUT pasting" -AsSecureString
            $confirmation.Dispose()
            $clipboardValue = Get-Clipboard -Raw
            try {
                if ([string]::IsNullOrWhiteSpace($clipboardValue)) {
                    throw 'Clipboard has no text. Copy the raw credential and run the script again.'
                }
                [Environment]::SetEnvironmentVariable($entry[0], $clipboardValue, 'Process')
            } finally {
                $clipboardValue = $null
            }
            continue
        }
        $secureValue = Read-Host $entry[1] -AsSecureString
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureValue)
        try {
            [Environment]::SetEnvironmentVariable($entry[0], [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer), 'Process')
        } finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
            $secureValue.Dispose()
        }
    }
    & $pythonPath -m atlas.main broker-check
    $result = $LASTEXITCODE
} finally {
    $env:ATLAS_ENV = $oldEnv
    $env:TRADING212_API_KEY = $oldKey
    $env:TRADING212_API_SECRET = $oldSecret
}
exit $result
