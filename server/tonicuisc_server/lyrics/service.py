"""增强歌词服务：缓存、后台抓取、降级。

一次请求的语义和封面一样，是「有结论才返回」：

* ``ready: true``  —— 结构化逐字歌词；
* ``ready: false`` —— 还没查完 / 没找到，客户端稍后再问。

抓取跑在独立线程池里，不阻塞播放页的请求；同一首歌同时只跑一个任务。
"""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from ..core.logging import timing_log
from ..storage.lyrics import LYRIC_MISS_TTL_SECONDS, LYRIC_TTL_SECONDS
from .models import EnhancedLyrics
from .providers import KugouProvider, LyricsProvider

NOT_READY: dict[str, Any] = {"format": "enhanced", "ready": False}


class LyricService:
    def __init__(
        self,
        storage: Any,
        providers: list[LyricsProvider] | None = None,
        *,
        max_workers: int = 2,
    ) -> None:
        self.storage = storage
        self.providers: list[LyricsProvider] = providers or [KugouProvider()]
        self._pool = ThreadPoolExecutor(max_workers=max(max_workers, 1), thread_name_prefix="lyric")
        self._inflight: dict[str, Future[Any]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ 对外
    def enhanced(self, item_id: str, song: Any) -> dict[str, Any]:
        """取增强歌词；没有就排后台任务并返回 ``ready: false``。"""
        cached = self.storage.load_lyrics(item_id)
        if cached is not None:
            return NOT_READY if cached.get("empty") else cached
        self._schedule(item_id, song)
        return dict(NOT_READY)

    def prepare(self, item_id: str, song: Any) -> dict[str, Any]:
        """同步抓一次（测试 / 预热用），成功返回结构化结果。"""
        payload = self._fetch(item_id, song)
        return payload or dict(NOT_READY)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def inflight(self) -> int:
        with self._lock:
            return sum(1 for future in self._inflight.values() if not future.done())

    # ------------------------------------------------------------------ 内部
    def _schedule(self, item_id: str, song: Any) -> None:
        with self._lock:
            running = self._inflight.get(item_id)
            if running is not None and not running.done():
                return
            future = self._pool.submit(self._fetch, item_id, song)
            self._inflight[item_id] = future
        future.add_done_callback(lambda _: self._forget(item_id))

    def _forget(self, item_id: str) -> None:
        with self._lock:
            self._inflight.pop(item_id, None)

    def _fetch(self, item_id: str, song: Any) -> dict[str, Any] | None:
        for provider in self.providers:
            try:
                lyrics: EnhancedLyrics | None = provider.fetch(song)
            except Exception as exc:  # 单个源挂掉不能影响下一个源
                timing_log(f"[lyric] {provider.name} 失败（换下一个源）: {type(exc).__name__}: {exc}")
                continue
            if lyrics is None or not lyrics.lines:
                continue
            payload = lyrics.to_dict()
            try:
                self.storage.save_lyrics(item_id, payload, LYRIC_TTL_SECONDS)
            except Exception as exc:  # 缓存写失败不影响这次返回
                timing_log(f"[lyric] 写缓存失败（忽略）: {exc}")
            return payload
        try:
            self.storage.save_lyrics(item_id, {"empty": True}, LYRIC_MISS_TTL_SECONDS)
        except Exception:
            pass
        return None
