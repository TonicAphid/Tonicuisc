"""搜索、分页、歌手页。

搜索结果缓存按「关键词 + 音源」存在 **Redis** 里（默认 24 小时），所以服务重启
之后同一个关键词依然秒回，不会又去联网搜一遍。缓存里条数不够（翻页要更多）才重新抓。

**软刷新**：App 每次搜索都会带 ``refresh=true``。真按「强制刷新」办，同一个词每搜一次
都要联网 5 秒。所以这里的规则是：缓存比 ``TONICUISC_SEARCH_SOFT_TTL``（默认 10 分钟）
新就直接返回（几十毫秒），同时在后台重新搜一遍把缓存更新掉；只有缓存实在太旧、
或者压根没有，才阻塞着真去搜。
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any

from .config import SETTINGS, resolve_sources
from .items import (
    song_to_item,
    split_artists,
    timing_log,
)

if TYPE_CHECKING:
    from .storage import Storage


class SearchMixin:
    """宿主需要提供：``storage``、``_songs``、``_search_locks``、``_locks_guard``、
    ``_revalidating``，以及 ``_remember()`` / ``prewarm_covers()`` / ``_search_selected()``。"""

    if TYPE_CHECKING:
        storage: Storage
        _search_locks: Any
        _revalidating: set[tuple[str, tuple[str, ...]]]

    # ------------------------------------------------------------------ search
    def search(
        self,
        keyword: str,
        sources: list[str] | None = None,
        limit: int | None = None,
        refresh: bool = False,
        allow_short: bool = False,
    ) -> list[dict[str, Any]]:
        """``allow_short``：缓存条数不够时也先返回（首页用），后台再补全。

        翻页时必须为 False —— 少了就得真去搜，不然这一页会是空的。
        """
        keyword = keyword.strip()
        if not keyword:
            return []
        selected = resolve_sources(sources) if sources else resolve_sources(SETTINGS.sources)
        key = (keyword.lower(), tuple(sorted(selected)))
        need = limit or 0

        cached = self._cached_search(key, need, allow_short=allow_short)
        if cached is not None:
            short = bool(need) and len(cached) < need
            if short or refresh:
                # App 每次都发 refresh=true → 软刷新：先给现在这份，后台偷偷更新
                if short:
                    timing_log(f"search '{keyword}' -> 缓存偏少（{len(cached)}/{need}），先给这些，后台补全")
                elif self._cache_stale(key):
                    timing_log(f"search '{keyword}' -> 缓存超过 {SETTINGS.search_soft_ttl}s，不再软刷新")
                    cached = None  # 太旧了，老老实实重搜
                else:
                    timing_log(f"search '{keyword}' -> 命中缓存（软刷新，{SETTINGS.search_soft_ttl}s 内不重搜）")
                if cached is not None:
                    self._revalidate_async(keyword, selected, key, limit)
                    return cached
            else:
                timing_log(f"search '{keyword}' -> 命中缓存（要 {need or '全部'} 条, 拿到 {len(cached)}）")
                return cached

        # 同一个关键词并发进来时只查一次，其余的等结果。
        with self._search_lock(key):
            cached = self._cached_search(key, need, allow_short=allow_short)
            if cached is not None and (not refresh or not self._cache_stale(key)):
                timing_log(f"search '{keyword}' -> 命中缓存（并发等待后）")
                return cached
            timing_log(f"search '{keyword}' -> 缓存不够/没有，真的去搜（要 {need or SETTINGS.search_size} 条）")
            items = self._search_sources(keyword, set(selected), limit)
            self._save_cache(key, items)
            return [dict(item) for item in items]

    def _revalidate_async(
        self,
        keyword: str,
        selected: list[str],
        key: tuple[str, tuple[str, ...]],
        limit: int | None,
    ) -> None:
        """后台重新搜一遍更新缓存：同一个关键词同时只跑一个，失败了下次再说。"""
        with self._locks_guard:
            if key in self._revalidating:
                return
            self._revalidating.add(key)

        def work() -> None:
            started = time.perf_counter()
            try:
                with self._search_lock(key):
                    items = self._search_sources(keyword, set(selected), limit, record=False)
                    self._save_cache(key, items)
                timing_log(
                    f"search '{keyword}' -> 后台刷新完成（{len(items)} 条 / {time.perf_counter() - started:.2f}s）"
                )
            except Exception as exc:  # 后台失败不能影响任何请求
                timing_log(f"search '{keyword}' -> 后台刷新失败（忽略）: {exc}")
            finally:
                with self._locks_guard:
                    self._revalidating.discard(key)

        threading.Thread(target=work, name=f"revalidate:{keyword}", daemon=True).start()

    def _cache_stale(self, key: tuple[str, tuple[str, ...]]) -> bool:
        """缓存比「软刷新」窗口还旧（或者查不到年龄）就算过期。"""
        try:
            age = self.storage.search_cache_age(key)
        except Exception:
            return True
        return age is None or age > max(SETTINGS.search_soft_ttl, 0)

    def _save_cache(self, key: tuple[str, tuple[str, ...]], items: list[dict[str, Any]]) -> None:
        try:
            self.storage.save_search_cache(key, items)
        except Exception as exc:  # Redis 出问题也不能让搜索本身失败
            timing_log(f"搜索结果写缓存失败（忽略）: {exc}")

    def _cached_search(
        self,
        key: tuple[str, tuple[str, ...]],
        need: int,
        allow_short: bool = False,
    ) -> list[dict[str, Any]] | None:
        """缓存里够多就直接切片；不够多返回 None 让调用方重新搜（``allow_short`` 时也给）。"""
        try:
            items = self.storage.load_search_cache(key)
        except Exception as exc:  # 缓存读不到就当没有，照常联网搜
            timing_log(f"搜索结果读缓存失败（忽略）: {exc}")
            return None
        if items is None:
            return None
        if need and len(items) < need and not allow_short:
            return None
        return items if not need else items[:need]

    def search_page(
        self,
        keyword: str,
        sources: list[str] | None = None,
        offset: int = 0,
        limit: int | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """分页搜索：客户端滑到底就要下一页，同一关键词不会重复给同一首。"""
        offset = max(offset, 0)
        size = SETTINGS.search_page_size if limit is None else max(1, min(limit, 100))
        need = min(offset + size, SETTINGS.search_max)
        started = time.perf_counter()
        # 首页：缓存里少几条也先给（别为了凑满一页让用户干等联网）；
        # 翻页：少了必须真去搜，不然这一页是空的。
        items = self.search(
            keyword, sources=sources, limit=need, refresh=refresh, allow_short=(offset == 0)
        )
        page = items[offset : offset + size]
        timing_log(f"search_page offset={offset} limit={size} -> 返回 {len(page)} 条 / 合计 {time.perf_counter() - started:.2f}s")
        return {
            "items": page,
            "offset": offset,
            "limit": size,
            "total": len(items),
            # 这一页有内容、而且还没抓到上限 → 客户端可以继续要下一页。
            # 别用「这一页是不是满的」判断：首页为了让用户少等，允许缓存不足就先返回
            # （allow_short），那样 has_more 会恒为 False，用户再也翻不动了；
            # 真没货时下一页自然会拿到空页，那时再收手。
            "has_more": bool(page) and need < SETTINGS.search_max,
        }

    def _search_lock(self, key: tuple[str, tuple[str, ...]]) -> threading.Lock:
        with self._locks_guard:
            return self._search_locks.setdefault(key, threading.Lock())

    def _search_sources(
        self,
        keyword: str,
        wanted: set[str],
        limit: int | None,
        *,
        record: bool = True,
    ) -> list[dict[str, Any]]:
        source_started = time.perf_counter()
        self._prune_search_dirs()
        results = self._search_selected(keyword, wanted, limit)
        source_elapsed = time.perf_counter() - source_started

        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for source_name, songs in results.items():
            if source_name not in wanted:
                continue
            for song in songs or []:
                try:
                    item = song_to_item(song)
                except Exception:  # 单条坏数据不能拖垮整次搜索
                    continue
                if item["id"] in seen or not item["ext"]:
                    continue
                seen.add(item["id"])
                self._remember(song)
                items.append(item)
        if limit:
            items = items[:limit]

        # 搜索不等封面：先把音源原图返回，同时把这批歌丢给后台线程池并发查 QQ 封面。
        # 已经查过的（Redis 里有）直接写进结果，所以重复搜同一个词第一帧就是 QQ 封面。
        warmed = self.prewarm_covers(items)
        timing_log(
            f"search '{keyword}' -> {len(items)} 条 | 音源 {source_elapsed:.2f}s "
            f"| 合计 {time.perf_counter() - source_started:.2f}s"
            f"（封面：{len(items) - warmed} 张已就绪，{warmed} 张后台预热中）"
        )
        # 只有用户真的搜了一次才算搜索历史：后台软刷新也记的话，
        # /api/history 的次数会虚高，还会把 500 条历史窗口顶掉（连带每轮都全量裁剪歌曲）
        if record:
            self.storage.record_search(keyword, sorted(wanted), [item["id"] for item in items])
        return items

    # ---------------------------------------------------------------- 歌手页
    def artist(self, name: str, sources: list[str] | None = None, limit: int = 50) -> dict[str, Any]:
        """搜歌手名，只留下歌手字段里真的包含这个名字的。

        音源只有关键词搜索、没有"歌手全部作品"这种接口，所以这是能做到的最好结果：
        同一个人名多抓一些（默认按 2 倍 + 至少 60 条）再过滤。
        过滤后一首都没有（比如搜出来全是翻唱）时，退回不过滤的结果。
        """
        name = (name or "").strip()
        if not name:
            return {"name": name, "total": 0, "items": [], "filtered": False}
        # 抓取量夹在「2 倍且至少 60」和 search_max 之间：
        # 想要的条数比音源实际给的多时，缓存永远凑不够 need，歌手页就会每次都真联网
        # （以前就是这样：每进一次歌手页就等 5~20 秒）。allow_short 让它先给、后台补。
        fetch = min(max(limit * 2, 60), max(SETTINGS.search_max, limit))
        items = self.search(name, sources=sources, limit=fetch, allow_short=True)
        tokens = split_artists(name)
        matched = [item for item in items if any(token in str(item.get("singers") or "").lower() for token in tokens)]
        filtered = bool(matched)
        result = matched if filtered else items
        return {"name": name, "total": len(result[:limit]), "items": result[:limit], "filtered": filtered}

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.storage.recent_searches(limit)
