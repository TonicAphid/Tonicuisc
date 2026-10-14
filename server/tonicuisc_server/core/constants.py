"""跨模块共用的常量（纯数据，不 import 任何东西，避免环）。"""

from __future__ import annotations

#: 搜索结果在内存里保持可用的时间（/api/stream/{id} 靠它）
ITEM_TTL_SECONDS = 3 * 60 * 60

#: 搜索残留目录（只有 *.pkl）至少闲置这么久才清理，避免打断正在进行的下载
SEARCH_RESIDUE_MAX_AGE = 600

#: 这些后缀算音频，清理目录时一律不动
AUDIO_SUFFIXES = {".mp3", ".flac", ".m4a", ".aac", ".wav", ".ogg", ".ape", ".wma", ".wv", ".tta"}

#: 兜底请求用的浏览器 UA
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

#: 设备 ``last_seen`` 最多多久写一次 Redis（秒）。
#: 每个带 key 的请求都写一次的话，翻一屏列表就是几十次写；60 秒足够了。
TOUCH_INTERVAL_SECONDS = 60

#: 线程池大小：``asyncio.to_thread`` 默认只有 ``min(32, cpu+4)``，
#: 而搜索、边下边播的读、Redis 调用全挤在这个池子里。
DEFAULT_THREAD_POOL_SIZE = 64
