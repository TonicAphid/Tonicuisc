"""命令行入口。

``__main__.py`` 只有一行 ``from .cli import main``，真正的逻辑在这里：

* :mod:`~tonicuisc_server.cli.commands` —— 一次性管理命令（devices / revoke / cert …）
* :mod:`~tonicuisc_server.cli.console` —— 常驻 ``>`` 控制台
* :mod:`~tonicuisc_server.cli.launch` —— uvicorn 启动（含原生 TLS）

``python -m tonicuisc_server --help`` 打命令总览（**不会**启动服务）。
不带参数 = 启动服务。
"""

from __future__ import annotations

import sys

from .commands import HELP_FLAGS, MANAGEMENT_COMMANDS, print_commands, run_management_command
from .launch import run_server


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)

    # --help 要在这里拦住：以前没有这个分支，`--help` 会被当成「没给命令」
    # 直接去启动服务（顺手拉起 Redis/Caddy），非常意外。
    if args and args[0].strip().lower() in HELP_FLAGS:
        print_commands()
        return

    if args and args[0] in MANAGEMENT_COMMANDS:
        raise SystemExit(run_management_command(args[0], args[1:]))

    # 给了个不认识的命令：提示一下，别默默去起服务
    if args and not args[0].startswith("-"):
        print(f"不认识这个命令：{args[0]}")
        print()
        print_commands()
        raise SystemExit(2)

    run_server()


__all__ = ["main", "run_server", "MANAGEMENT_COMMANDS", "run_management_command", "print_commands"]
