"""针对「审查修复」的回归用例：不联网、不需要 musicdl、不需要真的 Redis。

覆盖这一轮改过的点：Range 解析、鉴权关闭时的收藏接口、设备归属、封面失败不落库、
分页 has_more、裁剪保护、限速器封顶、音源别名解析、缓存 TTL=0、收藏校验、登录页头。
"""

from __future__ import annotations

import dataclasses
import shutil
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import tonicuisc_server.artwork as artwork_module
import tonicuisc_server.storage as storage_module
from conftest import make_storage
from tonicuisc_server.config import resolve_sources
from tonicuisc_server.items import song_to_item
from tonicuisc_server.main import _parse_range, app
from tonicuisc_server.qqmusic import CoverLookupError, _is_same_song
from tonicuisc_server.ratelimit import SlidingWindowLimiter
from tonicuisc_server.service import MusicService

client = TestClient(app)


@pytest.fixture()
def storage():
    store = make_storage()
    try:
        yield store
    finally:
        store.close()


@pytest.fixture()
def service() -> MusicService:
    """临时目录 + fakeredis 的 MusicService（和 test_service_cache 一样的套路）。"""
    root = Path(__file__).resolve().parent / "_scratch" / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    svc = MusicService()
    svc.work_dir = root / "music"
    svc.files_dir = root / "files"
    svc.work_dir.mkdir(parents=True, exist_ok=True)
    svc.files_dir.mkdir(parents=True, exist_ok=True)
    svc.storage.close()
    svc.storage = make_storage()
    try:
        yield svc
    finally:
        svc.storage.close()
        shutil.rmtree(root, ignore_errors=True)


def _song(identifier: str, source: str = "MiguMusicClient") -> SimpleNamespace:
    return SimpleNamespace(
        source=source,
        root_source=None,
        song_name=f"歌{identifier}",
        singers="歌手",
        album="专辑",
        ext="mp3",
        file_size="1 MB",
        file_size_bytes=1024,
        duration="00:03:00",
        duration_s=180,
        lyric=None,
        cover_url="",
        identifier=identifier,
    )


# --------------------------------------------------------------------- Range
def test_range_ignores_garbage_header() -> None:
    """非法 Range 当没带（RFC 7233），不能让 int() 炸成 500。"""
    assert _parse_range("bytes=abc", 100) is None
    assert _parse_range("bytes=0-xyz", 100) is None
    assert _parse_range("bytes=-xyz", 100) is None
    assert _parse_range("bytes=", 100) is None
    assert _parse_range(None, 100) is None


def test_range_rejects_inverted_bounds() -> None:
    """start > end 会算出负的 Content-Length → 416，不能回 206。"""
    with pytest.raises(HTTPException) as exc:
        _parse_range("bytes=5-3", 100)
    assert exc.value.status_code == 416


def test_range_beyond_file_is_416() -> None:
    with pytest.raises(HTTPException) as exc:
        _parse_range("bytes=999-1000", 100)
    assert exc.value.status_code == 416


def test_range_valid_values() -> None:
    assert _parse_range("bytes=0-4", 100) == (0, 4)
    assert _parse_range("bytes=90-", 100) == (90, 99)
    assert _parse_range("bytes=-4", 100) == (96, 99)
    assert _parse_range("bytes=0-9999", 100) == (0, 99)  # 结尾超出文件长度时截断


# --------------------------------------------------------- 鉴权关闭时的收藏
def test_library_works_when_auth_disabled(app_env) -> None:
    """TONICUISC_AUTH=0 时中间件不写 request.state.device，
    以前收藏类接口会一律 400「这台设备没有关联账户」。"""
    app_env["auth"].enabled = False
    app_env["service"].storage.upsert_song({"id": "MiguMusicClient:1", "source": "MiguMusicClient", "ext": "mp3"}, SimpleNamespace())

    resp = client.post("/api/library/like", json={"song_id": "MiguMusicClient:1"})
    assert resp.status_code == 200, resp.text

    summary = client.get("/api/library/summary")
    assert summary.status_code == 200, summary.text
    assert summary.json()["counts"]["like"] == 1


def test_library_still_requires_account_when_auth_on(app_env) -> None:
    """鉴权开着、设备又没绑账户时，还是要 400（孤儿设备不能用收藏）。"""
    from tonicuisc_server.auth import hash_key, new_api_key

    key = new_api_key()
    app_env["store"].create_device(name="孤儿设备", key_hash=hash_key(key))
    assert client.get("/api/library/summary", headers={"X-API-Key": key}).status_code == 400


def test_library_add_unknown_song_is_404(app_env) -> None:
    """收一首库里没有的歌，会让 counts 和条数永远对不上。"""
    from tonicuisc_server.auth import hash_key, new_api_key

    key = new_api_key()
    app_env["store"].create_device(name="手机", key_hash=hash_key(key), user_id="u1")

    resp = client.post("/api/library/like", json={"song_id": "MiguMusicClient:never-seen"}, headers={"X-API-Key": key})
    assert resp.status_code == 404, resp.text


# ----------------------------------------------------------------- 设备归属
def _register(code: str, username: str, secret: str) -> dict[str, str]:
    client.post("/api/device/start", json={"user_code": code, "poll_secret": secret, "name": "手机"})
    client.post("/login", data={"action": "register", "user_code": code, "username": username, "password": "hunter2x"})
    key = client.get("/api/device/status", params={"user_code": code, "poll_secret": secret}).json()["api_key"]
    return {"X-API-Key": key}


def test_devices_are_scoped_to_own_account(app_env) -> None:
    """任一台配对成功的设备都不该看到别人的设备（以前连 username 都带出去）。"""
    headers_a = _register("A1A1-A1A1", "alice", "secret-a")
    headers_b = _register("B2B2-B2B2", "bob", "secret-b")

    items_a = client.get("/api/devices", headers=headers_a).json()["items"]
    items_b = client.get("/api/devices", headers=headers_b).json()["items"]
    ids_a = {item["id"] for item in items_a}
    ids_b = {item["id"] for item in items_b}

    assert ids_a and ids_b
    assert not (ids_a & ids_b), "两个账户的设备列表不该有交集"
    assert all(item["username"] == "alice" for item in items_a)


def test_cannot_revoke_other_accounts_device(app_env) -> None:
    headers_a = _register("A1A1-A1A1", "alice", "secret-a")
    headers_b = _register("B2B2-B2B2", "bob", "secret-b")

    victim = client.get("/api/devices", headers=headers_a).json()["items"][0]["id"]
    assert client.delete(f"/api/devices/{victim}", headers=headers_b).status_code == 404
    assert client.delete(f"/api/devices/{victim}", headers=headers_a).status_code == 200


# ----------------------------------------------------------------- 封面失败
def test_network_failure_does_not_get_cached(service, monkeypatch) -> None:
    """QQ 掉线那一次绝不能记成「查过、没有」，否则这首歌永远只剩糊图。"""
    service.storage.upsert_song({"id": "MiguMusicClient:cover", "source": "MiguMusicClient"}, SimpleNamespace())

    def boom(name: str, singers: str = "", timeout: float = 6.0) -> str | None:
        raise CoverLookupError("boom")

    monkeypatch.setattr(artwork_module, "qq_cover", boom)
    assert service._lookup_cover("MiguMusicClient:cover", "歌", "歌手") is None
    assert service.storage.qq_cover_of("MiguMusicClient:cover") is None, "网络失败不能写缓存"

    # 「确实没有」才落库成空串
    monkeypatch.setattr(artwork_module, "qq_cover", lambda name, singers="", timeout=6.0: None)
    assert service._lookup_cover("MiguMusicClient:cover", "歌", "歌手") is None
    assert service.storage.qq_cover_of("MiguMusicClient:cover") == ""


def test_song_without_singer_cannot_match() -> None:
    """QQ 结果没歌手字段时（got_singers == ''），歌手校验不能算通过。"""
    song = {"songname": "晴天", "singer": [], "albummid": "m1"}
    assert not _is_same_song(song, "晴天", "周杰伦")


# --------------------------------------------------------------------- 分页
def test_short_first_page_still_reports_has_more(service, monkeypatch) -> None:
    """首页为了少等允许缓存不足就返回，但 has_more 不能因此变成 False。"""
    canned = [song_to_item(_song(str(i))) for i in range(15)]
    # 短缓存会触发后台补全，必须 fake：不然这条用例就在后台真联网了
    monkeypatch.setattr(service, "_search_sources", lambda *args, **kwargs: canned)

    key = ("老歌", ("MiguMusicClient",))
    service.storage.save_search_cache(key, [song_to_item(_song(str(i))) for i in range(3)])

    page = service.search_page("老歌", sources=["migu"], offset=0, limit=15)
    assert len(page["items"]) == 3
    assert page["has_more"] is True, "短缓存也还能继续翻（后台会补全）"


# ------------------------------------------------------------------ 裁剪保护
def _seed_songs(store, count: int) -> None:
    for index in range(count):
        store.upsert_song({"id": f"s:{index}", "source": "s", "name": f"n{index}"}, SimpleNamespace())


def test_prune_skips_when_references_could_not_be_read(storage) -> None:
    """引用清单读不全就别裁：以前会当成「没人引用」，把收藏里的歌删光。"""
    _seed_songs(storage, 5)
    storage.add_to_library("user-1", "like", "s:0")

    storage._referenced_song_ids = lambda: None  # type: ignore[method-assign]
    assert storage.prune_songs(keep=2) == 0, "读不到引用清单时必须跳过这轮裁剪"
    assert storage.count_songs() == 5


def test_prune_keeps_referenced_songs(storage) -> None:
    _seed_songs(storage, 5)
    storage.add_to_library("user-1", "like", "s:0")

    # keep=2 → 最老的 3 首是候选，其中被喜欢的 s:0 必须活下来
    storage.prune_songs(keep=2)
    assert storage.known_song("s:0"), "被喜欢的歌不能被裁掉"
    assert storage.count_songs() == 3, "没被引用的 2 首要裁掉"


# ------------------------------------------------------------------- 限速器
def test_limiter_key_count_is_bounded() -> None:
    limiter = SlidingWindowLimiter(limit=5, window=60)
    for index in range(SlidingWindowLimiter.max_keys + 500):
        limiter.hit(f"ip|random-user-{index}")
    assert len(limiter._hits) <= SlidingWindowLimiter.max_keys


def test_limiter_check_blocks_at_limit() -> None:
    limiter = SlidingWindowLimiter(limit=3, window=60)
    assert limiter.check("k") == (True, 0)
    assert limiter.check("k") == (True, 0)
    assert limiter.check("k") == (True, 0)
    allowed, retry_after = limiter.check("k")
    assert allowed is False and retry_after >= 1


# -------------------------------------------------------------- 音源别名解析
def test_resolve_sources_accepts_class_names() -> None:
    assert resolve_sources(["migu", "KuwoMusicClient"]) == ["MiguMusicClient", "KuwoMusicClient"]
    with pytest.raises(ValueError):
        resolve_sources(["netease"])


# ------------------------------------------------------------- 缓存 TTL = 0
def test_search_ttl_zero_means_no_cache(storage, monkeypatch) -> None:
    """TTL=0 是「不缓存」：以前会写成永不过期的键，结果被当成永远新鲜。"""
    monkeypatch.setattr(storage_module, "SETTINGS", dataclasses.replace(storage_module.SETTINGS, search_ttl=0))
    key = ("词", ("MiguMusicClient",))
    storage.save_search_cache(key, [{"id": "a:b"}])

    assert storage.load_search_cache(key) is None
    assert storage.search_cache_age(key) is None


# ----------------------------------------------------------------- 登录页头
def test_login_page_cannot_be_framed() -> None:
    resp = client.get("/login")
    assert resp.status_code == 200
    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert "frame-ancestors 'none'" in resp.headers.get("Content-Security-Policy", "")
