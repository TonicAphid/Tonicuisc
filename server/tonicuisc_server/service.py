"""Thin wrapper around musicdl: keyword search + cached downloads.

musicdl is imported lazily so that ``/api/health`` works even when the
(heavy) downloader package is not installed yet.
"""

from __future__ import annotations

import re
import shutil
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import requests

from .config import SETTINGS, resolve_sources, source_label
from .qqmusic import search_cover as qq_cover
from .storage import Storage

#: How long a search result stays addressable through /api/stream/{id}.
ITEM_TTL_SECONDS = 3 * 60 * 60

#: 相同关键词的搜索结果缓存时间，避免每次重新联网搜索。
SEARCH_TTL_SECONDS = 10 * 60

#: 搜索残留目录（只有 *.pkl）至少闲置这么久才清理，避免打断正在进行的下载。
SEARCH_RESIDUE_MAX_AGE = 600

AUDIO_SUFFIXES = {".mp3", ".flac", ".m4a", ".aac", ".wav", ".ogg", ".ape", ".wma", ".wv", ".tta"}

#: 兜底请求用的浏览器 UA
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class SongNotFound(KeyError):
    """Raised when a song id is unknown or expired."""


class DownloadFailed(RuntimeError):
    """Raised when musicdl could not produce a local audio file."""


def _clean(value: Any, default: str = "") -> str:
    text = "" if value is None else str(value).strip()
    return default if text.upper() in {"NULL", "NONE"} else text


def _split_artists(text: str) -> list[str]:
    """把「蒋雪儿/王力宏」这种人名串拆成单个歌手（都转小写）。"""
    return [part.strip().lower() for part in re.split(r"[/,，、&；;|]+", text or "") if part.strip()]


def song_to_item(song: Any) -> dict[str, Any]:
    ext = _clean(song.ext).lstrip(".")
    return {
        "id": f"{song.source}:{song.identifier}",
        "source": song.source,
        "source_label": source_label(str(song.source)),
        "root_source": _clean(song.root_source),
        "name": _clean(song.song_name, "未知歌曲"),
        "singers": _clean(song.singers, "未知歌手"),
        "album": _clean(song.album),
        "ext": ext,
        "duration": _clean(song.duration),
        "duration_s": song.duration_s,
        "file_size": _clean(song.file_size),
        "cover_url": _clean(song.cover_url),
        "has_lyric": bool(_clean(song.lyric)),
    }


class MusicService:
    def __init__(self) -> None:
        self._client: Any | None = None
        self._client_lock = threading.Lock()
        self._songs: dict[str, tuple[Any, float]] = {}
        self._search_cache: dict[tuple[str, tuple[str, ...], int], tuple[list[dict[str, Any]], float]] = {}
        self._search_locks: dict[tuple[str, tuple[str, ...], int], threading.Lock] = {}
        self._download_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self.work_dir = SETTINGS.work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)
        #: musicdl 每次搜索都会新建 "<时间戳> <关键词>" 目录，所以下载完要
        #: 把文件挪到这里，缓存才能跨搜索命中。
        self.files_dir = SETTINGS.cache_dir / "files"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        #: 搜索结果 / 歌曲元信息 / 搜索历史
        self.storage = Storage()
        self._quiet: Any | None = None

    def _quiet_progress(self) -> Any:
        """musicdl 的 search() 会自己建进度条；给它一个禁止渲染的就不会刷屏。"""
        if self._quiet is None:
            from rich.progress import Progress

            self._quiet = Progress(disable=True)
        return self._quiet

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

    # ------------------------------------------------------------------ search
    def search(
        self,
        keyword: str,
        sources: list[str] | None = None,
        limit: int | None = None,
        refresh: bool = False,
    ) -> list[dict[str, Any]]:
        keyword = keyword.strip()
        if not keyword:
            return []
        selected = resolve_sources(sources) if sources else resolve_sources(SETTINGS.sources)
        key = (keyword.lower(), tuple(sorted(selected)))
        need = limit or 0
        if not refresh:
            cached = self._cached_search(key, need)
            if cached is not None:
                return cached
        # 同一个关键词并发进来时只查一次，其余的等结果。
        with self._search_lock(key):
            if not refresh:
                cached = self._cached_search(key, need)
                if cached is not None:
                    return cached
            items = self._search_sources(keyword, set(selected), limit)
            self._search_cache[key] = (items, time.time() + SEARCH_TTL_SECONDS, len(items))
            self._purge_search_cache()
            return [dict(item) for item in items]

    def search_page(
        self,
        keyword: str,
        sources: list[str] | None = None,
        offset: int = 0,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """分页搜索：客户端滑到底就要下一页，同一关键词不会重复给同一首。"""
        offset = max(offset, 0)
        size = SETTINGS.search_page_size if limit is None else max(1, min(limit, 100))
        need = min(offset + size, SETTINGS.search_max)
        items = self.search(keyword, sources=sources, limit=need)
        page = items[offset : offset + size]
        return {
            "items": page,
            "offset": offset,
            "limit": size,
            "total": len(items),
            # 这一页是满的、音源还有货、并且没到上限 → 还能继续加载
            "has_more": len(page) >= size and len(items) >= need and need < SETTINGS.search_max,
        }

    def _cached_search(self, key: tuple[str, tuple[str, ...]], need: int) -> list[dict[str, Any]] | None:
        """缓存里够多就直接切片；不够多返回 None 让调用方重新搜。"""
        entry = self._search_cache.get(key)
        if entry is None or entry[1] < time.time():
            return None
        items, _, size = entry
        if need and size < need:
            return None
        sliced = items if not need else items[:need]
        return [dict(item) for item in sliced]

    def _search_lock(self, key: tuple[str, tuple[str, ...]]) -> threading.Lock:
        with self._locks_guard:
            return self._search_locks.setdefault(key, threading.Lock())

    def _purge_search_cache(self) -> None:
        now = time.time()
        for key in [k for k, (_, deadline, _) in self._search_cache.items() if deadline < now]:
            self._search_cache.pop(key, None)
        if len(self._search_cache) > 200:  # 兜底，避免无限增长
            oldest = sorted(self._search_cache, key=lambda k: self._search_cache[k][1])[: len(self._search_cache) - 200]
            for key in oldest:
                self._search_cache.pop(key, None)

    def _tune_fetch_size(self, client: Any, need: int) -> None:
        """按这次要多少条调整 musicdl 的抓取量。

        请求数保持在 search_threads 附近：要 30 条、10 个线程 → 每页 3 条、10 个请求。
        配置里的 search_size_per_page 是下限（默认 1，即「10 条分 10 个请求」）。
        """
        per_page = max(SETTINGS.search_size_per_page, -(-max(need, 1) // max(SETTINGS.search_threads, 1)))  # 向上取整
        try:
            client.search_size_per_source = max(need, 1)
            client.search_size_per_page = per_page
        except Exception:
            pass

    def _search_sources(self, keyword: str, wanted: set[str], limit: int | None) -> list[dict[str, Any]]:
        self._prune_search_dirs()
        results = self._search_selected(keyword, wanted, limit)
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for source_name, songs in results.items():
            if source_name not in wanted:
                continue
            for song in songs or []:
                try:
                    item = song_to_item(song)
                except Exception:  # a single broken result must not kill the search
                    continue
                if item["id"] in seen or not item["ext"]:
                    continue
                seen.add(item["id"])
                self._remember(song)
                items.append(item)
        if limit:
            items = items[:limit]
        self.storage.record_search(keyword, sorted(wanted), [item["id"] for item in items])
        return items

    # ------------------------------------------------------------------ 封面
    def covers(self, item_ids: list[str]) -> dict[str, str]:
        """按需补封面：只返回「有 QQ 封面」的那些 id。

        搜索接口不等这个，客户端拿到列表之后再单独来问，查到就换图。
        查过的结果落库（'' = 查过没有），所以同一首歌只真的查一次。
        """
        if not item_ids or not SETTINGS.qq_cover:
            return {}
        found: dict[str, str] = {}
        pending: list[tuple[str, str, str]] = []
        for item_id in item_ids[:200]:
            cached = self.storage.qq_cover_of(item_id)
            if cached is None:
                meta = self.storage.song_meta(item_id) or {}
                name = str(meta.get("name") or "")
                if name:
                    pending.append((item_id, name, str(meta.get("singers") or "")))
            elif cached:
                found[item_id] = cached

        if pending:
            def lookup(entry: tuple[str, str, str]) -> tuple[str, str | None]:
                return entry[0], qq_cover(entry[1], entry[2])

            with ThreadPoolExecutor(max_workers=max(SETTINGS.qq_cover_threads, 1)) as pool:
                for item_id, url in pool.map(lookup, pending):
                    try:
                        self.storage.set_qq_cover(item_id, url)
                    except Exception:
                        pass
                    if url:
                        found[item_id] = url
        return found

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
        per_source = max(need or SETTINGS.search_size, SETTINGS.search_size)

        def run(name: str) -> list[Any]:
            source_client = clients[name]
            self._tune_fetch_size(source_client, per_source)
            return (
                source_client.search(
                    keyword=keyword,
                    num_threadings=threadings.get(name, 5),
                    request_overrides=overrides.get(name, {}),
                    rule=rules.get(name, {}),
                    # 传一个禁用输出的 Progress，musicdl 就不会再打印进度条
                    main_process_context=self._quiet_progress(),
                )
                or []
            )

        results: dict[str, list[Any]] = {}
        with ThreadPoolExecutor(max_workers=max(len(selected), 1)) as pool:
            futures = {pool.submit(run, name): name for name in selected}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    results[name] = future.result()
                except Exception:
                    results[name] = []
        return results

    # ------------------------------------------------------------------ cache
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
        """内存没有就去 SQLite 找；音源直链过期就重搜一次刷新。"""
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
                self._remember(candidate)
                return candidate
        return song

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.storage.recent_searches(limit)

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
        items = self.search(name, sources=sources, limit=max(limit * 2, 60))
        tokens = _split_artists(name)
        matched = [
            item
            for item in items
            if any(token in str(item.get("singers") or "").lower() for token in tokens)
        ]
        filtered = bool(matched)
        result = matched if filtered else items
        return {"name": name, "total": len(result[:limit]), "items": result[:limit], "filtered": filtered}

    def lyric(self, item_id: str) -> str:
        song = self.get_song(item_id)
        text = _clean(song.lyric)
        if text:
            return text
        lrc = self.cache_path(song).with_suffix(".lrc")
        if lrc.exists():
            return lrc.read_text(encoding="utf-8", errors="ignore")
        return ""

    def direct_url(self, item_id: str) -> dict[str, Any]:
        song = self.get_song(item_id)
        url = song.download_url
        if not isinstance(url, str) or not url.startswith("http"):
            raise DownloadFailed("该歌曲没有可直接播放的链接")
        headers = {str(k): str(v) for k, v in dict(song.default_download_headers or {}).items()}
        cookies = dict(song.default_download_cookies or {})
        if cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
        return {
            "url": url,
            "headers": headers,
            "ext": _clean(song.ext).lstrip("."),
        }

    # ---------------------------------------------------------------- download
    def _lock_for(self, item_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._download_locks.setdefault(item_id, threading.Lock())

    def cache_path(self, song: Any) -> Path:
        """稳定的缓存文件名：<音源>_<id>.<格式>。"""
        ext = _clean(song.ext).lstrip(".") or "mp3"
        stem = re.sub(r'[\\/:*?"<>|]', "_", f"{song.source}_{song.identifier}")
        return self.files_dir / f"{stem}.{ext}"

    def _promote(self, source_path: Path, target: Path) -> Path:
        """把 musicdl 下载的文件挪到稳定缓存路径，并清掉那次的时间戳目录。"""
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if source_path.resolve() == target.resolve():
                return target
        except OSError:
            pass
        try:
            shutil.move(str(source_path), str(target))
        except OSError:
            shutil.copyfile(source_path, target)
        lyric_source, lyric_target = source_path.with_suffix(".lrc"), target.with_suffix(".lrc")
        if lyric_source.exists() and not lyric_target.exists():
            try:
                shutil.move(str(lyric_source), str(lyric_target))
            except OSError:
                pass
        self._prune(source_path.parent)
        return target

    def _prune(self, directory: Path) -> None:
        """清理下载残留：只删 *.pkl 之类的记录文件，音频一律不动。

        多首歌可能落在同一个时间戳目录里，所以只有目录真的空了才删目录。
        """
        work_dir = self.work_dir.resolve()
        current = directory
        while True:
            try:
                current = current.resolve()
            except OSError:
                return
            if current == work_dir or work_dir not in current.parents:
                return
            try:
                for leftover in current.iterdir():
                    if leftover.is_file() and leftover.suffix.lower() not in AUDIO_SUFFIXES:
                        leftover.unlink(missing_ok=True)
                current.rmdir()
            except OSError:
                return
            current = current.parent

    def _prune_search_dirs(self) -> None:
        """清掉 musicdl 每次搜索留下的 "<时间戳> <关键词>" 空壳目录（只剩 *.pkl）。"""
        if not self.work_dir.exists():
            return
        now = time.time()
        for source_dir in self.work_dir.iterdir():
            if not source_dir.is_dir():
                continue
            for run_dir in source_dir.iterdir():
                if not run_dir.is_dir():
                    continue
                try:
                    if now - run_dir.stat().st_mtime < SEARCH_RESIDUE_MAX_AGE:
                        continue
                    files = [p for p in run_dir.iterdir() if p.is_file()]
                except OSError:
                    continue
                if any(p.suffix.lower() in AUDIO_SUFFIXES for p in files):
                    continue
                for leftover in files:
                    leftover.unlink(missing_ok=True)
                try:
                    run_dir.rmdir()
                except OSError:
                    pass

    def ensure_file(self, item_id: str) -> Path:
        """Download the song once and return the local path (safe for threads)."""
        song = self.get_song(item_id)
        target = self.cache_path(song)
        if target.exists() and target.stat().st_size > 0:
            return target
        with self._lock_for(item_id):
            if target.exists() and target.stat().st_size > 0:
                return target
            # 之前 musicdl 直接下到时间戳目录的情况，直接搬过来复用
            legacy = self._legacy_path(song)
            if legacy is not None and legacy.exists() and legacy.stat().st_size > 0:
                return self._promote(legacy, target)
            downloaded: list[Any] = []
            for attempt in range(2):
                try:
                    downloaded = self.client.download(song_infos=[song]) or []
                except Exception as exc:  # network / source side failure
                    refreshed = self._refresh(song)  # 直链过期就换一条再试
                    if attempt == 0 and refreshed is not song:
                        song = refreshed
                        continue
                    raise DownloadFailed(str(exc)) from exc
                break
            for candidate in [*downloaded, song]:
                candidate_path = self._legacy_path(candidate)
                if candidate_path is not None and candidate_path.exists() and candidate_path.stat().st_size > 0:
                    return self._promote(candidate_path, target)
            raise DownloadFailed("下载失败：音源未返回可用文件")

    # ------------------------------------------------- 边下边播（未缓存时用）
    def _legacy_path(self, song: Any) -> Path | None:
        """musicdl 自己算出来的保存路径（没有就返回 None）。"""
        try:
            raw = song.save_path
        except Exception:
            return None
        if not raw or not str(raw).strip():
            return None
        return Path(str(raw))

    def cached_file(self, item_id: str) -> Path | None:
        """已经下载好的文件就返回路径，否则返回 None（不会再触发下载）。"""
        song = self.get_song(item_id)
        target = self.cache_path(song)
        if target.exists() and target.stat().st_size > 0:
            return target
        legacy = self._legacy_path(song)
        if legacy is not None and legacy.exists() and legacy.stat().st_size > 0:
            return self._promote(legacy, target)
        return None

    def open_upstream(self, item_id: str) -> tuple[Any, Path, Path]:
        """连上音源直链，返回 (响应, 缓存目标, 临时文件)。

        先拿到响应头再交给 StreamingResponse，这样上游报错还能正常返回 502。
        请求走 musicdl 自己的会话（UA / cookies / 代理都已就绪），
        直接用裸 requests 会被 CDN 403。
        403/404 多半是直链过期，会自动重搜一次换新链接再试。
        """
        song = self.get_song(item_id)
        response: Any = None
        last_error: Exception | None = None
        for attempt in range(2):
            url = getattr(song, "download_url", None)
            if not isinstance(url, str) or not url.startswith("http"):
                raise DownloadFailed("该歌曲没有可用下载链接")
            try:
                response = self._open_url(song, url)
                break
            except Exception as exc:
                last_error = exc
                refreshed = self._refresh(song)
                if attempt == 1 or refreshed is song:
                    break
                song = refreshed
        if response is None:
            raise DownloadFailed(str(last_error))
        target = self.cache_path(song)
        target.parent.mkdir(parents=True, exist_ok=True)
        return response, target, target.with_name(target.name + ".part")

    def _open_url(self, song: Any, url: str) -> Any:
        headers = {str(k): str(v) for k, v in dict(getattr(song, "default_download_headers", None) or {}).items()}
        cookies = dict(getattr(song, "default_download_cookies", None) or {})
        kwargs: dict[str, Any] = {"stream": True, "timeout": (10, 30)}
        if headers:
            kwargs["headers"] = headers
        if cookies:
            kwargs["cookies"] = cookies
        source_client = (getattr(self.client, "music_clients", None) or {}).get(getattr(song, "source", None))
        if source_client is not None and hasattr(source_client, "get"):
            response = source_client.get(url, **kwargs)
        else:  # 兜底：至少带上 UA
            response = requests.get(url, stream=True, timeout=(10, 30), headers=headers or {"User-Agent": USER_AGENT})
        response.raise_for_status()
        return response

    def relay(self, response: Any, target: Path, temp_path: Path, chunk_size: int = 512 * 1024) -> Iterator[bytes]:
        """边吐给播放器边写缓存；中断就丢掉半截文件，不污染缓存。"""
        expected = int(response.headers.get("Content-Length") or 0)
        written, completed = 0, False
        try:
            with temp_path.open("wb") as fp:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if not chunk:
                        continue
                    fp.write(chunk)
                    written += len(chunk)
                    yield chunk
            completed = written > 0 and (expected == 0 or written >= expected)
        finally:
            response.close()
            if completed:
                temp_path.replace(target)
            else:
                temp_path.unlink(missing_ok=True)
