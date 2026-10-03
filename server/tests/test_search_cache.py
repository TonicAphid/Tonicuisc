"""搜索缓存测试：用假 client + fakeredis，不联网、不需要 musicdl。"""

from __future__ import annotations

import dataclasses
import shutil
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

import tonicuisc_server.artwork as artwork_module
from conftest import make_storage
from tonicuisc_server.config import SETTINGS
from tonicuisc_server.items import song_to_item
from tonicuisc_server.service import MusicService


def _wait_for(predicate, timeout: float = 5.0, interval: float = 0.01) -> bool:
    """等后台线程干完活（测试里替代 sleep 猜时间）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


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


class _SlowClient(_FakeClient):
    """搜得慢一点，用来验证「同一个关键词只跑一个后台刷新」。"""

    def search(self, keyword: str) -> dict:
        time.sleep(0.2)
        return super().search(keyword)


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
    svc.storage = make_storage()
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


def test_refresh_uses_cache_when_fresh(service: MusicService) -> None:
    """App 每次都发 refresh=true：缓存还新鲜就直接返回，不阻塞等联网。

    （以前是「refresh 就一定重搜」，App 每次搜索都带 refresh=true → 同一个词每次都联网 5 秒。）
    """
    service.search("天地龙鳞")
    assert service._client.calls == 1

    started = time.perf_counter()
    items = service.search("天地龙鳞", refresh=True)
    elapsed = time.perf_counter() - started

    assert len(items) == 2
    assert elapsed < 1.0, "软刷新不该等联网"
    assert _wait_for(lambda: service._client.calls >= 2), "后台应该偷偷刷新缓存"


def test_refresh_searches_when_cache_is_stale(service: MusicService, monkeypatch) -> None:
    """缓存超过软刷新窗口就得真去搜（阻塞）。"""
    service.search("天地龙鳞")
    # 直接把「缓存年龄」说成很大：等价于这条缓存已经超过 TONICUISC_SEARCH_SOFT_TTL
    monkeypatch.setattr(service.storage, "search_cache_age", lambda key: 99999.0)

    service.search("天地龙鳞", refresh=True)

    assert service._client.calls == 2


def test_background_revalidate_only_runs_once(service: MusicService) -> None:
    """同一个关键词同时只跑一个后台刷新（App 狂点搜索不会打出十个线程）。"""
    service._client = _SlowClient()
    service.search("天地龙鳞")  # 同步搜一次
    for _ in range(3):
        service.search("天地龙鳞", refresh=True)  # 都命中缓存，只该起一个后台任务

    assert _wait_for(lambda: service._client.calls >= 2, timeout=5)
    time.sleep(0.4)
    assert service._client.calls == 2, "同一关键词只该有一个后台刷新"


def test_different_keyword_searches_again(service: MusicService) -> None:
    service.search("天地龙鳞")
    service.search("龙")
    assert service._client.calls == 2


def test_first_page_accepts_short_cache(service: MusicService, monkeypatch) -> None:
    """历史导入回来的关键词可能只有几条：首页先给这几条，后台再补全。"""
    key = ("天地龙鳞", ("MiguMusicClient",))
    service.storage.save_search_cache(key, [song_to_item(_song(f"old-{i}")) for i in range(3)])

    calls: list[str] = []

    def fake_sources(keyword, wanted, limit):
        calls.append(keyword)  # 只有后台刷新会走到这里
        time.sleep(0.3)
        return [song_to_item(_song(f"new-{i}")) for i in range(15)]

    monkeypatch.setattr(service, "_search_sources", fake_sources)

    started = time.perf_counter()
    page = service.search_page("天地龙鳞", sources=["migu"], offset=0, limit=15)
    elapsed = time.perf_counter() - started

    assert elapsed < 0.2, "首页不该为了凑满一页去等联网"
    assert [item["id"] for item in page["items"]] == [f"MiguMusicClient:old-{i}" for i in range(3)]

    assert _wait_for(lambda: bool(calls)), "后台应该去补全"
    assert _wait_for(lambda: len(service.storage.load_search_cache(key) or []) == 15), "补全后缓存要更新"


def test_paging_never_uses_short_cache(service: MusicService) -> None:
    """翻页时缓存不够必须真去搜，不然那一页是空的。"""
    key = ("天地龙鳞", ("MiguMusicClient",))
    service.storage.save_search_cache(key, [song_to_item(_song(f"old-{i}")) for i in range(3)])
    service._client = _MultiClient(sources=("MiguMusicClient",), count=30)

    page = service.search_page("天地龙鳞", sources=["migu"], offset=15, limit=15)

    assert service._client.music_clients["MiguMusicClient"].calls == 1, "翻页要真去搜"
    assert len(page["items"]) == 15


def test_search_cache_survives_restart(service: MusicService) -> None:
    """搜索结果存在 Redis 里：换一个 MusicService 实例（≈服务重启）也直接命中。"""
    service._client = _MultiClient(sources=("KuwoMusicClient",), count=30)
    service.search("天地龙鳞", sources=["kuwo"])

    restarted = MusicService()
    restarted.storage.close()
    restarted.storage = service.storage  # 同一个 Redis：数据都还在
    restarted._client = _MultiClient(sources=("KuwoMusicClient",), count=30)

    items = restarted.search("天地龙鳞", sources=["kuwo"])

    assert len(items) == 30
    assert restarted._client.music_clients["KuwoMusicClient"].calls == 0, "重启后不该重新联网搜"


def test_search_cache_expiry_searches_again(service: MusicService) -> None:
    """缓存过期（TTL 到期）后要重新联网搜。"""
    service._client = _MultiClient(sources=("KuwoMusicClient",), count=30)
    service.search("天地龙鳞", sources=["kuwo"])
    assert service._client.music_clients["KuwoMusicClient"].calls == 1

    # 手动把这条缓存踹掉，等价于「TTL 到期」
    assert service.storage.drop_search_cache() == 1
    service.search("天地龙鳞", sources=["kuwo"])
    assert service._client.music_clients["KuwoMusicClient"].calls == 2


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
    """翻页时抓取量跟着变大。"""
    service._client = _MultiClient(sources=("KuwoMusicClient",), count=60)

    service.search_page("天地龙鳞", sources=["kuwo"], offset=0, limit=15)
    service.search_page("天地龙鳞", sources=["kuwo"], offset=15, limit=15)

    stub = service._client.music_clients["KuwoMusicClient"]
    assert stub.sizes[-1] >= 30, "第二页要抓更多"


def test_fetch_size_is_split_across_threads(service: MusicService) -> None:
    """拆成 search_threads 个请求并行拿，**不要**一个请求拉满。

    musicdl 一个请求内部是串行解析每首歌的，一次拉满 15 条会串行跑 15 首
    （实测 18~23s），拆成 5 个请求各 3 条只要 ~5s。
    """
    service._client = _MultiClient(sources=("KuwoMusicClient",), count=30)

    service.search_page("天地龙鳞", sources=["kuwo"], offset=0, limit=15)

    stub = service._client.music_clients["KuwoMusicClient"]
    assert stub.sizes[-1] == 15
    expected = -(-15 // SETTINGS.search_threads)
    assert stub.search_size_per_page == expected, "每页条数 = ceil(要几条 / 并发数)"
    assert stub.search_size_per_page < stub.sizes[-1], "两个值相等就退化成「1 个请求拉满」了"


def test_fetch_is_split_across_sources(service: MusicService) -> None:
    """开了两个音源就每个只拉一半，不再各自都拉满 15 条。"""
    service._client = _MultiClient(sources=("MiguMusicClient", "KuwoMusicClient"), count=30)

    service.search_page("天地龙鳞", sources=["migu", "kuwo"], offset=0, limit=15)

    for stub in service._client.music_clients.values():
        assert stub.sizes[-1] <= 8, "15 条分给 2 个音源，每个最多 8 条"


def test_search_warms_covers_without_waiting(service: MusicService, monkeypatch) -> None:
    """搜索**不等**封面（不阻塞），但会在后台把这批歌的 QQ 封面查好写进 Redis。"""
    calls: list[str] = []

    def fake_cover(name: str, singers: str = "") -> str:
        calls.append(name)
        return "https://qq/cover.jpg"

    _enable_qq_cover(monkeypatch, fake_cover)
    service._client = _MultiClient(sources=("KuwoMusicClient",), cover="https://kuwo/original.jpg")

    started = time.perf_counter()
    items = service.search("天地龙鳞", sources=["kuwo"])
    elapsed = time.perf_counter() - started

    assert elapsed < 1.0, "搜索不该等封面"
    assert items[0]["cover_url"] == "https://kuwo/original.jpg", "还没查到时先给音源原图"
    assert _wait_for(lambda: service.storage.qq_cover_of(items[0]["id"]) is not None), "后台要落库"

    # 再搜同一个词：这次搜索结果里直接就是 QQ 封面（App 连 /api/covers 都不用等）
    assert service.storage.drop_search_cache() == 1
    service._client = _MultiClient(sources=("KuwoMusicClient",), cover="https://kuwo/original.jpg")
    again = service.search("天地龙鳞", sources=["kuwo"])
    assert again[0]["cover_url"] == "https://qq/cover.jpg", "查过的封面要直接写进搜索结果"


def test_covers_endpoint_reuses_background_job(service: MusicService, monkeypatch) -> None:
    """`/api/covers` 和后台预热共用同一批任务：同一首歌只问 QQ 一次。"""
    calls: list[str] = []

    def fake_cover(name: str, singers: str = "") -> str:
        calls.append(name)
        return "https://y.gtimg.cn/cover.jpg"

    _enable_qq_cover(monkeypatch, fake_cover)
    service._client = _MultiClient(sources=("KuwoMusicClient",))

    items = service.search("天地龙鳞", sources=["kuwo"])
    found = service.covers([items[0]["id"]])
    assert found[items[0]["id"]] == "https://y.gtimg.cn/cover.jpg"
    assert calls == ["歌 Kuwo-0"], "搜索预热已经排过队了，这里不该再问一遍"

    # 第二次：库里已经有缓存，不再请求 QQ
    service.covers([items[0]["id"]])
    assert calls == ["歌 Kuwo-0"], "已经缓存了，不该重复查"


def test_covers_skip_songs_without_qq_match(service: MusicService, monkeypatch) -> None:
    _enable_qq_cover(monkeypatch, lambda name, singers="": None)
    service._client = _MultiClient(sources=("KuwoMusicClient",))
    items = service.search("天地龙鳞", sources=["kuwo"])

    assert _wait_for(lambda: service.storage.qq_cover_of(items[0]["id"]) is not None)
    assert service.covers([items[0]["id"]]) == {}, "没查到就不返回，客户端保持原图"
    assert service.storage.qq_cover_of(items[0]["id"]) == "", "但记下查过了"


def test_covers_disabled_returns_empty(service: MusicService) -> None:
    service._client = _MultiClient(sources=("KuwoMusicClient",))
    items = service.search("天地龙鳞", sources=["kuwo"])
    assert service.covers([items[0]["id"]]) == {}  # conftest 默认关了
    assert service.cover_inflight() == 0, "关掉封面就不该有任何后台任务"


def test_covers_deadline_falls_back_to_source_cover(service: MusicService, monkeypatch) -> None:
    """封面查得太慢时先给音源原图，绝不让 /api/covers 把 App 吊死。"""
    release = threading.Event()

    def slow_cover(name: str, singers: str = "") -> str:
        release.wait(5)
        return "https://qq/slow.jpg"

    _enable_qq_cover(monkeypatch, slow_cover)
    monkeypatch.setattr(
        artwork_module, "SETTINGS", dataclasses.replace(artwork_module.SETTINGS, qq_cover=True, qq_cover_wait=0.2)
    )
    service._client = _MultiClient(sources=("KuwoMusicClient",), cover="https://kuwo/original.jpg")

    items = service.search("天地龙鳞", sources=["kuwo"])
    started = time.perf_counter()
    found = service.covers([items[0]["id"]])
    elapsed = time.perf_counter() - started
    release.set()

    assert elapsed < 2.0, "到点了就该返回，不能一直等"
    assert found[items[0]["id"]] == "https://kuwo/original.jpg", "等不到就用音源原图"


def test_cover_falls_back_to_source_cover(service: MusicService, monkeypatch) -> None:
    _enable_qq_cover(monkeypatch, lambda name, singers="": None)
    service._client = _MultiClient(sources=("KuwoMusicClient",), cover="https://kuwo/original.jpg")

    items = service.search("天地龙鳞", sources=["kuwo"])
    _wait_for(lambda: service.storage.qq_cover_of(items[0]["id"]) is not None)
    found = service.covers([items[0]["id"]])
    assert found[items[0]["id"]] == "https://kuwo/original.jpg", "QQ 没有就用音源原图"
    assert service.storage.qq_cover_of(items[0]["id"]) == ""


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
    monkeypatch.setattr(artwork_module, "SETTINGS", dataclasses.replace(artwork_module.SETTINGS, qq_cover=True))
    monkeypatch.setattr(artwork_module, "qq_cover", fake)


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
