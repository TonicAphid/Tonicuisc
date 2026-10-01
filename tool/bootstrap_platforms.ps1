# Generate the missing native platform projects under app/ (windows / linux /
# macos / ios), then apply the native patches and fetch dependencies.
# Existing files are never overwritten by "flutter create", so lib/ is safe.
# ASCII-only on purpose: Windows PowerShell 5.1 reads .ps1 as ANSI.
param(
    [string]$Platforms = 'windows,linux,macos,ios'
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$app = Join-Path $root 'app'

Push-Location $app
try {
    flutter create --project-name tonicuisc --org com.tonicuisc --platforms=$Platforms .
    if ($LASTEXITCODE -ne 0) { throw "flutter create failed" }
}
finally {
    Pop-Location
}

& (Join-Path $PSScriptRoot 'apply_patches.ps1')

Push-Location $app
try {
    flutter pub get
    if ($LASTEXITCODE -ne 0) { throw "flutter pub get failed" }
}
finally {
    Pop-Location
}

Write-Host "Done. You can now run: flutter run / flutter build"
