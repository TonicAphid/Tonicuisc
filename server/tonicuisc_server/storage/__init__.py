"""Redis 持久化：歌曲元信息、搜索历史、搜索结果缓存、账户 / 设备 / 收藏。

数据**全部**放在 Redis 里（服务端会自己拉起一个本地实例，见 ``redis_runtime.py``）：

* ``songs``       —— 歌曲元信息 + 音源直链（直链带一个保守的过期时间，过期就重搜刷新）
* ``searches``    —— 搜索历史（按关键词聚合）
* ``library``     —— 喜欢 / 收藏 / 播放历史（按账户）
* ``devices`` / ``users`` / ``device_requests`` —— 设备与账户
* ``sc``          —— 搜索结果缓存（默认 24 小时，**重启后依然命中**，不用重新联网搜）

key 统一带 ``tonicuisc:`` 前缀，所以和别人的 Redis 共用一个库也不会打架。
旧版本的 SQLite 数据用 ``scripts/import_sqlite.py`` 一次性导入。
"""

from __future__ import annotations

from .base import (  # noqa: F401  —— 常量与工具对外可见（脚本 / 测试在用）
    LIBRARY_KINDS,
    MAX_SEARCH_HISTORY,
    NAMESPACE,
    URL_TTL_SECONDS,
    StorageBase,
    song_payload,
)
from .device_requests import DeviceRequestMixin
from .devices import DeviceMixin
from .history import HistoryMixin
from .library import LibraryMixin
from .search_cache import SearchCacheMixin
from .songs import SongMixin
from .users import UserMixin


class Storage(
    StorageBase,
    SongMixin,
    HistoryMixin,
    UserMixin,
    DeviceMixin,
    DeviceRequestMixin,
    LibraryMixin,
    SearchCacheMixin,
):
    """Redis 后端：按领域拆开的一堆 mixin 在这里拼成一个对象。

    ``client`` 不传就自动连（必要时拉起）托管 Redis；测试里直接塞一个 fakeredis 客户端。
    """


__all__ = [
    "Storage",
    "song_payload",
    "NAMESPACE",
    "URL_TTL_SECONDS",
    "MAX_SEARCH_HISTORY",
    "LIBRARY_KINDS",
]
