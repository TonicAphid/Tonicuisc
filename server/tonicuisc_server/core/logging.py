"""日志小工具。"""

from __future__ import annotations

import sys


def timing_log(message: str) -> None:
    """`[timing]` 计时日志。Windows 控制台编码扛不住中文时退化成 ASCII，别把服务搞崩。"""
    if sys.is_finalizing():
        # 解释器收尾时 stdout 已经在 finalize，这时候写一下就是
        # `_enter_buffered_busy` → Fatal Python error → abort（进程 exit 134）。
        # 后台线程（封面预热、搜索软刷新）比请求活得久，常在收尾期醒来，
        # 它们的日志必须静默丢掉——CI 上这能把一整轮全绿的测试判成失败。
        return
    for candidate in (message, message.encode("ascii", "replace").decode("ascii")):
        try:
            print(f"[timing] {candidate}", flush=True)
            return
        except UnicodeEncodeError:
            continue
