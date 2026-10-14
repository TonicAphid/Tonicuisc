"""事件循环 / 线程池之类的运行时装配。"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

from .constants import DEFAULT_THREAD_POOL_SIZE

#: 我们建的那个线程池（退出时要自己收）
_executor: ThreadPoolExecutor | None = None


def configure_thread_pool(size: int = DEFAULT_THREAD_POOL_SIZE) -> ThreadPoolExecutor:
    """把 ``asyncio.to_thread`` 的默认线程池调大。

    默认是 ``min(32, cpu+4)``（这台机器上就是 32 封顶）——搜索、边下边播的读、
    每一次 Redis 调用都走 ``to_thread``，32 个线程在几台设备同时翻页时很容易排满，
    表现就是「明明 Redis 是本机、接口却要等」。这里给到 64，并由我们自己在退出时收掉。
    """
    global _executor
    if _executor is not None:
        return _executor
    loop = asyncio.get_running_loop()
    _executor = ThreadPoolExecutor(max_workers=max(int(size), 8), thread_name_prefix="tonicuisc")
    loop.set_default_executor(_executor)
    return _executor


def shutdown_thread_pool() -> None:
    global _executor
    if _executor is None:
        return
    _executor.shutdown(wait=False)
    _executor = None
