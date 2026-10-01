# 生成 app/ 下缺失的原生平台工程（windows / linux / macos / ios），然后打补丁并装依赖。
# 已存在的文件不会被 flutter create 覆盖，lib/ 下的代码始终保留。
param(
    [string]$Platforms = 'windows,linux,macos,ios'
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$app = Join-Path $root 'app'

Push-Location $app
try {
    flutter create --project-name tonicuisc --org com.tonicuisc --platforms=$Platforms .
    if ($LASTEXITCODE -ne 0) { throw "flutter create 失败" }
}
finally {
    Pop-Location
}

& (Join-Path $PSScriptRoot 'apply_patches.ps1')

Push-Location $app
try {
    flutter pub get
    if ($LASTEXITCODE -ne 0) { throw "flutter pub get 失败" }
}
finally {
    Pop-Location
}

Write-Host "完成：平台工程已生成，可以直接 flutter run / flutter build"
