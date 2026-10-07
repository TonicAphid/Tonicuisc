"""音乐服务门面。

把几块拼起来，公共 API 就是这一个类：

* :class:`~tonicuisc_server.sources.SourceMixin`    —— musicdl 客户端、只搜选中音源
* :class:`~tonicuisc_server.searching.SearchMixin`  —— 搜索、分页、歌手页
* :class:`~tonicuisc_server.artwork.ArtworkMixin`   —— QQ 封面补充
* :class:`~tonicuisc_server.downloads.DownloadMixin`—— 下载、缓存、边下边播

这里只管：进程内的歌曲缓存（内存 + Redis 恢复）、直链。
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from typing import Any

from .artwork import ArtworkMixin
from .config import SETTINGS
from .downloads import DownloadMixin
from .items import (  # noqa: F401  —— 这些名字是对外的，main.py / 测试都在用
    AUDIO_SUFFIXES,
    ITEM_TTL_SECONDS,
    SEARCH_RESIDUE_MAX_AGE,
    USER_AGENT,
    DownloadFailed,
    SongNotFound,
    clean,
    song_to_item,
)
from .searching import SearchMixin
from .sources import SourceMixin
from .storage import Storage


class MusicService(SourceMixin, SearchMixin, ArtworkMixin, DownloadMixin):
    def __init__(self) -> None:
        self._client: Any | None = None
        self._client_lock = threading.Lock()
        self._songs: dict[str, tuple[Any, float]] = {}
        self._search_locks: dict[tuple[str, tuple[str, ...]], threading.Lock] = {}
        #: 正在后台「软刷新」的关键词（同一个关键词只跑一个）
        self._revalidating: set[tuple[str, tuple[str, ...]]] = set()
        self._download_locks: dict[str, threading.Lock] = {}
        #: 同一音源一次只跑一个搜索（抓取量字段是写在共享 client 上的，见 sources.py）
        self._source_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        #: QQ 封面：常驻线程池 + 正在查的歌曲（搜索预热和 /api/covers 共用同一批任务）
        #: 锁要可重入：`_submit_cover` 拿着它的时候会去 `_cover_executor()` 里再拿一次
        self._cover_lock = threading.RLock()
        self._cover_jobs: dict[str, Any] = {}
        self._cover_pool: Any | None = None
        self.work_dir = SETTINGS.work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)
        #: musicdl 每次搜索都会新建 "<时间戳> <关键词>" 目录，所以下载完要把文件
        #: 挪到这里，缓存才能跨搜索命中。
        self.files_dir = SETTINGS.cache_dir / "files"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        #: 歌曲元信息 / 搜索结果缓存 / 账户 / 设备 / 收藏列表（都在 Redis 里）
        self.storage = Storage()
        self._quiet: Any | None = None

    # ------------------------------------------------------------ 歌曲内存缓存
    def _remember(self, song: Any) -> None:
        item = song_to_item(song)
        self._songs[item["id"]] = (song, time.time() + ITEM_TTL_SECONDS)
        self.storage.upsert_song(item, song)
        self._purge()

    def _purge(self) -> None:
        now = time.time()
        for key in [k for k, (_, deadline) in self._songs.items() if deadline < now]:
            self._songs.pop(key, None)

    def get_song(self, item_id: str) -> Any:
        entry = self._songs.get(item_id)
        if entry is not None and entry[1] >= time.time():
            return entry[0]
        song = self._restore(item_id)
        if song is None:
            raise SongNotFound(item_id)
        return song

    def _restore(self, item_id: str) -> Any | None:
        """内存没有就去 Redis 找；音源直链过期就重搜一次刷新。"""
        payload = self.storage.load_song(item_id)
        if payload is None:
            return None
        song = self._rebuild(payload)
        if song is None:
            return None
        if self.storage.url_expired(item_id):
            refreshed = self._refresh(song)
            if refreshed is song:
                # 刷新失败：不写内存，下次请求再试一次
                return song
            self._songs[item_id] = (refreshed, time.time() + ITEM_TTL_SECONDS)
            return refreshed
        self._songs[item_id] = (song, time.time() + ITEM_TTL_SECONDS)
        return song

    def _rebuild(self, payload: dict[str, Any]) -> Any | None:
        try:
            from musicdl.musicdl import SongInfo  # 没有 musicdl 时退化成简单对象

            song = SongInfo.fromdict(payload)
            if song.identifier:
                return song
        except Exception:
            pass
        try:
            return SimpleNamespace(**payload)
        except Exception:
            return None

    def _refresh(self, song: Any) -> Any:
        """直链失效后就地重搜一次，拿到新的 download_url。失败返回原对象。"""
        keyword = " ".join(
            part for part in (str(getattr(song, "song_name", "") or ""), str(getattr(song, "singers", "") or "")) if part
        ).strip()
        if not keyword:
            return song
        try:
            # 只重搜这首歌所在的音源，别把其他音源也打一遍
            results = self._search_selected(keyword, {str(getattr(song, "source", ""))})
        except Exception:
            return song
        for candidate in results.get(getattr(song, "source", None), []) or []:
            if str(getattr(candidate, "identifier", "")) == str(getattr(song, "identifier", "")):
                try:
                    self._remember(candidate)  # 入库失败不能把「已经拿到新直链」这件事也搞砸
                except Exception:
                    pass
                return candidate
        return song

    # ------------------------------------------------------------------ 直链
    def direct_url(self, item_id: str) -> dict[str, Any]:
        song = self.get_song(item_id)
        # 从 Redis 重建出来的 payload 可能缺字段（老数据 / 手工写过），一律取不到就当没有，
        # 不能直接属性访问抛 AttributeError → 500
        url = getattr(song, "download_url", None)
        if not isinstance(url, str) or not url.startswith("http"):
            raise DownloadFailed("该歌曲没有可直接播放的链接")
        headers = {str(k): str(v) for k, v in dict(getattr(song, "default_download_headers", None) or {}).items()}
        cookies = dict(getattr(song, "default_download_cookies", None) or {})
        if cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
        return {
            "url": url,
            "headers": headers,
            "ext": clean(getattr(song, "ext", "") or "").lstrip("."),
        }
