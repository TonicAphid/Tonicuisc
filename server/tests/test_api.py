"""接口测试：配对 + API Key 校验。不联网、不需要 musicdl。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tonicuisc_server.main import app

client = TestClient(app)


def _pair(env, name: str = "测试机") -> dict[str, str]:
    resp = client.post("/api/pair", json={"code": env["code"], "name": name})
    assert resp.status_code == 200, resp.text
    return {"X-API-Key": resp.json()["api_key"]}


def test_health_is_public(app_env) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["auth"] == "enabled"


def test_api_requires_key(app_env) -> None:
    assert client.get("/api/sources").status_code == 401
    assert client.get("/api/history").status_code == 401
    assert client.get("/api/devices").status_code == 401


def test_pair_then_access(app_env) -> None:
    headers = _pair(app_env)
    assert client.get("/api/sources", headers=headers).status_code == 200
    assert client.get("/api/history", headers=headers).status_code == 200


def test_pairing_code_is_one_time(app_env) -> None:
    _pair(app_env, "第一台")
    again = client.post("/api/pair", json={"code": app_env["code"], "name": "第二台"})
    assert again.status_code == 401


def test_wrong_pairing_code_is_rejected(app_env) -> None:
    wrong = "999999" if app_env["code"] != "999999" else "111111"
    assert client.post("/api/pair", json={"code": wrong, "name": "陌生人"}).status_code == 401


def test_random_key_differs_per_device(app_env) -> None:
    first = client.post("/api/pair", json={"code": app_env["code"], "name": "A"}).json()["api_key"]
    code, _ = app_env["auth"].current_code()
    second = client.post("/api/pair", json={"code": code, "name": "B"}).json()["api_key"]
    assert first != second
    assert len(first) >= 32


def test_key_is_not_stored_in_plaintext(app_env) -> None:
    key = client.post("/api/pair", json={"code": app_env["code"], "name": "A"}).json()["api_key"]
    assert key.encode() not in (app_env["root"] / "test.db").read_bytes(), "数据库里不该出现明文 key"


def test_revoked_device_is_locked_out(app_env) -> None:
    headers = _pair(app_env)
    devices = client.get("/api/devices", headers=headers).json()["items"]
    assert devices and devices[0]["current"] is True

    device_id = devices[0]["id"]
    assert client.delete(f"/api/devices/{device_id}", headers=headers).status_code == 200
    assert client.get("/api/sources", headers=headers).status_code == 401
    assert client.delete(f"/api/devices/{device_id}", headers=headers).status_code == 401


def test_search_validation_after_auth(app_env) -> None:
    headers = _pair(app_env)
    assert client.get("/api/search", params={"keyword": ""}, headers=headers).status_code == 422
    assert client.get("/api/lyric/migu:0", headers=headers).status_code == 404
