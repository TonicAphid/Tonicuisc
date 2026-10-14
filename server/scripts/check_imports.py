"""自检：包里的每个模块都必须能被 import。

`compileall` 只查语法、不查 import —— 重构时留下的死模块就是这样漏过去的：
语法全对，但它 import 的模块早就搬走了，谁 import 谁炸（而没人 import 时又查不出来）。

用法（server 目录下）：python scripts/check_imports.py
"""

from __future__ import annotations

import importlib
import pathlib
import pkgutil
import sys

SERVER_DIR = pathlib.Path(__file__).resolve().parent.parent
if str(SERVER_DIR) not in sys.path:  # 直接 `python scripts/check_imports.py` 时要能找到包
    sys.path.insert(0, str(SERVER_DIR))

import tonicuisc_server  # noqa: E402  —— 得先把 server/ 放进 sys.path


def main() -> int:
    names = [module.name for module in pkgutil.walk_packages(tonicuisc_server.__path__, "tonicuisc_server.")]
    failed: list[str] = []
    for name in names:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 —— 这里就是要抓住所有 import 期错误
            failed.append(f"{name}: {type(exc).__name__}: {exc}")

    if failed:
        print(f"{len(failed)} 个模块 import 失败：")
        for line in failed:
            print(f"  {line}")
        return 1
    print(f"OK   {len(names)} 个模块都能 import")
    return 0


if __name__ == "__main__":
    sys.exit(main())
