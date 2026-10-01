#!/usr/bin/env bash
# 生成 app/ 下缺失的原生平台工程（windows / linux / macos / ios），然后打补丁并装依赖。
set -euo pipefail

platforms="${1:-windows,linux,macos,ios}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
app="$root/app"

cd "$app"
flutter create --project-name tonicuisc --org com.tonicuisc --platforms="$platforms" .
cd "$root"

bash "$root/tool/apply_patches.sh"

cd "$app"
flutter pub get

echo "完成：平台工程已生成，可以直接 flutter run / flutter build"
