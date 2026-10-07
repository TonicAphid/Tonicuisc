"""下载、缓存与「边下边播」。

musicdl 每次搜索都会新建 ``<时间戳> <关键词>`` 目录，所以下载完要把文件
挪到稳定的缓存路径（``files_dir``），缓存才能跨搜索命中。
"""

from __future__ import annotations

import re
import shutil
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import requests

from .items import (
    AUDIO_SUFFIXES,
    SEARCH_RESIDUE_MAX_AGE,
    USER_AGENT,
    DownloadFailed,
    clean,
)

if TYPE_CHECKING:
    from .storage import Storage


class DownloadMixin:
    """宿主需要提供：``files_dir``、``work_dir``、``_download_locks``、``_locks_guard``，
    以及 ``get_song()`` / ``_refresh()`` / ``client``。"""

    if TYPE_CHECKING:
        files_dir: Path
        work_dir: Path
        storage: Storage

    # ------------------------------------------------------------------ 路径
    def _lock_for(self, item_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._download_locks.setdefault(item_id, threading.Lock())

    def cache_path(self, song: Any) -> Path:
        """稳定的缓存文件名：<音源>_<id>.<格式>。

        ``ext`` 是音源给的元数据，必须洗成纯后缀：``../../x`` 这种能把文件写出
        ``files_dir`` 之外（写文件和读歌词都会跟着越界）。
        """
        raw_ext = clean(getattr(song, "ext", "") or "").lstrip(".")
        ext = re.sub(r"[^A-Za-z0-9]", "", raw_ext).lower()[:8] or "mp3"
        stem = re.sub(r'[\\/:*?"<>|]', "_", f"{song.source}_{song.identifier}")
        return self.files_dir / f"{stem}.{ext}"

    def _legacy_path(self, song: Any) -> Path | None:
        """musicdl 自己算出来的保存路径（没有就返回 None）。"""
        try:
            raw = song.save_path
        except Exception:
            return None
        if not raw or not str(raw).strip():
            return None
        return Path(str(raw))

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
            try:
                shutil.copyfile(source_path, target)
            except OSError as exc:
                # move 和 copy 都失败：抛个能被上层翻译成 502 的错，
                # 而不是让原始 OSError 冒泡成 500
                raise DownloadFailed(f"缓存文件搬运失败: {exc}") from exc
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
        self._prune_partial_files()
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

    def _prune_partial_files(self, max_age: float = 600.0) -> None:
        """清掉边下边播留下的半截 ``*.part``（临时文件是按请求随机命名的）。

        正在写的那些一定很新，所以闲置超过 ``max_age`` 秒才删。
        """
        if not self.files_dir.exists():
            return
        now = time.time()
        for partial in self.files_dir.glob("*.part"):
            try:
                if now - partial.stat().st_mtime >= max_age:
                    partial.unlink(missing_ok=True)
            except OSError:
                continue

    # ------------------------------------------------------------------ 下载
    def ensure_file(self, item_id: str) -> Path:
        """下载这首歌并返回本地路径（线程安全）。"""
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
            last_error: Exception | None = None
            for attempt in range(2):
                try:
                    downloaded = self.client.download(song_infos=[song]) or []
                    last_error = None
                except Exception as exc:  # 网络 / 音源侧失败
                    downloaded, last_error = [], exc
                # 找到能用的文件就收工；找不到（含 musicdl **静默返回空列表**）
                # 也走一次「换直链再试」——以前只有抛异常才重试
                for candidate in [*downloaded, song]:
                    candidate_path = self._legacy_path(candidate)
                    if candidate_path is not None and candidate_path.exists() and candidate_path.stat().st_size > 0:
                        return self._promote(candidate_path, target)
                refreshed = self._refresh(song)  # 直链过期就换一条再试
                if attempt == 0 and refreshed is not song:
                    song = refreshed
                    continue
                break
            if last_error is not None:
                raise DownloadFailed(str(last_error)) from last_error
            raise DownloadFailed("下载失败：音源未返回可用文件")

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

    # ------------------------------------------------------- 边下边播（未缓存时用）
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
        # 每次拉流用自己的临时文件：两个请求同时边下边播同一个文件时，
        # 共用一个 .part 会把字节交错写坏缓存
        return response, target, target.with_name(f"{target.name}.{uuid.uuid4().hex[:8]}.part")

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
                try:
                    temp_path.replace(target)
                except OSError:
                    # Windows 上目标正被 Range 读占用时会失败：这份就当没下过，
                    # 不能让异常冒泡把整个响应打断
                    temp_path.unlink(missing_ok=True)
            else:
                temp_path.unlink(missing_ok=True)

    # ------------------------------------------------------------------ 歌词
    def lyric(self, item_id: str) -> str:
        song = self.get_song(item_id)
        text = clean(getattr(song, "lyric", None))  # 从 Redis 重建的对象可能没这个字段
        if text:
            return text
        lrc = self.cache_path(song).with_suffix(".lrc")
        if lrc.exists():
            return lrc.read_text(encoding="utf-8", errors="ignore")
        return ""
