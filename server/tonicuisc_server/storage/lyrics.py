"""逐字歌词缓存（Redis）。

搜索结果/歌曲元信息在别处；这里只存「已经解析好的增强歌词」和「查过但没有」的负缓存。
"""

from __future__ import annotations

import json
from typing import Any

#: 命中的增强歌词保留 30 天（歌词基本不变，重查一次很贵）
LYRIC_TTL_SECONDS = 30 * 24 * 3600

#: 没找到的负缓存 6 小时：避免每次点歌都重新打一遍歌词源
LYRIC_MISS_TTL_SECONDS = 6 * 3600

#: 缓存格式版本。**清洗 / 注音规则一改就 +1**：key 跟着变，旧缓存自然失效，
#: 不用手动清库，也不会让人继续看到按老规则处理过的歌词（署名行被吞、乱注音那种）。
LYRIC_CACHE_VERSION = 4


class LyricsCacheMixin:
    def _lyric_key(self, item_id: str) -> str:
        return self._k("lyric", f"v{LYRIC_CACHE_VERSION}", item_id)

    def save_lyrics(self, item_id: str, payload: dict[str, Any], ttl: int) -> None:
        self.client.set(self._lyric_key(item_id), json.dumps(payload, ensure_ascii=False), ex=max(int(ttl), 1))

    def load_lyrics(self, item_id: str) -> dict[str, Any] | None:
        raw = self.client.get(self._lyric_key(item_id))
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None
