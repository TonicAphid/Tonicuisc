"""歌曲元信息、QQ 封面缓存、直链过期与歌曲表裁剪。"""

from __future__ import annotations

import json
import time
from typing import Any

from .base import URL_TTL_SECONDS, _integer, _number, _text, song_payload

class SongMixin:
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
