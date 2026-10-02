"""接口测试：设备码登录 + API Key 校验。不联网、不需要 musicdl。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tonicuisc_server.main import app

client = TestClient(app)

CODE = "A1B1-C1D1"
SECRET = "poll-secret-abcdefgh"


def _start(code: str = CODE, secret: str = SECRET, name: str = "测试机"):
    return client.post("/api/device/start", json={"user_code": code, "poll_secret": secret, "name": name})


def _status(code: str = CODE, secret: str = SECRET) -> dict:
    resp = client.get("/api/device/status", params={"user_code": code, "poll_secret": secret})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _web(form: dict) -> str:
    resp = client.post("/login", data=form)
    assert resp.status_code == 200, resp.text
    return resp.text


def _login_via_web(code: str = CODE, username: str = "aphid", password: str = "hunter2x") -> None:
    _start(code=code)
    assert "设备码" in _web({"action": "lookup", "user_code": code})
    assert "已批准" in _web({"action": "register", "user_code": code, "username": username, "password": password})


def test_health_is_public(app_env) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["auth"] == "enabled"


def test_api_requires_key(app_env) -> None:
    for path in ("/api/sources", "/api/history", "/api/devices", "/api/me"):
        assert client.get(path).status_code == 401, path


def test_login_page_is_public(app_env) -> None:
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "设备码" in resp.text
    assert resp.headers["cache-control"] == "no-store"


def test_full_device_login_flow(app_env) -> None:
    started = _start()
    assert started.status_code == 200
    body = started.json()
    assert body["user_code"] == CODE and body["login_path"] == "/login"
    assert body["expires_in"] > 0

    assert _status()["status"] == "pending"

    # 登录页：先输设备码，看到注册表单
    assert "注册账户" in _web({"action": "lookup", "user_code": "a1b1c1d1"})  # 手输大小写、无横杠也能认

    # 注册并批准
    assert "已批准" in _web({"action": "register", "user_code": CODE, "username": "aphid", "password": "hunter2x"})

    approved = _status()
    assert approved["status"] == "approved"
    assert approved["username"] == "aphid"
    api_key = approved["api_key"]
    assert len(api_key) >= 32

    # key 只能取一次
    assert _status()["status"] == "claimed"

    headers = {"X-API-Key": api_key}
    assert client.get("/api/sources", headers=headers).status_code == 200
    me = client.get("/api/me", headers=headers).json()
    assert me["username"] == "aphid" and me["device_name"] == "测试机"


def test_existing_account_flow(app_env) -> None:
    _login_via_web(username="aphid", password="hunter2x")
    client.get("/api/device/status", params={"user_code": CODE, "poll_secret": SECRET})

    second = "B2C2-D2E2"
    _start(code=second, name="第二台")
    page = _web({"action": "lookup", "user_code": second})
    assert "选择账户" in page and "aphid" in page

    # 密码错
    wrong = _web({"action": "approve_existing", "user_code": second, "username": "aphid", "password": "bad"})
    assert "用户名或密码不对" in wrong

    ok = _web({"action": "approve_existing", "user_code": second, "username": "aphid", "password": "hunter2x"})
    assert "已批准" in ok
    assert _status(second)["username"] == "aphid"


def test_wrong_poll_secret_gets_nothing(app_env) -> None:
    _start()
    assert _status(secret="another-secret-value")["status"] == "expired"


def test_device_code_collision_is_rejected(app_env) -> None:
    assert _start().status_code == 200
    assert _start(name="撞码的").status_code == 409


def test_expired_code_cannot_be_approved(app_env) -> None:
    _start()
    app_env["auth"].ttl = -1  # 只在生成时用，这里直接把已有请求改成过期
    request = app_env["store"].get_device_request(CODE)
    app_env["store"]._conn.execute("UPDATE device_requests SET expires_at = 0 WHERE user_code = ?", (CODE,))
    app_env["store"]._conn.commit()
    assert request is not None

    page = _web({"action": "lookup", "user_code": CODE})
    assert "不存在或已过期" in page


def test_revoked_device_is_locked_out(app_env) -> None:
    _login_via_web()
    api_key = _status()["api_key"]
    headers = {"X-API-Key": api_key}

    devices = client.get("/api/devices", headers=headers).json()["items"]
    assert devices and devices[0]["current"] is True
    assert devices[0]["username"] == "aphid"

    device_id = devices[0]["id"]
    assert client.delete(f"/api/devices/{device_id}", headers=headers).status_code == 200
    assert client.get("/api/sources", headers=headers).status_code == 401


def test_search_validation_after_auth(app_env) -> None:
    _login_via_web()
    headers = {"X-API-Key": _status()["api_key"]}
    assert client.get("/api/search", params={"keyword": ""}, headers=headers).status_code == 422
    assert client.get("/api/lyric/migu:0", headers=headers).status_code == 404
