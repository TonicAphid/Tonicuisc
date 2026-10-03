"""Redis 持久化测试：用 fakeredis，不联网、不需要 musicdl、不需要真的 Redis。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from conftest import make_storage  # noqa: E402  —— 同一个测试目录里的助手
from tonicuisc_server.storage import Storage, song_payload


def _song(identifier: str = "1") -> SimpleNamespace:
    return SimpleNamespace(
        source="MiguMusicClient",
        root_source=None,
        song_name="歌",
        singers="歌手",
        album="专辑",
        ext="mp3",
        file_size="1 MB",
        file_size_bytes=1024,
        duration="00:03:00",
        duration_s=180,
        lyric="[00:01.00]hi",
        cover_url="http://c/1.jpg",
        identifier=identifier,
        download_url="http://cdn/1.mp3",
        download_url_status={"ok": True},
        default_download_headers={"a": "b"},
        default_download_cookies={},
    )


def _item(identifier: str = "1") -> dict:
    return {
        "id": f"MiguMusicClient:{identifier}",
        "source": "MiguMusicClient",
        "source_label": "咪咕",
        "root_source": "",
        "name": "歌",
        "singers": "歌手",
        "album": "专辑",
        "ext": "mp3",
        "duration": "00:03:00",
        "duration_s": 180,
        "file_size": "1 MB",
        "cover_url": "http://c/1.jpg",
        "has_lyric": True,
    }


@pytest.fixture()
def storage() -> Storage:
    store = make_storage()
    try:
        yield store
    finally:
        store.close()


def test_song_roundtrip(storage: Storage) -> None:
    storage.upsert_song(_item(), _song())

    payload = storage.load_song("MiguMusicClient:1")
    assert payload is not None
    assert payload["song_name"] == "歌"
    assert payload["download_url"] == "http://cdn/1.mp3"

    meta = storage.song_meta("MiguMusicClient:1")
    assert meta is not None and meta["source_label"] == "咪咕" and meta["has_lyric"] == 1
    assert storage.count_songs() == 1
    assert not storage.url_expired("MiguMusicClient:1")


def test_url_expires(storage: Storage) -> None:
    storage.upsert_song(_item(), _song(), url_ttl=-1)
    assert storage.url_expired("MiguMusicClient:1")
    assert storage.url_expired("missing:1"), "库里没有的歌也当作过期"


def test_search_history_groups_by_keyword(storage: Storage) -> None:
    storage.record_search("天地龙鳞", ["MiguMusicClient"], ["a:1", "b:2"])
    storage.record_search("天地龙鳞", ["MiguMusicClient"], ["a:1"])
    storage.record_search("龙", ["MiguMusicClient"], ["a:1"])

    history = storage.recent_searches()
    assert [row["keyword"] for row in history] == ["龙", "天地龙鳞"]
    assert history[1]["times"] == 2
    assert history[1]["result_count"] == 2


def test_unknown_song_returns_none(storage: Storage) -> None:
    assert storage.load_song("nope:1") is None
    assert storage.song_meta("nope:1") is None


def test_song_payload_is_json_safe() -> None:
    payload = song_payload(_song())
    assert payload["identifier"] == "1"
    assert "episodes" not in payload and "downloaded_contents" not in payload
