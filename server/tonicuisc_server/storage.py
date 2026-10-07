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

import hashlib
import json
import threading
import time
import uuid
from typing import TYPE_CHECKING, Any

from .config import SETTINGS

if TYPE_CHECKING:  # 只是给读代码/类型检查看的，运行时不需要装 redis
    import redis

#: 音源直链的保守有效期（秒）：超过就重新搜一次刷新
URL_TTL_SECONDS = 30 * 60

#: 搜索历史最多保留多少条
MAX_SEARCH_HISTORY = 500

#: 所有 key 的前缀
NAMESPACE = "tonicuisc"

#: 收藏 / 喜欢 / 播放历史
LIBRARY_KINDS = ("like", "favorite", "history")


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


class Storage:
    """Redis 后端。

    ``client`` 不传就自动连（必要时拉起）托管 Redis；测试里直接塞一个 fakeredis 客户端。
    """

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

    # ----------------------------------------------------------------- songs
    def upsert_song(self, item: dict[str, Any], song: Any, url_ttl: float = URL_TTL_SECONDS) -> None:
        now = time.time()
        key = self._song_key(item["id"])
        mapping = {
            "id": _text(item.get("id")),
            "source": _text(item.get("source")),
            "source_label": _text(item.get("source_label")),
            "root_source": _text(item.get("root_source")),
            "name": _text(item.get("name")),
            "singers": _text(item.get("singers")),
            "album": _text(item.get("album")),
            "ext": _text(item.get("ext")),
            "duration": _text(item.get("duration")),
            "duration_s": _text(item.get("duration_s")),
            "file_size": _text(item.get("file_size")),
            "cover_url": _text(item.get("cover_url")),
            "has_lyric": "1" if item.get("has_lyric") else "0",
            "payload": json.dumps(song_payload(song), ensure_ascii=False),
            "url_expire_at": repr(float(now + url_ttl)),
            "last_seen": repr(now),
        }
        pipe = self.client.pipeline(transaction=False)
        pipe.hsetnx(key, "first_seen", repr(now))  # 第一次见到的时间不覆盖
        pipe.hset(key, mapping=mapping)
        pipe.zadd(self._songs_key, {str(item["id"]): now})  # 按 last_seen 排序，方便裁剪
        pipe.execute()

    def load_song(self, item_id: str) -> dict[str, Any] | None:
        """返回重建播放对象所需的 payload（不含 musicdl 依赖）。"""
        data = self.client.hgetall(self._song_key(item_id))
        if not data:
            return None
        try:
            payload = json.loads(data.get("payload") or "")
        except (TypeError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        payload.setdefault("identifier", item_id.split(":", 1)[-1])
        payload.setdefault("source", data.get("source"))
        return payload

    def song_meta(self, item_id: str) -> dict[str, Any] | None:
        return self.songs_meta([item_id]).get(item_id)

    def songs_meta(self, item_ids: list[str]) -> dict[str, dict[str, Any]]:
        """批量取歌曲元信息（``/api/covers`` 一次问 200 个 id，别一首一首查）。"""
        if not item_ids:
            return {}
        pipe = self.client.pipeline(transaction=False)
        for item_id in item_ids:
            pipe.hgetall(self._song_key(item_id))
            pipe.get(self._cover_key(item_id))
        values = pipe.execute()
        out: dict[str, dict[str, Any]] = {}
        for index, item_id in enumerate(item_ids):
            data, qq_cover = values[2 * index], values[2 * index + 1]
            if not data:
                continue
            data["has_lyric"] = _integer(data.get("has_lyric")) or 0
            data["duration_s"] = _integer(data.get("duration_s"))
            for field in ("url_expire_at", "first_seen", "last_seen"):
                data[field] = _number(data.get(field)) or 0.0
            data["qq_cover"] = qq_cover
            out[item_id] = data
        return out

    def qq_cover_of(self, item_id: str) -> str | None:
        """已缓存的 QQ 封面：None = 还没查过，'' = 查过但没有。"""
        return self.client.get(self._cover_key(item_id))

    def qq_covers_for(self, item_ids: list[str]) -> dict[str, str | None]:
        """批量查封面缓存（列表用，别一首一首查）。"""
        if not item_ids:
            return {}
        pipe = self.client.pipeline(transaction=False)
        for item_id in item_ids:
            pipe.get(self._cover_key(item_id))
        values = pipe.execute()
        return dict(zip(item_ids, values))

    def known_song(self, item_id: str) -> bool:
        """歌曲元信息还在不在（收藏/喜欢列表按它取显示用的字段）。"""
        return bool(self.client.exists(self._song_key(item_id)))

    def set_qq_cover(self, item_id: str, url: str | None) -> None:
        """写封面缓存；url 为 None 表示查过但没找到（存空串）。"""
        self.client.set(self._cover_key(item_id), _text(url))

    def covers_to_lookup(self, item_ids: list[str]) -> list[str]:
        """哪些歌还没查过 QQ 封面（库里没有的歌不算）。"""
        if not item_ids:
            return []
        pipe = self.client.pipeline(transaction=False)
        for item_id in item_ids:
            pipe.exists(self._song_key(item_id))
            pipe.get(self._cover_key(item_id))
        values = pipe.execute()
        pending: list[str] = []
        for index, item_id in enumerate(item_ids):
            known, cover = values[2 * index], values[2 * index + 1]
            if known and cover is None:
                pending.append(item_id)
        return pending

    def url_expired(self, item_id: str) -> bool:
        expire_at = _number(self.client.hget(self._song_key(item_id), "url_expire_at"))
        return expire_at is None or expire_at < time.time()

    def count_songs(self) -> int:
        return int(self.client.zcard(self._songs_key))

    def prune_songs(self, keep: int = 5000) -> int:
        """只保留最近 ``keep`` 首歌；被收藏/喜欢/历史引用的一律留着。"""
        if int(self.client.zcard(self._songs_key)) <= keep:
            return 0
        stale = self.client.zrevrange(self._songs_key, keep, -1)
        if not stale:
            return 0
        referenced = self._referenced_song_ids()
        if referenced is None:
            return 0  # 引用清单没读全就别删：宁可多留，也不能把收藏里的歌裁没了
        victims = [song_id for song_id in stale if song_id not in referenced]
        if not victims:
            return 0
        pipe = self.client.pipeline(transaction=False)
        for song_id in victims:
            pipe.delete(self._song_key(song_id), self._cover_key(song_id))
            pipe.zrem(self._songs_key, song_id)
        pipe.execute()
        return len(victims)

    def _referenced_song_ids(self) -> set[str] | None:
        """所有用户收藏 / 喜欢 / 历史里出现过的歌曲 id。

        读不全就返回 ``None``（调用方跳过这轮裁剪）：返回空集合会被当成
        「没人引用」，Redis 抖一下就把收藏里的歌删光了。
        """
        referenced: set[str] = set()
        try:
            keys = list(self.client.scan_iter(match=self._k("lib", "*"), count=200))
        except Exception:
            return None
        for key in keys:
            try:
                referenced.update(self.client.zrange(key, 0, -1))
            except Exception:
                return None
        return referenced

    # ---------------------------------------------------------------- history
    def record_search(self, keyword: str, sources: list[str], song_ids: list[str]) -> None:
        now = time.time()
        seq = int(self.client.incr(self._search_seq_key))
        search_key = self._search_key(seq)
        pipe = self.client.pipeline(transaction=False)
        pipe.hset(
            search_key,
            mapping={
                "keyword": _text(keyword),
                "sources": ",".join(sources),
                "result_count": str(len(song_ids)),
                "created_at": repr(now),
            },
        )
        if song_ids:
            pipe.rpush(f"{search_key}:songs", *[_text(song_id) for song_id in song_ids])
        pipe.lpush(self._recent_searches_key, str(seq))
        pipe.execute()
        self._trim_searches()
        # 歌曲表也跟着封顶，避免无限增长
        self.prune_songs()

    def _trim_searches(self) -> None:
        dropped = self.client.lrange(self._recent_searches_key, MAX_SEARCH_HISTORY, -1)
        if not dropped:
            return
        pipe = self.client.pipeline(transaction=False)
        for seq in dropped:
            pipe.delete(self._search_key(seq), f"{self._search_key(seq)}:songs")
        pipe.ltrim(self._recent_searches_key, 0, MAX_SEARCH_HISTORY - 1)
        pipe.execute()

    def recent_searches(self, limit: int = 20) -> list[dict[str, Any]]:
        seqs = self.client.lrange(self._recent_searches_key, 0, MAX_SEARCH_HISTORY - 1)
        if not seqs:
            return []
        pipe = self.client.pipeline(transaction=False)
        for seq in seqs:
            pipe.hgetall(self._search_key(seq))
        rows = pipe.execute()

        order: list[str] = []
        agg: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not row:
                continue
            keyword = _text(row.get("keyword"))
            if keyword not in agg:
                order.append(keyword)
                agg[keyword] = {
                    "keyword": keyword,
                    "sources": _text(row.get("sources")),
                    "times": 0,
                    "last_searched_at": 0.0,
                    "result_count": 0,
                }
            entry = agg[keyword]
            entry["times"] += 1
            entry["last_searched_at"] = max(entry["last_searched_at"], _number(row.get("created_at")) or 0.0)
            entry["result_count"] = max(entry["result_count"], _integer(row.get("result_count")) or 0)
        return [agg[keyword] for keyword in order[:limit]]

    def search_songs(self, search_id: int) -> list[str]:
        return self.client.lrange(f"{self._search_key(search_id)}:songs", 0, -1)

    def recent_song_ids(self, limit: int = 20) -> list[str]:
        """最近搜索到的歌曲 id（最近的排在前面，按 id 去重）。"""
        seqs = self.client.lrange(self._recent_searches_key, 0, MAX_SEARCH_HISTORY - 1)
        found: list[str] = []
        seen: set[str] = set()
        for seq in seqs:
            for song_id in self.client.lrange(f"{self._search_key(seq)}:songs", 0, -1):
                if song_id in seen:
                    continue
                seen.add(song_id)
                found.append(song_id)
                if len(found) >= limit:
                    return found
        return found

    # ---------------------------------------------------------------- devices
    def create_device(
        self,
        name: str,
        key_hash: str,
        device_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """只存 key 的哈希，明文 key 不落库。"""
        device_id = device_id or uuid.uuid4().hex
        now = time.time()
        pipe = self.client.pipeline(transaction=False)
        pipe.hset(
            self._k("device", device_id),
            mapping={
                "id": device_id,
                "name": _text(name),
                "key_hash": key_hash,
                "created_at": repr(now),
                "last_seen": repr(now),
                "revoked": "0",
                "user_id": _text(user_id),
            },
        )
        pipe.set(self._k("devicehash", key_hash), device_id)
        pipe.zadd(self._devices_key, {device_id: now})
        pipe.execute()
        return {"id": device_id, "name": name, "created_at": now, "last_seen": now, "revoked": 0, "user_id": user_id}

    def _device_row(self, data: dict[str, Any]) -> dict[str, Any]:
        row = dict(data)
        row["revoked"] = _integer(row.get("revoked")) or 0
        row["created_at"] = _number(row.get("created_at")) or 0.0
        row["last_seen"] = _number(row.get("last_seen")) or 0.0
        row["user_id"] = row.get("user_id") or None
        return row

    def _device(self, device_id: str) -> dict[str, Any] | None:
        data = self.client.hgetall(self._k("device", device_id))
        return None if not data else self._device_row(data)

    def _username_of(self, user_id: str | None) -> str | None:
        if not user_id:
            return None
        return self.client.hget(self._k("user", user_id), "username") or None

    def find_device_by_hash(self, key_hash: str) -> dict[str, Any] | None:
        device_id = self.client.get(self._k("devicehash", key_hash))
        if not device_id:
            return None
        row = self._device(device_id)
        if row is None:
            return None
        row["username"] = self._username_of(row.get("user_id"))
        return row

    def touch_device(self, device_id: str) -> None:
        self.client.hset(self._k("device", device_id), "last_seen", repr(time.time()))

    def list_devices(self, user_id: str | None = None, *, own_only: bool = True) -> list[dict[str, Any]]:
        """设备列表。

        ``own_only`` 时只给「属于这个账户的 + 没有账户的孤儿设备」——否则任何一台
        配对成功的设备都能看到全站账户名单（``username`` 也在里面）。
        ``own_only=False`` 只在鉴权关闭（没有账户概念）时用。
        """
        device_ids = self.client.zrange(self._devices_key, 0, -1)
        if not device_ids:
            return []
        pipe = self.client.pipeline(transaction=False)
        for device_id in device_ids:
            pipe.hgetall(self._k("device", device_id))
        rows = pipe.execute()
        items: list[dict[str, Any]] = []
        for row in rows:
            if not row:
                continue
            device = self._device_row(row)
            owner = device.get("user_id")
            if own_only and owner and owner != user_id:
                continue
            device.pop("key_hash", None)  # 哈希本身别发出去
            device["username"] = self._username_of(owner)
            items.append(device)
        return items

    def revoke_device(self, device_id: str) -> bool:
        if not self.client.exists(self._k("device", device_id)):
            return False
        self.client.hset(self._k("device", device_id), "revoked", "1")
        return True

    def delete_device(self, device_id: str, user_id: str | None = None, *, own_only: bool = True) -> bool:
        """直接从库里删掉这台设备，它的 key 立刻失效（查不到哈希了）。

        ``own_only`` 时只允许删本账户（或无主）的设备，防止跨账户吊销。
        """
        row = self._device(device_id)
        if row is None:
            return False
        owner = row.get("user_id")
        if own_only and owner and owner != user_id:
            return False
        pipe = self.client.pipeline(transaction=False)
        pipe.delete(self._k("device", device_id))
        pipe.zrem(self._devices_key, device_id)
        if row.get("key_hash"):
            pipe.delete(self._k("devicehash", row["key_hash"]))
        pipe.execute()
        return True

    def delete_devices(self, *, without_user: bool = False, revoked: bool = False) -> int:
        """清理设备记录（默认什么都不删）。

        - ``without_user``：删掉没有关联账户的（旧版配对流程、测试残留）
        - ``revoked``：删掉已吊销的
        """
        if not without_user and not revoked:
            return 0
        victims: list[str] = []
        for device_id in self.client.zrange(self._devices_key, 0, -1):
            row = self._device(device_id)
            if row is None:
                victims.append(device_id)  # 悬空记录，顺手清掉
                continue
            if without_user and (not row.get("user_id") or not self.client.exists(self._k("user", row["user_id"]))):
                victims.append(device_id)
            elif revoked and row.get("revoked"):
                victims.append(device_id)
        for device_id in victims:
            self.delete_device(device_id, own_only=False)  # 清理是全库的，不做归属过滤
        return len(victims)

    # ------------------------------------------------------------------ users
    def create_user(self, username: str, password_hash: str, user_id: str | None = None) -> dict[str, Any] | None:
        """用户名已存在时返回 None（大小写不敏感）。"""
        user_id = user_id or uuid.uuid4().hex
        now = time.time()
        if not self.client.set(self._k("username", username.lower()), user_id, nx=True):
            return None
        pipe = self.client.pipeline(transaction=False)
        pipe.hset(
            self._k("user", user_id),
            mapping={
                "id": user_id,
                "username": _text(username),
                "password_hash": password_hash,
                "created_at": repr(now),
                "last_login_at": "",
            },
        )
        pipe.zadd(self._users_key, {user_id: now})
        pipe.execute()
        return {"id": user_id, "username": username, "created_at": now}

    def _user(self, user_id: str) -> dict[str, Any] | None:
        data = self.client.hgetall(self._k("user", user_id))
        if not data:
            return None
        data["created_at"] = _number(data.get("created_at")) or 0.0
        data["last_login_at"] = _number(data.get("last_login_at"))
        return data

    def find_user(self, username: str) -> dict[str, Any] | None:
        user_id = self.client.get(self._k("username", (username or "").strip().lower()))
        return None if not user_id else self._user(user_id)

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        return self._user(user_id)

    def list_users(self) -> list[dict[str, Any]]:
        """给登录页用；不带密码哈希。"""
        user_ids = self.client.zrange(self._users_key, 0, -1)
        if not user_ids:
            return []
        pipe = self.client.pipeline(transaction=False)
        for user_id in user_ids:
            pipe.hgetall(self._k("user", user_id))
        rows = pipe.execute()
        items: list[dict[str, Any]] = []
        for row in rows:
            if not row:
                continue
            row.pop("password_hash", None)
            row["created_at"] = _number(row.get("created_at")) or 0.0
            row["last_login_at"] = _number(row.get("last_login_at"))
            items.append(row)
        return sorted(items, key=lambda item: item["created_at"])

    def touch_user_login(self, user_id: str) -> None:
        self.client.hset(self._k("user", user_id), "last_login_at", repr(time.time()))

    # --------------------------------------------------------- device requests
    def create_device_request(
        self,
        user_code: str,
        poll_hash: str,
        device_name: str,
        ttl: float,
    ) -> dict[str, Any] | None:
        """user_code 已被占用（且未过期）时返回 None，让 App 换一个码。"""
        now = time.time()
        seconds = max(int(ttl), 1)
        # 占位 key 和请求本身同寿：过期后码就能被重新用
        if not self.client.set(self._k("dreq", user_code, "lock"), "1", nx=True, ex=seconds):
            return None
        key = self._k("dreq", user_code)
        pipe = self.client.pipeline(transaction=False)
        pipe.hset(
            key,
            mapping={
                "user_code": user_code,
                "poll_hash": poll_hash,
                "device_name": _text(device_name),
                "status": "pending",
                "created_at": repr(now),
                "expires_at": repr(now + ttl),
                "user_id": "",
                "api_key": "",
                "device_id": "",
                "claimed_at": "",
            },
        )
        pipe.expire(key, seconds)
        pipe.zadd(self._dreqs_key, {user_code: now})
        pipe.execute()
        return {"user_code": user_code, "device_name": device_name, "expires_at": now + ttl}

    def get_device_request(self, user_code: str) -> dict[str, Any] | None:
        data = self.client.hgetall(self._k("dreq", user_code))
        if not data:
            return None
        data["created_at"] = _number(data.get("created_at")) or 0.0
        data["expires_at"] = _number(data.get("expires_at")) or 0.0
        data["claimed_at"] = _number(data.get("claimed_at"))
        for field in ("user_id", "api_key", "device_id"):
            data[field] = data.get(field) or None
        return data

    def approve_device_request(
        self,
        user_code: str,
        user_id: str,
        api_key: str,
        device_id: str,
    ) -> bool:
        """批准：把明文 key 暂存在请求里，等 App 取走后清掉。"""
        request = self.get_device_request(user_code)
        if request is None or request["status"] != "pending" or request["expires_at"] <= time.time():
            return False
        self.client.hset(
            self._k("dreq", user_code),
            mapping={"status": "approved", "user_id": user_id, "api_key": api_key, "device_id": device_id},
        )
        return True

    def deny_device_request(self, user_code: str) -> bool:
        request = self.get_device_request(user_code)
        if request is None or request["status"] != "pending":
            return False
        self.client.hset(self._k("dreq", user_code), "status", "denied")
        return True

    def clear_device_request_key(self, user_code: str) -> None:
        self.client.hset(
            self._k("dreq", user_code),
            mapping={"api_key": "", "claimed_at": repr(time.time())},
        )

    def drop_expired_device_requests(self) -> int:
        """请求本身靠 Redis 的 TTL 过期，这里只清理索引里的残留。"""
        codes = self.client.zrange(self._dreqs_key, 0, -1)
        gone = [code for code in codes if not self.client.exists(self._k("dreq", code))]
        if gone:
            self.client.zrem(self._dreqs_key, *gone)
        return len(gone)

    # ---------------------------------------------------------------- library
    def add_to_library(self, user_id: str, kind: str, song_id: str) -> None:
        """喜欢/收藏是幂等的；历史会累加播放次数并刷新时间。

        读-改-写要整段上锁：并发播放同一首歌时，两边都读到旧的 ``play_count``
        会让计数少加一次。
        """
        now = time.time()
        key = self._lib_key(user_id, kind)
        meta_key = self._lib_meta_key(user_id, kind)
        with self._lib_lock:
            info = _json_dict(self.client.hget(meta_key, song_id))
            if kind == "history":
                created = _number(info.get("created_at")) or now
                play_count = (_integer(info.get("play_count")) or 0) + 1
            else:
                if info:
                    return  # 已经喜欢/收藏过了，时间不动
                created, play_count = now, 1
            pipe = self.client.pipeline(transaction=False)
            pipe.zadd(key, {song_id: now})
            pipe.hset(meta_key, song_id, json.dumps({"created_at": created, "play_count": play_count}))
            pipe.execute()

    def remove_from_library(self, user_id: str, kind: str, song_id: str) -> bool:
        pipe = self.client.pipeline(transaction=False)
        pipe.zrem(self._lib_key(user_id, kind), song_id)
        pipe.hdel(self._lib_meta_key(user_id, kind), song_id)
        removed, _ = pipe.execute()
        return bool(removed)

    def clear_library(self, user_id: str, kind: str) -> int:
        key = self._lib_key(user_id, kind)
        removed = int(self.client.zcard(key))
        pipe = self.client.pipeline(transaction=False)
        pipe.delete(key)
        pipe.delete(self._lib_meta_key(user_id, kind))
        pipe.execute()
        return removed

    def library_items(self, user_id: str, kind: str, limit: int = 200) -> list[dict[str, Any]]:
        entries = self.client.zrevrange(self._lib_key(user_id, kind), 0, max(limit, 1) - 1, withscores=True)
        if not entries:
            return []
        pipe = self.client.pipeline(transaction=False)
        for song_id, _ in entries:
            pipe.hgetall(self._song_key(song_id))
            pipe.hget(self._lib_meta_key(user_id, kind), song_id)
            pipe.get(self._cover_key(song_id))
        values = pipe.execute()

        items: list[dict[str, Any]] = []
        for index, (song_id, updated_at) in enumerate(entries):
            song = values[3 * index]
            if not song:
                continue  # 歌被裁掉了，条目留着但不显示
            meta = _json_dict(values[3 * index + 1])
            qq_cover = values[3 * index + 2]
            cover_url = _text(song.get("cover_url"))
            if SETTINGS.qq_cover and qq_cover:
                cover_url = qq_cover
            items.append(
                {
                    "id": song_id,
                    "source": _text(song.get("source")),
                    "source_label": _text(song.get("source_label")),
                    "name": _text(song.get("name")),
                    "singers": _text(song.get("singers")),
                    "album": _text(song.get("album")),
                    "ext": _text(song.get("ext")),
                    "duration": _text(song.get("duration")),
                    "duration_s": _integer(song.get("duration_s")),
                    "file_size": _text(song.get("file_size")),
                    "cover_url": cover_url,
                    "has_lyric": _integer(song.get("has_lyric")) or 0,
                    "created_at": _number(meta.get("created_at")) or updated_at,
                    "updated_at": updated_at,
                    "play_count": _integer(meta.get("play_count")) or 1,
                }
            )
        return items

    def library_ids(self, user_id: str, kind: str, limit: int = 500) -> list[str]:
        return self.client.zrevrange(self._lib_key(user_id, kind), 0, max(limit, 1) - 1)

    def library_counts(self, user_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for kind in LIBRARY_KINDS:
            counts[kind] = int(self.client.zcard(self._lib_key(user_id, kind)))
        return counts

    # ----------------------------------------------------------- 搜索结果缓存
    def _cache_key(self, key: tuple[str, tuple[str, ...]]) -> str:
        keyword, sources = key
        digest = hashlib.sha1(f"{keyword}|{','.join(sources)}".encode("utf-8")).hexdigest()
        return self._k("sc", digest)

    def save_search_cache(self, key: tuple[str, tuple[str, ...]], items: list[dict[str, Any]]) -> None:
        """把一次搜索的结果存进 Redis（默认 24 小时），重启后也能直接命中。

        ``TONICUISC_SEARCH_TTL <= 0`` 表示**不缓存**：直接把旧的踹掉，
        否则「不带过期」的键会被当成永远新鲜，``refresh=true`` 就再也搜不动了。
        """
        ttl = int(SETTINGS.search_ttl or 0)
        if ttl <= 0:
            self.client.delete(self._cache_key(key))
            return
        payload = json.dumps(items, ensure_ascii=False)
        self.client.set(self._cache_key(key), payload, ex=ttl)

    def load_search_cache(self, key: tuple[str, tuple[str, ...]]) -> list[dict[str, Any]] | None:
        raw = self.client.get(self._cache_key(key))
        if not raw:
            return None
        try:
            items = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(items, list):
            return None
        return [dict(item) for item in items if isinstance(item, dict)]

    def search_cache_age(self, key: tuple[str, tuple[str, ...]]) -> float | None:
        """这条搜索结果缓存存了多久（秒）；不存在返回 None。

        靠 Redis 的 TTL 反推年龄，不用额外存时间戳。
        """
        ttl = int(self.client.ttl(self._cache_key(key)))
        if ttl == -2:
            return None  # 没有这条缓存
        if ttl == -1:
            # 键在、但没设过期时间（旧版本写过 / TTL 配成过 0）：算不出年龄，
            # 当作「不知道多新」，让调用方按过期处理去重搜
            return None
        return max(int(SETTINGS.search_ttl) - ttl, 0)

    def drop_search_cache(self) -> int:
        """清掉所有搜索结果缓存（改音源配置后用得上）。"""
        removed = 0
        for redis_key in self.client.scan_iter(match=self._k("sc", "*"), count=200):
            removed += int(self.client.delete(redis_key))
        return removed
