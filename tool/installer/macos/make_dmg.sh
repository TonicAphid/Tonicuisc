#!/usr/bin/env bash
# 把 .app 打成 DMG：里面是 App + 一个指向 /Applications 的快捷方式。
#
# 用法：bash tool/installer/macos/make_dmg.sh <Tonicuisc.app> <out.dmg> [卷名]
set -euo pipefail

app="${1:?用法: make_dmg.sh <App.app> <out.dmg> [卷名]}"
out="${2:?用法: make_dmg.sh <App.app> <out.dmg> [卷名]}"
volume="${3:-Tonicuisc}"

[ -d "$app" ] || { echo "找不到 App: $app" >&2; exit 1; }

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT

cp -R "$app" "$stage/"
ln -s /Applications "$stage/Applications"

mkdir -p "$(dirname "$out")"
rm -f "$out"
hdiutil create -volname "$volume" -srcfolder "$stage" -ov -format UDZO "$out"

echo "DMG: $out ($(du -h "$out" | cut -f1))"
