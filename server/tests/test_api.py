"""Smoke tests: 不依赖网络，也不需要 musicdl 真正联网。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tonicuisc_server.main import app

client = TestClient(app)


def test_health() -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_sources() -> None:
    resp = client.get("/api/sources")
    assert resp.status_code == 200
    aliases = {item["alias"] for item in resp.json()["items"]}
    assert {"migu", "kuwo"} <= aliases


def test_search_rejects_empty_keyword() -> None:
    assert client.get("/api/search", params={"keyword": ""}).status_code == 422


def test_unknown_song() -> None:
    assert client.get("/api/lyric/migu:0").status_code == 404
