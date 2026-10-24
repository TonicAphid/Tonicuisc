"""`/api/lyric` 的两种格式：默认纯文本、enhanced 结构化逐字。"""

from __future__ import annotations

import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from tonicuisc_server.api import app
from tonicuisc_server.lyrics.models import EnhancedLyrics, LyricsLine, LyricsWord

client = TestClient(app)

CODE = "A1B1-C1D1"
SECRET = "poll-secret-abcdefgh"


def _login() -> str:
    assert client.post("/api/device/start", json={"user_code": CODE, "poll_secret": SECRET, "name": "测试机"}).status_code == 200
    page = client.post(
        "/login",
        data={"action": "register", "user_code": CODE, "username": "lyricuser", "password": "hunter2x"},
    )
    assert page.status_code == 200 and "已批准" in page.text
    return client.post("/api/device/status", json={"user_code": CODE, "poll_secret": SECRET}).json()["api_key"]


def _song() -> SimpleNamespace:
    return SimpleNamespace(
        source="KuwoMusicClient",
        identifier="test-1",
        root_source=None,
        song_name="测试歌",
        singers="测试歌手",
        album="",
        ext="mp3",
        file_size="1 MB",
        file_size_bytes=1024,
        duration="00:01",
        duration_s=1,
        lyric="[00:00.00]原来的逐行歌词",
        cover_url="",
    )


class _FakeProvider:
    name = "fake"

    def fetch(self, song) -> EnhancedLyrics:
        return EnhancedLyrics(
            provider="fake",
            lines=(LyricsLine(0, 1000, (LyricsWord(0, 400, "你"), LyricsWord(400, 800, "好"))),),
        )


def _wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_text_lyric_is_unchanged(app_env) -> None:
    service = app_env["service"]
    service._remember(_song())
    key = _login()

    resp = client.get("/api/lyric/KuwoMusicClient:test-1", headers={"X-API-Key": key})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "原来的逐行歌词" in resp.text


def test_enhanced_lyric_two_phase(app_env) -> None:
    service = app_env["service"]
    service._remember(_song())
    service.lyrics.providers = [_FakeProvider()]
    key = _login()
    headers = {"X-API-Key": key}
    url = "/api/lyric/KuwoMusicClient:test-1?format=enhanced"

    first = client.get(url, headers=headers)
    assert first.status_code == 200
    assert first.json() == {"format": "enhanced", "ready": False}

    assert _wait_for(lambda: service.storage.load_lyrics("KuwoMusicClient:test-1") is not None)
    second = client.get(url, headers=headers)

    body = second.json()
    assert body["ready"] is True
    assert body["provider"] == "fake"
    assert body["lines"][0]["text"] == "你好"
    assert body["lines"][0]["words"][0] == {"s": 0, "e": 400, "t": "你"}


def test_enhanced_lyric_requires_existing_song(app_env) -> None:
    key = _login()
    resp = client.get("/api/lyric/KuwoMusicClient:missing?format=enhanced", headers={"X-API-Key": key})
    assert resp.status_code == 404
