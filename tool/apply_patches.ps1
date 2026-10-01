# Copy the native config files from tool/platform_patches into the generated
# platform folders. Platforms that were not generated are skipped.
# ASCII-only on purpose: Windows PowerShell 5.1 reads .ps1 as ANSI.
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$source = Join-Path $PSScriptRoot 'platform_patches'
$target = Join-Path $root 'app'

if (-not (Test-Path $target)) { throw "app directory not found: $target" }

$copied = 0
Get-ChildItem -Path $source -Recurse -File | ForEach-Object {
    $relative = $_.FullName.Substring($source.Length + 1)
    $destination = Join-Path $target $relative
    $destinationDir = Split-Path -Parent $destination
    if (-not (Test-Path $destinationDir)) {
        Write-Host "skip (platform not generated): $relative"
        return
    }
    Copy-Item -Path $_.FullName -Destination $destination -Force
    Write-Host "patched: $relative"
    $copied++
}

Write-Host "$copied native config file(s) applied"
