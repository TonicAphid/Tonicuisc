"""Redis 连接的持有者：命名空间、key 拼装、连接与自描述。

领域方法按用途拆在 ``songs`` / ``history`` / ``users`` / ``devices`` /
``device_requests`` / ``library`` / ``search_cache`` 里，由 :class:`~tonicuisc_server.storage.Storage` 组合。"""

from __future__ import annotations

import json
import threading
import time
from typing import TYPE_CHECKING, Any

#: 音源直链的保守有效期（秒）：超过就重新搜一次刷新
URL_TTL_SECONDS = 30 * 60

#: 搜索历史最多保留多少条
MAX_SEARCH_HISTORY = 500

#: 所有 key 的前缀
NAMESPACE = "tonicuisc"

#: 收藏 / 喜欢 / 播放历史
LIBRARY_KINDS = ("like", "favorite", "history")

if TYPE_CHECKING:  # 只是给读代码/类型检查看的，运行时不需要装 redis
    import redis


def song_payload(song: Any) -> dict[str, Any]:
    """把 musicdl 的 SongInfo 变成可 JSON 序列化的 dict。"""
    if hasattr(song, "todict"):
        data = dict(song.todict())
    else:
        data = dict(getattr(song, "__dict__", {}))
    data.pop("episodes", None)  # FM 电台的子集，不入库
    data.pop("downloaded_contents", None)  # 可能是 bytes
    return json.loads(json.dumps(data, ensure_ascii=False, default=str))


def _text(value: Any) -> str:
    """Redis 里只存字符串；None / 空值统一成空串。"""
    return "" if value is None else str(value)


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return None if number is None else int(number)


def _json_dict(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}

class StorageBase:
    def __init__(self, client: Any | None = None, namespace: str = NAMESPACE) -> None:
        if client is None:
            from .redis_runtime import ensure_redis_client

            client = ensure_redis_client()
        self.client: Any = client
        self.ns = namespace
        #: 收藏列表的「读-改-写」用（Redis 不是事务，得自己串起来）
        self._lib_lock = threading.Lock()

    def close(self) -> None:
        """断开连接池。

        Redis **服务端**由 ``redis_runtime`` 统一管理（谁拉起来的谁关），
        这里只归还本对象持有的连接，绝不去关别人的 Redis。
        """
        try:
            self.client.close()
        except Exception:
            pass

    def describe(self) -> str:
        """给 ``/api/health`` 用的一行说明。"""
        try:
            kwargs = dict(self.client.connection_pool.connection_kwargs)
            host = kwargs.get("host") or "127.0.0.1"
            port = kwargs.get("port") or 6379
            db = kwargs.get("db") or 0
            return f"redis://{host}:{port}/{db}"
        except Exception:
            return "redis"

    # ------------------------------------------------------------------ keys
    def _k(self, *parts: Any) -> str:
        return ":".join((self.ns, *(str(part) for part in parts)))

    def key(self, *parts: Any) -> str:
        """拼一个带命名空间的 Redis key（``scripts/import_sqlite.py`` 迁移老数据时用）。"""
        return self._k(*parts)

    def _song_key(self, item_id: str) -> str:
        return self._k("song", item_id)

    def _cover_key(self, item_id: str) -> str:
        return self._k("qcover", item_id)

    @property
    def _songs_key(self) -> str:
        return self._k("songs")

    @property
    def _devices_key(self) -> str:
        return self._k("devices")

    @property
    def _dreqs_key(self) -> str:
        return self._k("dreqs")

    @property
    def _users_key(self) -> str:
        return self._k("users")

    @property
    def _recent_searches_key(self) -> str:
        return self._k("searchrecent")

    @property
    def _search_seq_key(self) -> str:
        return self._k("searchseq")

    def _search_key(self, seq: Any) -> str:
        return self._k("search", seq)

    def _lib_key(self, user_id: str, kind: str) -> str:
        return self._k("lib", user_id, kind)

    def _lib_meta_key(self, user_id: str, kind: str) -> str:
        return self._k("libmeta", user_id, kind)

