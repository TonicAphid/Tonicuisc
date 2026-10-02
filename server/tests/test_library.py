"""喜欢 / 收藏 / 播放历史 接口测试。不联网、不需要 musicdl。"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from tonicuisc_server.main import app
from tonicuisc_server.storage import Storage

client = TestClient(app)

DEVICE = "E5F5-A5B5"


def _login(env, username: str = "aphid", password: str = "hunter2x") -> dict[str, str]:
    code = DEVICE
    client.post("/api/device/start", json={"user_code": code, "poll_secret": "library-secret-1", "name": "手机"})
    client.post("/login", data={"action": "register", "user_code": code, "username": username, "password": password})
    key = client.get("/api/device/status", params={"user_code": code, "poll_secret": "library-secret-1"}).json()["api_key"]
    return {"X-API-Key": key}


def _seed_song(env, song_id: str = "MiguMusicClient:1", name: str = "天地龙鳞") -> str:
    """往 songs 表塞一条，列表接口是 JOIN songs 取元信息的。"""
    source = song_id.split(":", 1)[0]
    item = {
        "id": song_id,
        "source": source,
        "source_label": "咪咕" if source == "MiguMusicClient" else "酷我",
        "root_source": "",
        "name": name,
        "singers": "王力宏",
        "album": "专辑",
        "ext": "flac",
        "duration": "00:04:05",
        "duration_s": 245,
        "file_size": "41 MB",
        "cover_url": "https://example.com/c.jpg",
        "has_lyric": True,
    }
    env["service"].storage.upsert_song(item, SimpleNamespace())
    return song_id


def test_library_requires_login(app_env) -> None:
    assert client.get("/api/library/summary").status_code == 401


def test_like_is_idempotent(app_env) -> None:
    headers = _login(app_env)
    song_id = _seed_song(app_env)

    assert client.post("/api/library/like", json={"song_id": song_id}, headers=headers).status_code == 200
    again = client.post("/api/library/like", json={"song_id": song_id}, headers=headers)
    assert again.json()["counts"]["like"] == 1

    summary = client.get("/api/library/summary", headers=headers).json()
    assert summary["counts"]["like"] == 1
    assert summary["counts"]["favorite"] == 0
    assert summary["counts"]["history"] == 0
    assert summary["like_ids"] == [song_id]


def test_favorite_is_separate_from_like(app_env) -> None:
    headers = _login(app_env)
    song_id = _seed_song(app_env)
    client.post("/api/library/like", json={"song_id": song_id}, headers=headers)
    client.post("/api/library/favorite", json={"song_id": song_id}, headers=headers)

    summary = client.get("/api/library/summary", headers=headers).json()
    assert summary["counts"]["like"] == 1 and summary["counts"]["favorite"] == 1
    assert summary["favorite_ids"] == [song_id]


def test_history_counts_plays(app_env) -> None:
    headers = _login(app_env)
    song_id = _seed_song(app_env)

    for _ in range(3):
        client.post("/api/library/history", json={"song_id": song_id}, headers=headers)

    items = client.get("/api/library/history", headers=headers).json()["items"]
    assert len(items) == 1, "同一首歌在历史里只占一条"
    assert items[0]["play_count"] == 3
    assert items[0]["name"] == "天地龙鳞" and items[0]["singers"] == "王力宏"
    assert items[0]["ext"] == "flac"


def test_list_returns_metadata(app_env) -> None:
    headers = _login(app_env)
    song_id = _seed_song(app_env, "KuwoMusicClient:9", "莫问归期")
    client.post("/api/library/favorite", json={"song_id": song_id}, headers=headers)

    body = client.get("/api/library/favorite", headers=headers).json()
    assert body["kind"] == "favorite" and body["total"] == 1
    item = body["items"][0]
    assert item["id"] == song_id
    assert item["source"] == "KuwoMusicClient" and item["name"] == "莫问归期"
    assert item["has_lyric"] is True and isinstance(item["created_at"], float)


def test_remove_and_clear(app_env) -> None:
    headers = _login(app_env)
    song_id = _seed_song(app_env)
    client.post("/api/library/like", json={"song_id": song_id}, headers=headers)

    assert client.delete(f"/api/library/like/{song_id}", headers=headers).status_code == 200
    assert client.delete(f"/api/library/like/{song_id}", headers=headers).status_code == 404
    assert client.get("/api/library/summary", headers=headers).json()["counts"]["like"] == 0

    client.post("/api/library/history", json={"song_id": song_id}, headers=headers)
    cleared = client.delete("/api/library/history", headers=headers).json()
    assert cleared["removed"] == 1


def test_unknown_kind_is_rejected(app_env) -> None:
    headers = _login(app_env)
    assert client.get("/api/library/nonsense", headers=headers).status_code == 400
    assert client.post("/api/library/nonsense", json={"song_id": "a:b"}, headers=headers).status_code == 400


def test_library_is_per_account(app_env) -> None:
    headers_a = _login(app_env)
    song_id = _seed_song(app_env)
    client.post("/api/library/like", json={"song_id": song_id}, headers=headers_a)

    # 第二个账号
    code = "F6F6-B6C6"
    client.post("/api/device/start", json={"user_code": code, "poll_secret": "library-secret-2", "name": "另一台"})
    client.post("/login", data={"action": "register", "user_code": code, "username": "someone", "password": "hunter2x"})
    key_b = client.get("/api/device/status", params={"user_code": code, "poll_secret": "library-secret-2"}).json()["api_key"]

    summary_b = client.get("/api/library/summary", headers={"X-API-Key": key_b}).json()
    assert summary_b["counts"]["like"] == 0, "别人的喜欢不能串到我这里"
    assert summary_b["like_ids"] == []


def test_device_without_account_is_rejected(app_env) -> None:
    """旧版配对流程留下的设备（没有账户）不能用收藏功能。"""
    from tonicuisc_server.auth import hash_key, new_api_key

    key = new_api_key()
    app_env["store"].create_device(name="孤儿设备", key_hash=hash_key(key))
    assert client.get("/api/library/summary", headers={"X-API-Key": key}).status_code == 400
