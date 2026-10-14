"""本地自检：Dart 的 import 必须真的指到文件。

本机没装 flutter/dart 时（CI 上的 `flutter analyze` 只能等远端），重构 / 搬文件之后
最怕的就是某个 `import '../xxx.dart'` 指到了不存在的路径——那种错要等整轮 CI 才报。
这个脚本几毫秒就能查出来，顺带查 `package:tonicuisc/...` 有没有落到 `app/lib` 下。

用法（仓库根目录）：python tool/check_dart_imports.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
LIB = APP / "lib"
PACKAGE = "tonicuisc"  # pubspec.yaml 里的 name

IMPORT_RE = re.compile(r"""^\s*(?:import|export|part)\s+'([^']+)'""", re.MULTILINE)


def check_file(path: Path) -> list[str]:
    problems: list[str] = []
    for match in IMPORT_RE.finditer(path.read_text(encoding="utf-8")):
        target = match.group(1)
        if target.startswith("dart:") or target.startswith("package:$PACKAGE/"):
            if target.startswith("dart:"):
                continue
            resolved = LIB / target[len(f"package:{PACKAGE}/") :]
        elif target.startswith("package:"):
            continue  # 第三方包交给 pub / analyze
        else:
            # 相对路径（含同目录裸文件名）：Dart 两者都按「相对当前文件」解析
            resolved = (path.parent / target).resolve()
        if not resolved.exists():
            problems.append(f"{path.relative_to(ROOT)}: {target}")
    return problems


def main() -> int:
    files = sorted(LIB.rglob("*.dart")) + sorted((APP / "test").rglob("*.dart"))
    problems: list[str] = []
    for path in files:
        problems.extend(check_file(path))

    if problems:
        print(f"发现 {len(problems)} 个指不到文件的 import：")
        for line in problems:
            print(f"  {line}")
        return 1
    print(f"OK   {len(files)} 个 dart 文件的 import 都能解析")
    return 0


if __name__ == "__main__":
    sys.exit(main())
