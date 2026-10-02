"""搜索缓存测试：用假 client，不联网、不需要 musicdl。"""

from __future__ import annotations

import dataclasses
import shutil
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

import tonicuisc_server.service as service_module
from tonicuisc_server.service import MusicService
from tonicuisc_server.storage import Storage


def _song(identifier: str, source: str = "MiguMusicClient", cover: str = "") -> SimpleNamespace:
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
        cover_url=cover,
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

    def __init__(self, source: str, singers: str = "歌手", count: int = 1, cover: str = "") -> None:
        self.source = source
        self.singers = singers
        self.count = count
        self.cover = cover
        self.calls = 0
        self.sizes: list[int] = []

    def search(self, **kwargs) -> list:
        self.calls += 1
        self.sizes.append(getattr(self, "search_size_per_source", 0))
        songs = []
        for index in range(self.count):
            song = _song(f"{self.source[:4]}-{index}", self.source, cover=self.cover)
            song.singers = self.singers
            songs.append(song)
        return songs


class _MultiClient:
    """带 music_clients 结构的桩，用来验证「只打选中的音源」。"""

    def __init__(
        self,
        sources: tuple[str, ...] = ("MiguMusicClient", "KuwoMusicClient"),
        singers: str = "歌手",
        count: int = 1,
        cover: str = "",
    ) -> None:
        self.music_clients = {name: _SourceStub(name, singers, count, cover) for name in sources}
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
    items, _, size = service._search_cache[key]
    service._search_cache[key] = (items, time.time() - 1, size)
    service.search("天地龙鳞")
    assert service._client.calls == 2


def test_search_page_returns_disjoint_pages(service: MusicService) -> None:
    service._client = _MultiClient(sources=("KuwoMusicClient",), count=30)

    first = service.search_page("天地龙鳞", sources=["kuwo"], offset=0, limit=15)
    second = service.search_page("天地龙鳞", sources=["kuwo"], offset=15, limit=15)

    assert len(first["items"]) == 15 and first["has_more"] is True
    assert len(second["items"]) == 15
    first_ids = {item["id"] for item in first["items"]}
    second_ids = {item["id"] for item in second["items"]}
    assert not (first_ids & second_ids), "两页不能有重复的歌"

    # 再往后就没有了
    third = service.search_page("天地龙鳞", sources=["kuwo"], offset=30, limit=15)
    assert third["items"] == [] and third["has_more"] is False


def test_search_page_grows_fetch_size(service: MusicService) -> None:
    """翻页时抓取量跟着变大，但请求数保持在并发数附近。"""
    service._client = _MultiClient(sources=("KuwoMusicClient",), count=60)

    service.search_page("天地龙鳞", sources=["kuwo"], offset=0, limit=15)
    service.search_page("天地龙鳞", sources=["kuwo"], offset=15, limit=15)

    stub = service._client.music_clients["KuwoMusicClient"]
    assert stub.sizes[-1] >= 30, "第二页要抓更多"
    assert stub.search_size_per_page >= 1


def test_covers_are_looked_up_on_demand(service: MusicService, monkeypatch) -> None:
    """搜索本身不等封面；封面是客户端另外来问的。"""
    calls: list[str] = []

    def fake_cover(name: str, singers: str = "") -> str:
        calls.append(name)
        return "https://y.gtimg.cn/cover.jpg"

    _enable_qq_cover(monkeypatch, fake_cover)
    service._client = _MultiClient(sources=("KuwoMusicClient",))

    items = service.search("天地龙鳞", sources=["kuwo"])
    assert calls == [], "搜索接口不该等封面"
    assert items[0]["cover_url"] == ""

    found = service.covers([items[0]["id"]])
    assert found[items[0]["id"]] == "https://y.gtimg.cn/cover.jpg"
    assert calls == ["歌 Kuwo-0"]

    # 第二次：库里已经有缓存，不再请求 QQ
    assert service.covers([items[0]["id"]]) == {items[0]["id"]: "https://y.gtimg.cn/cover.jpg"}
    assert calls == ["歌 Kuwo-0"], "缓存过的封面不该重复查"


def test_covers_skip_songs_without_qq_match(service: MusicService, monkeypatch) -> None:
    _enable_qq_cover(monkeypatch, lambda name, singers="": None)
    service._client = _MultiClient(sources=("KuwoMusicClient",))
    items = service.search("天地龙鳞", sources=["kuwo"])

    assert service.covers([items[0]["id"]]) == {}, "没查到就不返回，客户端保持原图"
    assert service.storage.qq_cover_of(items[0]["id"]) == "", "但记下查过了"


def test_covers_disabled_returns_empty(service: MusicService) -> None:
    service._client = _MultiClient(sources=("KuwoMusicClient",))
    items = service.search("天地龙鳞", sources=["kuwo"])
    assert service.covers([items[0]["id"]]) == {}  # conftest 默认关了


def test_cover_hidden_until_qq_lookup(service: MusicService, monkeypatch) -> None:
    """QQ 还没查过时先别显示音源封面（免得列表里先糊一张），查完再兜底。"""
    _enable_qq_cover(monkeypatch, lambda name, singers="": None)  # QQ 查不到
    service._client = _MultiClient(sources=("KuwoMusicClient",), cover="https://kuwo/original.jpg")

    items = service.search("天地龙鳞", sources=["kuwo"])
    assert items[0]["cover_url"] == "", "没查过就先留空"
    assert items[0]["cover_pending"] is True

    # 客户端来问一次：QQ 没有 → 退回音源原图，并记下「查过了」
    found = service.covers([items[0]["id"]])
    assert found[items[0]["id"]] == "https://kuwo/original.jpg"
    assert service.storage.qq_cover_of(items[0]["id"]) == ""

    # 再搜一次：知道 QQ 没有，直接给音源原图
    service._search_cache.clear()
    again = service.search("天地龙鳞", sources=["kuwo"], refresh=True)
    assert again[0]["cover_url"] == "https://kuwo/original.jpg"


def test_cover_prefers_qq_when_available(service: MusicService, monkeypatch) -> None:
    _enable_qq_cover(monkeypatch, lambda name, singers="": "https://qq/cover.jpg")
    service._client = _MultiClient(sources=("KuwoMusicClient",), cover="https://kuwo/original.jpg")

    items = service.search("天地龙鳞", sources=["kuwo"])
    assert items[0]["cover_url"] == "", "查之前先空着"

    service.covers([items[0]["id"]])
    service._search_cache.clear()
    again = service.search("天地龙鳞", sources=["kuwo"], refresh=True)
    assert again[0]["cover_url"] == "https://qq/cover.jpg"


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


# ------------------------------------------------------------------ QQ 封面
def _enable_qq_cover(monkeypatch, fake):
    monkeypatch.setattr(
        service_module, "SETTINGS", dataclasses.replace(service_module.SETTINGS, qq_cover=True)
    )
    monkeypatch.setattr(service_module, "qq_cover", fake)


# ------------------------------------------------------------------ 歌手页
def test_artist_keeps_only_matching_singers(service: MusicService) -> None:
    service._client = _MultiClient(sources=("KuwoMusicClient",), singers="周杰伦")

    result = service.artist("周杰伦", sources=["kuwo"])
    assert result["filtered"] is True
    assert result["total"] == 1 and result["items"][0]["singers"] == "周杰伦"


def test_artist_falls_back_when_nothing_matches(service: MusicService) -> None:
    service._client = _MultiClient(sources=("KuwoMusicClient",), singers="别人")

    result = service.artist("周杰伦", sources=["kuwo"])
    assert result["filtered"] is False, "一首都没匹配上就退回原始结果"
    assert result["total"] == 1


def test_artist_without_name(service: MusicService) -> None:
    result = service.artist("   ")
    assert result["total"] == 0 and result["filtered"] is False
