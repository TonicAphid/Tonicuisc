"""搜索缓存测试：用假 client，不联网、不需要 musicdl。"""

from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from tonicuisc_server.service import MusicService
from tonicuisc_server.storage import Storage


def _song(identifier: str, source: str = "MiguMusicClient") -> SimpleNamespace:
    return SimpleNamespace(
        source=source,
        root_source=None,
        song_name=f"歌 {identifier}",
        singers="歌手",
        album="专辑",
        ext="mp3",
        file_size="1 MB",
        file_size_bytes=1024,
        duration="00:03:00",
        duration_s=180,
        lyric=None,
        cover_url=None,
        identifier=identifier,
    )


class _FakeClient:
    """没有 music_clients 结构，走兜底分支。"""

    def __init__(self) -> None:
        self.calls = 0

    def search(self, keyword: str) -> dict:
        self.calls += 1
        return {"MiguMusicClient": [_song("1"), _song("2")]}


class _SourceStub:
    """模拟单个音源的 client。"""

    def __init__(self, source: str) -> None:
        self.source = source
        self.calls = 0

    def search(self, **kwargs) -> list:
        self.calls += 1
        return [_song(f"{self.source[:4]}-1", self.source)]


class _MultiClient:
    """带 music_clients 结构的桩，用来验证「只打选中的音源」。"""

    def __init__(self, sources: tuple[str, ...] = ("MiguMusicClient", "KuwoMusicClient")) -> None:
        self.music_clients = {name: _SourceStub(name) for name in sources}
        self.clients_threadings: dict = {}
        self.requests_overrides: dict = {}
        self.search_rules: dict = {}


@pytest.fixture()
def service() -> MusicService:
    root = Path(__file__).resolve().parent / "_scratch" / uuid.uuid4().hex
    svc = MusicService()
    svc.work_dir = root / "music"
    svc.files_dir = root / "files"
    svc.work_dir.mkdir(parents=True, exist_ok=True)
    svc.files_dir.mkdir(parents=True, exist_ok=True)
    svc.storage.close()
    svc.storage = Storage(root / "test.db")
    svc._client = _FakeClient()
    try:
        yield svc
    finally:
        svc.storage.close()
        shutil.rmtree(root, ignore_errors=True)


def test_song_restored_from_database(service: MusicService) -> None:
    items = service.search("天地龙鳞")
    service._songs.clear()  # 模拟进程重启：内存缓存没了，只剩 SQLite

    song = service.get_song(items[0]["id"])

    assert song.song_name == items[0]["name"]
    assert service.storage.song_meta(items[0]["id"]) is not None


def test_search_records_history(service: MusicService) -> None:
    service.search("天地龙鳞")
    service.search("龙", refresh=True)

    keywords = [row["keyword"] for row in service.history()]
    assert keywords == ["龙", "天地龙鳞"]


def test_same_keyword_hits_cache(service: MusicService) -> None:
    first = service.search("天地龙鳞")
    second = service.search("天地龙鳞")
    assert service._client.calls == 1, "相同关键词不应重复联网搜索"
    assert second == first


def test_cache_returns_copies(service: MusicService) -> None:
    first = service.search("天地龙鳞")
    first.append({"id": "fake"})
    assert len(service.search("天地龙鳞")) == 2, "调用方改返回值不能污染缓存"


def test_refresh_bypasses_cache(service: MusicService) -> None:
    service.search("天地龙鳞")
    service.search("天地龙鳞", refresh=True)
    assert service._client.calls == 2


def test_different_keyword_searches_again(service: MusicService) -> None:
    service.search("天地龙鳞")
    service.search("龙")
    assert service._client.calls == 2


def test_expired_cache_searches_again(service: MusicService) -> None:
    service.search("天地龙鳞")
    key = next(k for k in service._search_cache if k[0] == "天地龙鳞")
    items, _ = service._search_cache[key]
    service._search_cache[key] = (items, time.time() - 1)
    service.search("天地龙鳞")
    assert service._client.calls == 2


def test_only_selected_source_is_requested(service: MusicService) -> None:
    multi = _MultiClient()
    service._client = multi

    items = service.search("天地龙鳞", sources=["kuwo"])

    assert multi.music_clients["KuwoMusicClient"].calls == 1
    assert multi.music_clients["MiguMusicClient"].calls == 0, "没选咪咕就不该请求咪咕"
    assert all(item["source"] == "KuwoMusicClient" for item in items)


def test_both_selected_sources_are_requested(service: MusicService) -> None:
    multi = _MultiClient()
    service._client = multi

    items = service.search("天地龙鳞", sources=["migu", "kuwo"])

    assert multi.music_clients["MiguMusicClient"].calls == 1
    assert multi.music_clients["KuwoMusicClient"].calls == 1
    assert {item["source"] for item in items} == {"MiguMusicClient", "KuwoMusicClient"}


def test_disabled_source_is_rejected(service: MusicService) -> None:
    service._client = _MultiClient(sources=("MiguMusicClient",))
    with pytest.raises(ValueError):
        service.search("天地龙鳞", sources=["kuwo"])
