"""musicdl 客户端管理 + 「只搜选中的音源」。

musicdl 是懒加载的，这样没装 musicdl 时 ``/api/health`` 也能用。
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Any

from .config import SETTINGS, resolve_sources
from .items import timing_log

class SourceMixin:
    """宿主需要提供：``_client``、``_client_lock``、``_quiet``、``work_dir``、
    ``_source_locks``、``_locks_guard``。"""

    if TYPE_CHECKING:
        _client: Any
        _client_lock: Any
        _quiet: Any
        work_dir: Any
        _source_locks: dict[str, Any]
        _locks_guard: Any

    # ------------------------------------------------------------------ client
    @property
    def client(self) -> Any:
        with self._client_lock:
            if self._client is None:
                from musicdl.musicdl import MusicClient  # heavy import, keep lazy

                sources = resolve_sources(SETTINGS.sources)
                self._client = MusicClient(
                    music_sources=sources,
                    init_music_clients_cfg={
                        name: {
                            "work_dir": str(self.work_dir),
                            "search_size_per_source": SETTINGS.search_size,
                            # 每页 1 条 → musicdl 会拆成 search_size 个请求
                            "search_size_per_page": SETTINGS.search_size_per_page,
                            "strict_limit_search_size_per_page": True,
                            "disable_print": True,
                        }
                        for name in sources
                    },
                    # 每个音源并发请求数
                    clients_threadings={name: SETTINGS.search_threads for name in sources},
                )
            return self._client

    def _quiet_progress(self) -> Any:
        """musicdl 的 search() 会自己建进度条；给它一个禁止渲染的就不会刷屏。"""
        if self._quiet is None:
            from rich.progress import Progress

            self._quiet = Progress(disable=True)
        return self._quiet

    def _per_page(self, size: int) -> int:
        """要 ``size`` 条时每页取几条，也就是拆成几个请求。

        musicdl 的请求数 = ``ceil(search_size_per_source / search_size_per_page)``，
        想让这些请求铺满 ``search_threads`` 个线程，每页条数就取
        ``ceil(size / search_threads)``，再不低于配置里的下限。
        """
        threads = max(SETTINGS.search_threads, 1)
        return max(SETTINGS.search_size_per_page, -(-max(size, 1) // threads))

    def _tune_fetch_size(self, client: Any, size: int) -> None:
        """拆成 ``search_threads`` 个并行请求，**别**一个请求拉满。

        musicdl 每个请求内部是**串行**解析结果的：酷我要为每首歌单独请求直链
        （``_parsewiththirdpartapis`` / ``_parsewithofficialapiv1``），所以
        「1 个请求拿 15 条」= 15 首歌排队解析，实测 18~23s；
        拆成 5 个请求各 3 条 → 5 首歌的时间，实测 ~5s。
        """
        size = max(size, 1)
        try:
            client.search_size_per_source = size
            client.search_size_per_page = self._per_page(size)
        except Exception:
            pass

    def _source_lock(self, name: str) -> Any:
        """每个音源一把锁：``search_size_per_source`` 是写在**共享 client 对象**上的。

        不同音源之间依然并行（这是主要的并行度），但同一个音源上，
        两次不同 ``need`` 的搜索不能同时改这两个字段，否则抓取量和请求数会互相覆盖。
        """
        with self._locks_guard:
            return self._source_locks.setdefault(name, threading.Lock())

    def _search_selected(self, keyword: str, wanted: set[str], need: int | None = None) -> dict[str, list[Any]]:
        """只请求被选中的音源。

        musicdl 的 ``MusicClient.search()`` 会把配置里所有音源都打一遍，
        所以这里直接调用对应音源 client 的 ``search()``。
        ``need`` 是这次想要多少条（分页时会变大），用来调整抓取量。
        """
        client = self.client
        clients = getattr(client, "music_clients", None)
        if not isinstance(clients, dict) or not clients:
            # 兜底：没有 music_clients 结构的桩 / 旧版 musicdl
            return client.search(keyword) or {}

        selected = [name for name in sorted(wanted) if name in clients]
        if not selected:
            raise ValueError(f"没有可用的音源；服务端已启用: {', '.join(sorted(clients))}")

        threadings = getattr(client, "clients_threadings", None) or {}
        overrides = getattr(client, "requests_overrides", None) or {}
        rules = getattr(client, "search_rules", None) or {}
        # 想要 need 条就按音源数分摊，别每个音源都拉 need 条（多拉的部分纯浪费）
        wanted_per_source = -(-max(need or 0, 1) // len(selected))
        per_source = max(wanted_per_source, SETTINGS.search_size)
        per_page = self._per_page(per_source)
        pages = -(-per_source // per_page)

        def run(name: str) -> tuple[str, list[Any], float, float]:
            source_client = clients[name]
            # 「改抓取量 → 发请求」整段锁住：抓取量是写在共享 client 上的，
            # 并发搜索各写各的会让 musicdl 按错的尺寸去分页。
            # 等锁时间单独记账：它和「音源本身慢」长得一模一样，不拆开看不出来。
            queued = time.perf_counter()
            with self._source_lock(name):
                waited = time.perf_counter() - queued
                self._tune_fetch_size(source_client, per_source)
                started = time.perf_counter()
                songs = (
                    source_client.search(
                        keyword=keyword,
                        num_threadings=threadings.get(name, SETTINGS.search_threads),
                        request_overrides=overrides.get(name, {}),
                        rule=rules.get(name, {}),
                        # 传一个禁用输出的 Progress，musicdl 就不会再打印进度条
                        main_process_context=self._quiet_progress(),
                    )
                    or []
                )
                elapsed = time.perf_counter() - started
            return name, songs, elapsed, waited

        timing_log(
            f"音源搜索: {keyword!r} 每源 {per_source} 条 / 每页 {per_page} 条 "
            f"= {pages} 个请求并行（{len(selected)} 个音源并行）"
        )
        results: dict[str, list[Any]] = {}
        with ThreadPoolExecutor(max_workers=max(len(selected), 1)) as pool:
            futures = {pool.submit(run, name): name for name in selected}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    name, songs, elapsed, waited = future.result()
                    results[name] = songs
                    # 等锁 >50ms 才显示：说明前面有个搜索（多半是软刷新的后台线程）占着音源
                    wait_note = f"（等锁 {waited:.2f}s）" if waited > 0.05 else ""
                    timing_log(f"  {name}: {len(songs)} 条 / {elapsed:.2f}s{wait_note}")
                except Exception as exc:
                    results[name] = []
                    timing_log(f"  {name}: 失败 {exc}")
        return results
