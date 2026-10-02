"""构建时把版本号写进 App。

版本来源优先级：推送的 tag → 提交信息里的 vX.Y.Z（可带 fix 后缀）→ pubspec 现有版本。

    python tool/apply_version.py [tag] --build=123

会写两处：
* app/lib/version.dart  —— App 界面上显示的版本（可以把 fix 带上）
* app/pubspec.yaml      —— 只能放合法 semver，fix 用 build number 表达
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "app" / "lib" / "version.dart"
PUBSPEC = ROOT / "app" / "pubspec.yaml"

#: v1 / v1.2 / v1.2.3，后面可以跟 fix（允许空格、-、_、. 分隔）
VERSION_RE = re.compile(r"[vV]?(\d+)\.(\d+)(?:\.(\d+))?(?:[\s\-_.]*([fF][iI][xX]))?")


def parse_version(text: str) -> tuple[str, str] | None:
    """返回 (semver, 显示用标签)，解析不出来返回 None。"""
    match = VERSION_RE.search(text or "")
    if not match:
        return None
    major, minor, patch, fix = match.group(1), match.group(2), match.group(3) or "0", match.group(4)
    semver = f"{major}.{minor}.{patch}"
    return semver, semver + ("fix" if fix else "")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    build = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--build=")), "1")
    build = re.sub(r"\D", "", build) or "1"

    candidates = [
        args[0] if args else "",
        os.getenv("GITHUB_REF_NAME", ""),
        os.getenv("COMMIT_MESSAGE", ""),
    ]
    parsed = next((p for p in (parse_version(c) for c in candidates) if p), None)
    if parsed is None:
        # 退回 pubspec 里现有的版本
        current = re.search(r"^version:\s*([0-9.]+)", PUBSPEC.read_text(encoding="utf-8"), re.M)
        parsed = (current.group(1) if current else "1.0.0", current.group(1) if current else "1.0.0")
    semver, label = parsed

    VERSION_FILE.write_text(
        "// 由 tool/apply_version.py 在构建时生成，不要手改。\n"
        f"const String kAppVersion = 'v{label}';\n"
        f"const String kAppBuild = '{build}';\n",
        encoding="utf-8",
    )

    pubspec = PUBSPEC.read_text(encoding="utf-8")
    pubspec = re.sub(r"^version:.*$", f"version: {semver}+{build}", pubspec, count=1, flags=re.M)
    PUBSPEC.write_text(pubspec, encoding="utf-8")

    print(f"版本: v{label}  (pubspec {semver}+{build})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
