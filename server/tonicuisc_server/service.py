"""Thin wrapper around musicdl: keyword search + cached downloads.

musicdl is imported lazily so that ``/api/health`` works even when the
(heavy) downloader package is not installed yet.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from .config import SETTINGS, resolve_sources, source_label

#: How long a search result stays addressable through /api/stream/{id}.
ITEM_TTL_SECONDS = 3 * 60 * 60


class SongNotFound(KeyError):
    """Raised when a song id is unknown or expired."""


class DownloadFailed(RuntimeError):
    """Raised when musicdl could not produce a local audio file."""


def _clean(value: Any, default: str = "") -> str:
    text = "" if value is None else str(value).strip()
    return default if text.upper() in {"NULL", "NONE"} else text


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
        self._download_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self.work_dir = SETTINGS.work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)

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
                        }
                        for name in sources
                    },
                )
            return self._client

    # ------------------------------------------------------------------ search
    def search(self, keyword: str, sources: list[str] | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        keyword = keyword.strip()
        if not keyword:
            return []
        wanted = set(resolve_sources(sources)) if sources else set(resolve_sources(SETTINGS.sources))
        results = self.client.search(keyword) or {}
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
        return items

    # ------------------------------------------------------------------ cache
    def _remember(self, song: Any) -> None:
        self._songs[song_to_item(song)["id"]] = (song, time.time() + ITEM_TTL_SECONDS)
        self._purge()

    def _purge(self) -> None:
        now = time.time()
        for key in [k for k, (_, deadline) in self._songs.items() if deadline < now]:
            self._songs.pop(key, None)

    def get_song(self, item_id: str) -> Any:
        entry = self._songs.get(item_id)
        if entry is None or entry[1] < time.time():
            raise SongNotFound(item_id)
        return entry[0]

    def lyric(self, item_id: str) -> str:
        return _clean(self.get_song(item_id).lyric, "")

    def direct_url(self, item_id: str) -> dict[str, Any]:
        song = self.get_song(item_id)
        url = song.download_url
        if not isinstance(url, str) or not url.startswith("http"):
            raise DownloadFailed("该歌曲没有可直接播放的链接")
        return {
            "url": url,
            "headers": dict(song.default_download_headers or {}),
            "ext": _clean(song.ext).lstrip("."),
        }

    # ---------------------------------------------------------------- download
    def _lock_for(self, item_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._download_locks.setdefault(item_id, threading.Lock())

    def ensure_file(self, item_id: str) -> Path:
        """Download the song once and return the local path (safe for threads)."""
        song = self.get_song(item_id)
        path = Path(song.save_path)
        if path.exists() and path.stat().st_size > 0:
            return path
        with self._lock_for(item_id):
            path = Path(song.save_path)
            if path.exists() and path.stat().st_size > 0:
                return path
            try:
                downloaded = self.client.download(song_infos=[song]) or []
            except Exception as exc:  # network / source side failure
                raise DownloadFailed(str(exc)) from exc
            for candidate in downloaded:
                candidate_path = Path(candidate.save_path)
                if candidate_path.exists() and candidate_path.stat().st_size > 0:
                    return candidate_path
            if path.exists() and path.stat().st_size > 0:
                return path
            raise DownloadFailed("下载失败：音源未返回可用文件")
