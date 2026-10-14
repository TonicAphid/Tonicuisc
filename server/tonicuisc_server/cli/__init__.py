"""命令行入口。

``__main__.py`` 只有一行 ``from .cli import main``，真正的逻辑在这里：

* :mod:`~tonicuisc_server.cli.commands` —— 一次性管理命令（devices / revoke / cert …）
* :mod:`~tonicuisc_server.cli.console` —— 常驻 ``>`` 控制台
* :mod:`~tonicuisc_server.cli.launch` —— uvicorn 启动（含原生 TLS）
"""

from __future__ import annotations

import sys

from .commands import MANAGEMENT_COMMANDS, run_management_command
from .launch import run_server


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in MANAGEMENT_COMMANDS:
        raise SystemExit(run_management_command(args[0], args[1:]))
    run_server()


__all__ = ["main", "run_server", "MANAGEMENT_COMMANDS", "run_management_command"]
