# 把 tool/platform_patches 下的原生配置覆盖到已生成的平台目录。
# 只覆盖目标目录已存在的平台，未生成的平台直接跳过。
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$source = Join-Path $PSScriptRoot 'platform_patches'
$target = Join-Path $root 'app'

if (-not (Test-Path $target)) { throw "找不到 app 目录: $target" }

$copied = 0
Get-ChildItem -Path $source -Recurse -File | ForEach-Object {
    $relative = $_.FullName.Substring($source.Length + 1)
    $destination = Join-Path $target $relative
    $destinationDir = Split-Path -Parent $destination
    if (-not (Test-Path $destinationDir)) {
        Write-Host "跳过（平台未生成）: $relative"
        return
    }
    Copy-Item -Path $_.FullName -Destination $destination -Force
    Write-Host "已应用: $relative"
    $copied++
}

Write-Host "共应用 $copied 个原生配置文件"
