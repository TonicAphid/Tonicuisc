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
    assert "登录账户" in _web({"action": "lookup", "user_code": code})
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


def test_login_page_does_not_list_accounts(app_env) -> None:
    """登录页不能把服务器上的账户列出来。"""
    _login_via_web(username="aphid", password="hunter2x")
    code = "C3D3-E3F3"
    _start(code=code)
    page = _web({"action": "lookup", "user_code": code})
    assert 'type="radio"' not in page
    assert 'name="username" type="text"' in page
    assert "注册新账户" in page

def test_full_device_login_flow(app_env) -> None:
    started = _start()
    assert started.status_code == 200
    body = started.json()
    assert body["user_code"] == CODE and body["login_path"] == "/login"
    assert body["expires_in"] > 0

    assert _status()["status"] == "pending"

    # 登录页：先输设备码，看不到任何已有账户名，用户名靠自己填
    page = _web({"action": "lookup", "user_code": "a1b1c1d1"})  # 手输大小写、无横杠也能认
    assert "登录账户" in page and "注册新账户" in page
    assert 'type="radio"' not in page and "aphid" not in page

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
    assert "登录账户" in page

    # 密码错
    wrong = _web({"action": "login_existing", "user_code": second, "username": "aphid", "password": "bad"})
    assert "用户名或密码不对" in wrong

    ok = _web({"action": "login_existing", "user_code": second, "username": "aphid", "password": "hunter2x"})
    assert "已批准" in ok
    assert _status(second)["username"] == "aphid"


def test_password_bruteforce_is_rate_limited(app_env) -> None:
    _login_via_web(username="aphid", password="hunter2x")
    second = "D4E4-F4G4"
    _start(code=second)

    # 5 次密码错误后进入锁定
    for _ in range(5):
        _web({"action": "login_existing", "user_code": second, "username": "aphid", "password": "bad"})

    locked = _web({"action": "login_existing", "user_code": second, "username": "aphid", "password": "bad"})
    assert "请求太频繁" in locked

    # 就算密码对了，锁定期内也进不去
    still_locked = _web({"action": "login_existing", "user_code": second, "username": "aphid", "password": "hunter2x"})
    assert "请求太频繁" in still_locked


def test_device_start_is_rate_limited(app_env) -> None:
    from tonicuisc_server.main import device_start_limiter

    device_start_limiter.reset()
    for index in range(device_start_limiter.limit):
        code = f"A{index:03d}-B{index:03d}"[:9]
        client.post("/api/device/start", json={"user_code": code, "poll_secret": "secret-value-1", "name": "x"})

    blocked = client.post(
        "/api/device/start", json={"user_code": "Z9Z9-Z9Z9", "poll_secret": "secret-value-1", "name": "x"}
    )
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers


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
    assert devices[0]["revoked"] is False

    device_id = devices[0]["id"]
    assert client.delete(f"/api/devices/{device_id}", headers=headers).status_code == 200
    assert client.get("/api/sources", headers=headers).status_code == 401


def test_devices_returns_revoked_as_boolean(app_env) -> None:
    """客户端按布尔判断，服务端不能返回 0/1。"""
    _login_via_web()
    headers = {"X-API-Key": _status()["api_key"]}
    device_id = "E5F5-A5B5"
    _start(code=device_id, secret=SECRET, name="备用机")
    assert "已批准" in _web(
        {"action": "login_existing", "user_code": device_id, "username": "aphid", "password": "hunter2x"}
    )
    client.get("/api/devices", headers=headers)  # 先确认能列出来

    items = client.get("/api/devices", headers=headers).json()["items"]
    assert len(items) == 2
    for item in items:
        assert isinstance(item["revoked"], bool)
        assert isinstance(item["current"], bool)

    target = next(item for item in items if not item["current"])
    client.delete(f"/api/devices/{target['id']}", headers=headers)
    after = {item["id"]: item for item in client.get("/api/devices", headers=headers).json()["items"]}
    assert after[target["id"]]["revoked"] is True


def test_cleanup_removes_orphan_devices(app_env) -> None:
    """旧版配对流程留下的、没有关联账户的设备可以被清掉。"""
    _login_via_web()
    api_key = _status()["api_key"]

    app_env["store"].create_device(name="旧设备", key_hash="x" * 64)  # user_id 为空
    devices = client.get("/api/devices", headers={"X-API-Key": api_key}).json()["items"]
    assert len(devices) == 2
    assert any(item["username"] is None for item in devices)

    removed = app_env["store"].delete_devices(without_user=True)
    assert removed == 1
    remaining = client.get("/api/devices", headers={"X-API-Key": api_key}).json()["items"]
    assert len(remaining) == 1 and remaining[0]["username"] == "aphid"


def test_search_validation_after_auth(app_env) -> None:
    _login_via_web()
    headers = {"X-API-Key": _status()["api_key"]}
    assert client.get("/api/search", params={"keyword": ""}, headers=headers).status_code == 422
    assert client.get("/api/lyric/migu:0", headers=headers).status_code == 404
