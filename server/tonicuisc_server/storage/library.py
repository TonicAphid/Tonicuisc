"""喜欢 / 收藏 / 播放历史（按账户隔离，读-改-写自己串行）。"""

from __future__ import annotations

import json
import time
from typing import Any

from ..core.config import SETTINGS
from .base import LIBRARY_KINDS, _integer, _json_dict, _number, _text

class LibraryMixin:
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

    def library_bundle(self, user_id: str, limit: int = 500) -> tuple[dict[str, int], list[str], list[str]]:
        """``/api/library/summary`` 要的三样东西，一次往返取齐。

        以前是「3 次 counts + 2 次 ids」共 5 个命令、分 3 个来回；合成一个 pipeline
        后只有 1 个来回（本机 Redis 省的是 socket + 事件循环的往返）。
        """
        pipe = self.client.pipeline(transaction=False)
        for kind in LIBRARY_KINDS:
            pipe.zcard(self._lib_key(user_id, kind))
        pipe.zrevrange(self._lib_key(user_id, "like"), 0, max(limit, 1) - 1)
        pipe.zrevrange(self._lib_key(user_id, "favorite"), 0, max(limit, 1) - 1)
        values = pipe.execute()
        kinds = len(LIBRARY_KINDS)
        counts = {kind: int(values[index]) for index, kind in enumerate(LIBRARY_KINDS)}
        return counts, list(values[kinds]), list(values[kinds + 1])
