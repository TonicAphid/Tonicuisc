"""日志小工具。"""

from __future__ import annotations

import sys


def _print_line(prefix: str, message: str) -> None:
    """带前缀的一行日志。Windows 控制台编码扛不住中文时退化成 ASCII，别把服务搞崩。"""
    if sys.is_finalizing():
        # 解释器收尾时 stdout 已经在 finalize，这时候写一下就是
        # `_enter_buffered_busy` → Fatal Python error → abort（进程 exit 134）。
        # 后台线程（封面预热、搜索软刷新）比请求活得久，常在收尾期醒来，
        # 它们的日志必须静默丢掉——CI 上这能把一整轮全绿的测试判成失败。
        return
    for candidate in (message, message.encode("ascii", "replace").decode("ascii")):
        try:
            print(f"[{prefix}] {candidate}", flush=True)
            return
        except UnicodeEncodeError:
            continue


def timing_log(message: str) -> None:
    """`[timing]` 计时日志。"""
    _print_line("timing", message)


def download_log(message: str) -> None:
    """`[download]` 下载日志。

    musicdl 自己会用 rich 画进度条（``KuwoMusicClient.download >>> Completed ...``），
    那套输出和本项目的 ``[timing]`` / ``[access]`` 风格对不上，而且会把日志刷满。
    所以进度条一律关掉（见 ``services/sources.py`` 的 ``silence_musicdl_progress``），
    这里用一行中文日志说明「开始 / 完成 / 失败」。
    """
    _print_line("download", message)
