"""搜索结果缓存：key 由「关键词 + 音源」哈希而来，年龄靠 Redis TTL 反推。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..core.config import SETTINGS

class SearchCacheMixin:
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
