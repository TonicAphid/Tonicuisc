# Start the Tonicuisc backend (run from anywhere).
# ASCII-only on purpose: Windows PowerShell 5.1 reads .ps1 as ANSI and would
# choke on non-ASCII characters.
$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

if (-not (Test-Path '.env')) {
    Write-Host "Note: no .env found, using defaults (0.0.0.0:8000, sources migu,kuwo)"
}

$port = if ($env:TONICUISC_PORT) { $env:TONICUISC_PORT } else { '8000' }
Write-Host "Server: http://127.0.0.1:$port  (docs: http://127.0.0.1:$port/docs)"
python -m tonicuisc_server
