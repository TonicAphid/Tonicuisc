#!/usr/bin/env bash
# 把 tool/platform_patches 下的原生配置覆盖到已生成的平台目录。
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_dir="$root/tool/platform_patches"
target_dir="$root/app"

[ -d "$target_dir" ] || { echo "找不到 app 目录: $target_dir" >&2; exit 1; }

copied=0
while IFS= read -r -d '' file; do
  relative="${file#"$source_dir"/}"
  destination="$target_dir/$relative"
  destination_dir="$(dirname "$destination")"
  if [ ! -d "$destination_dir" ]; then
    echo "跳过（平台未生成）: $relative"
    continue
  fi
  cp "$file" "$destination"
  echo "已应用: $relative"
  copied=$((copied + 1))
done < <(find "$source_dir" -type f -print0)

echo "共应用 $copied 个原生配置文件"
