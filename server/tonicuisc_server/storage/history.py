"""搜索历史（按关键词聚合）与「最近搜到的歌」。"""

from __future__ import annotations

import time
from typing import Any

from .base import MAX_SEARCH_HISTORY, _integer, _number, _text

class HistoryMixin:
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
