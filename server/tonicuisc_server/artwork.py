"""搜索结果里用 QQ 音乐的专辑封面（咪咕 / 酷我的封面经常糊）。

分工是这样的：

* **搜索不等封面**：QQ 一次要 3~4 秒，塞进搜索响应里会让「5 秒出列表」变成十几秒。
  搜索一返回就把这批歌丢给常驻的封面线程池，在**后台**并发查好写进 Redis。
* **命中缓存的封面直接写进搜索结果**：所以重复搜同一个词，列表第一帧就是 QQ 封面。
* **``/api/covers`` 和后台预热带的是同一批任务**（``_submit_cover`` 按歌曲 id 去重），
  所以 App 渲染完列表来问封面时，要么已经好了、要么就等同一批正在跑的请求，
  绝不会对同一首歌问两遍 QQ。
"""

from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from .config import SETTINGS
from .items import timing_log
from .qqmusic import search_cover as qq_cover

if TYPE_CHECKING:
    from .storage import Storage


class ArtworkMixin:
    """宿主需要提供：``storage``、``_cover_lock``、``_cover_jobs``、``_cover_pool``。"""

    if TYPE_CHECKING:
        storage: Storage
        _cover_lock: Any
        _cover_jobs: dict[str, Future[str | None]]
        _cover_pool: ThreadPoolExecutor | None

    # ------------------------------------------------------------------ 对外
    def prewarm_covers(self, items: list[dict[str, Any]]) -> int:
        """搜索刚返回时调用：填上已缓存的封面，剩下的丢后台并发查。

        返回「还需要问 QQ」的条数（只用于日志）。**不阻塞**。
        """
        pending = self.apply_cached_covers(items)
        for item in pending:
            self._submit_cover(item["id"], str(item.get("name") or ""), str(item.get("singers") or ""))
        return len(pending)

    def apply_cached_covers(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """把已经查过的 QQ 封面填进 item（纯本地，几毫秒），返回还没查过的那些。"""
        if not items or not SETTINGS.qq_cover:
            return []
        states = self._cover_states([item["id"] for item in items])
        pending: list[dict[str, Any]] = []
        for item in items:
            state = states.get(item["id"])
            if state:
                item["cover_url"] = state
            elif state is None:
                pending.append(item)  # None = 还没查过；'' = 查过但没有，保持音源原图
        return pending

    def covers(self, item_ids: list[str]) -> dict[str, str]:
        """按需补封面：返回每个 id 能用的最好封面（QQ 优先，其次音源原图）。

        查过的结果落 Redis（'' = 查过没有），同一首歌只真的查一次；
        正在后台预热的那批会被这里「接住」，不会重复请求 QQ。
        """
        if not item_ids:
            return {}
        found: dict[str, str] = {}
        waiting: list[tuple[str, str, Future[str | None]]] = []
        for item_id in item_ids[:200]:
            meta = self.storage.song_meta(item_id)
            if meta is None:
                continue
            original = str(meta.get("cover_url") or "")
            if not SETTINGS.qq_cover:
                if original:
                    found[item_id] = original
                continue
            state = meta.get("qq_cover")
            if state:
                found[item_id] = state
            elif state == "":
                if original:
                    found[item_id] = original  # QQ 没有，用音源原图兜底
            else:
                waiting.append(
                    (
                        item_id,
                        original,
                        self._submit_cover(item_id, str(meta.get("name") or ""), str(meta.get("singers") or "")),
                    )
                )

        if not waiting:
            return found

        started = time.perf_counter()
        deadline = started + max(SETTINGS.qq_cover_wait, 0)
        for item_id, original, future in waiting:
            try:
                url = future.result(timeout=max(deadline - time.perf_counter(), 0.01))
            except Exception:
                url = None  # 超时/失败：先给音源原图，后台查完会落库，下次就命中了
            if url:
                found[item_id] = url
            elif original:
                found[item_id] = original
        timing_log(
            f"/api/covers: {len(item_ids)} 个 id，要查 {len(waiting)} 首 / "
            f"耗时 {time.perf_counter() - started:.2f}s"
        )
        return found

    # ------------------------------------------------------------------ 内部
    def _cover_states(self, item_ids: list[str]) -> dict[str, str | None]:
        try:
            return self.storage.qq_covers_for(item_ids)
        except Exception:  # 缓存读不到就当没有，照常往下走
            return {}

    def _cover_executor(self) -> ThreadPoolExecutor:
        """常驻的封面线程池：搜索预热和 /api/covers 共用它，才能互相接住。"""
        pool = self._cover_pool
        if pool is None:
            with self._cover_lock:
                pool = self._cover_pool
                if pool is None:
                    pool = ThreadPoolExecutor(
                        max_workers=max(SETTINGS.qq_cover_threads, 1), thread_name_prefix="qqcover"
                    )
                    self._cover_pool = pool
        return pool

    def _submit_cover(self, item_id: str, name: str, singers: str) -> Future[str | None]:
        """同一首歌只排一次队：已经在跑就返回同一个 future。"""
        with self._cover_lock:
            running = self._cover_jobs.get(item_id)
            if running is not None and not running.done():
                return running
            if len(self._cover_jobs) > 512:  # 顺手清掉已完成的，别无限长
                for key in [key for key, future in self._cover_jobs.items() if future.done()]:
                    self._cover_jobs.pop(key, None)
            future = self._cover_executor().submit(self._lookup_cover, item_id, name, singers)
            self._cover_jobs[item_id] = future
            return future

    def _lookup_cover(self, item_id: str, name: str, singers: str) -> str | None:
        """真去问一次 QQ 并落库（跑在封面线程池里）。"""
        url = qq_cover(name, singers)
        try:
            self.storage.set_qq_cover(item_id, url)
        except Exception:
            pass  # 落库失败不影响这次返回
        return url

    def cover_inflight(self) -> int:
        """给诊断用：当前有多少首歌正在查封面。"""
        with self._cover_lock:
            return sum(1 for future in self._cover_jobs.values() if not future.done())
