"""内置网页站点（``api/routes/site.py``）：静态页 + 网页会话 + Cookie 鉴权。

以前这三件事归 Node 的 ``webapp/server.js``，所以一条测试都没有；现在搬进 Python，
至少要保证「页面能开、设备码能走完、Cookie 能当 API Key 用」这三条不塌。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tonicuisc_server.api import app
from tonicuisc_server.api.routes import site
from tonicuisc_server.auth import hash_key
from tonicuisc_server.core.config import SETTINGS
from tonicuisc_server.web.sessions import SESSIONS, WEB_ID_COOKIE

client = TestClient(app)

PUBLIC = Path(SETTINGS.web_public_dir)

#: 静态页测试要有真实文件；单独打包部署时可能没带 webapp/public
needs_public = pytest.mark.skipif(
    not (PUBLIC / "index.html").is_file(),
    reason=f"没有静态目录 {PUBLIC}",
)


@pytest.fixture(autouse=True)
def _clean_sessions():
    """会话是进程级单例、TestClient 也会跨用例留着 Cookie，两边都得清干净。"""
    SESSIONS.clear()
    client.cookies.clear()
    try:
        yield
    finally:
        SESSIONS.clear()
        client.cookies.clear()


# ------------------------------------------------------------------ 静态页
@needs_public
def test_landing_page(app_env) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "no-cache" in resp.headers["cache-control"]


@needs_public
@pytest.mark.parametrize("path", ["/music", "/music/", "/music/index.html"])
def test_music_player_routes(app_env, path: str) -> None:
    resp = client.get(path)
    assert resp.status_code == 200
    assert "music.js" in resp.text


@needs_public
@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/music/music.css", "text/css"),
        ("/music/music.js", "application/javascript"),
    ],
)
def test_player_assets(app_env, path: str, expected: str) -> None:
    """CSS / JS 的 Content-Type 必须是浏览器认的，写错就整页白屏。"""
    resp = client.get(path)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(expected)


@needs_public
def test_unknown_page_is_html_404(app_env) -> None:
    resp = client.get("/no-such-page")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("text/html")


def test_unknown_api_is_json_404(app_env) -> None:
    """``/api/*`` 不能被静态兜底路由吃掉（前端按 JSON 解析错误）。"""
    key = _make_api_key(app_env["auth"], "ghost")
    resp = client.get("/api/definitely-not-here", headers={"x-api-key": key})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Not Found"


@needs_public
def test_path_traversal_is_blocked(app_env) -> None:
    """``../`` 不能翻到 webapp/public 之外去。"""
    resp = client.get("/music/..%2f..%2f..%2fserver%2ftonicuisc_server%2f__init__.py")
    assert resp.status_code == 404


# ------------------------------------------------------------------ 网页会话
def _make_api_key(auth, username: str) -> str:
    """直接走服务端设备流拿一把明文 key（网页会话里拿不到明文，这是唯一的来源）。"""
    user = auth.register(username, "hunter2x")
    assert user is not None
    code, secret = "ZZZZ-9999", "poll-secret-abcdefgh"
    assert auth.start_device_request(code, secret, "测试机") is not None
    assert auth.approve(code, user["id"])
    key = auth.request_status(code, secret)["api_key"]
    assert key
    return key


def _reset_browser() -> None:
    """清掉服务端会话 + 浏览器 Cookie。

    夹具里已经清过一遍，但 ``app_env`` 收尾时删临时目录偶尔会被环境的
    安全删除钩子打断（``SystemExit``），后面的收尾就不执行了；这里再清一次，
    让每个用例都从「全新浏览器」开始。
    """
    SESSIONS.clear()
    client.cookies.clear()


def _start_session() -> dict:
    _reset_browser()
    resp = client.post("/music/api/session")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data.get("state") == "pending", data
    return data


def _pair(username: str = "webbie") -> dict:
    """走完一次设备码登录，返回 ``ready`` 的会话。

    **不重置浏览器**——要模拟「同一台机器上的同一个浏览器」，指纹 Cookie 必须留着。
    """
    resp = client.post("/music/api/session")
    data = resp.json()
    if data.get("authenticated"):
        return data
    assert data.get("state") == "pending", data
    code = data["user_code"]

    form = {"action": "register", "user_code": code, "username": username, "password": "hunter2x"}
    page = client.post("/login", data=form)
    if "已批准" not in page.text:
        # 这个账户已经存在（同一用例里第二次配对）：换成登录
        form["action"] = "login_existing"
        page = client.post("/login", data=form)
    assert "已批准" in page.text, page.text

    data = client.get("/music/api/session").json()
    assert data["authenticated"] is True, data
    return data


def _web_id() -> str:
    value = client.cookies.get(WEB_ID_COOKIE)
    assert value, "浏览器没拿到指纹 Cookie"
    return value


def test_session_start_returns_device_code(app_env) -> None:
    data = _start_session()
    assert data["authenticated"] is False
    assert data["state"] == "pending"
    assert len(data["user_code"]) == 9  # A1B1-C1D1
    assert data["expires_in"] > 0
    assert data["login_url"].endswith(data["user_code"])


def test_login_page_prefills_device_code(app_env) -> None:
    """``/login?code=`` 直接进「登录 / 注册」表单（以前是 Node 替用户提交一次）。"""
    code = _start_session()["user_code"]
    resp = client.get(f"/login?code={code}")
    assert resp.status_code == 200
    assert "登录账户" in resp.text
    assert code in resp.text


def test_session_becomes_ready_after_approval(app_env) -> None:
    code = _start_session()["user_code"]

    approved = client.post(
        "/login",
        data={"action": "register", "user_code": code, "username": "webbie", "password": "hunter2x"},
    )
    assert approved.status_code == 200
    assert "已批准" in approved.text

    data = client.get("/music/api/session").json()
    assert data["authenticated"] is True
    assert data["state"] == "ready"
    assert data["me"]["username"] == "webbie"


def test_cookie_works_as_api_key(app_env) -> None:
    """浏览器只带 HttpOnly Cookie，接口也要能过鉴权——这是整个网页播放器的地基。"""
    code = _start_session()["user_code"]
    client.post(
        "/login",
        data={"action": "register", "user_code": code, "username": "cooki", "password": "hunter2x"},
    )
    client.get("/music/api/session")

    resp = client.get("/api/me")  # 注意：没有 x-api-key
    assert resp.status_code == 200, resp.text
    assert resp.json()["username"] == "cooki"


def test_logout_invalidates_cookie(app_env) -> None:
    code = _start_session()["user_code"]
    client.post(
        "/login",
        data={"action": "register", "user_code": code, "username": "bye", "password": "hunter2x"},
    )
    client.get("/music/api/session")

    assert client.delete("/music/api/session").status_code == 200
    assert client.get("/api/me").status_code == 401


def test_manual_api_key_login(app_env) -> None:
    """登录页底部的「手动粘贴 API Key」：不走设备码，直接认 key。"""
    _reset_browser()
    key = _make_api_key(app_env["auth"], "manual")

    too_short = client.post("/music/api/session/manual", json={"api_key": "too-short"})
    assert too_short.status_code == 400

    bad = client.post("/music/api/session/manual", json={"api_key": "x" * 40})
    assert bad.status_code == 401

    resp = client.post("/music/api/session/manual", json={"api_key": key})
    assert resp.status_code == 200, resp.text
    assert resp.json()["me"]["username"] == "manual"
    # 之后浏览器只靠 Cookie 就能调接口
    assert client.get("/api/me").json()["username"] == "manual"


def test_cancel_keeps_session(app_env) -> None:
    _start_session()
    assert client.post("/music/api/session/cancel").status_code == 200
    assert client.get("/music/api/session").json()["state"] == "anonymous"


# ------------------------------------------------- 浏览器指纹（重启不掉线 / 不多设备）
def test_fingerprint_cookie_is_long_lived(app_env) -> None:
    """第一次进站就发一枚一年的指纹 Cookie——它就是「网页播放器的 MAC 地址」。"""
    resp = client.post("/music/api/session")
    raw = "; ".join(resp.headers.get_list("set-cookie"))
    assert f"{WEB_ID_COOKIE}=" in raw
    assert "HttpOnly" in raw
    assert "Max-Age=31536000" in raw
    assert len(_web_id()) == 32


def test_restart_keeps_browser_logged_in(app_env) -> None:
    """服务端重启（内存会话清空）后，浏览器靠指纹自动恢复登录，不用重新配对。"""
    _reset_browser()
    first = _pair()
    assert first["authenticated"] is True

    SESSIONS.clear()  # ← 模拟进程重启：内存会话全没了，Cookie 还在

    data = client.get("/music/api/session").json()
    assert data["authenticated"] is True
    assert data["state"] == "ready"
    assert data["me"]["username"] == "webbie"


def test_restart_reissues_key_and_keeps_other_tabs(app_env) -> None:
    """恢复是**重新签发** key（不是把 key 存起来），而且是追加，多个标签页不会互踢。"""
    _reset_browser()
    first = _pair()
    device_id = first["me"]["device_id"]
    web_id = _web_id()
    old_key = SESSIONS.get(client.cookies.get("tuisc_session")).api_key

    SESSIONS.clear()  # 模拟重启：内存会话全没了
    tab_a, tab_b = SESSIONS.create(), SESSIONS.create()
    assert site._restore(tab_a, web_id) is True
    assert site._restore(tab_b, web_id) is True

    assert tab_a.api_key and tab_b.api_key
    assert tab_a.api_key != tab_b.api_key != old_key
    # 两个标签页都还认同一台设备（不是各自新建一台）
    for key in (tab_a.api_key, tab_b.api_key):
        assert app_env["auth"].verify(key)["id"] == device_id
    assert len(app_env["store"].list_devices(None, own_only=False)) == 1


def test_old_key_survives_restart_until_device_is_revoked(app_env) -> None:
    """重启后旧 key 不立刻作废（另一个标签页还拿着它），但吊销设备就全灭。"""
    _reset_browser()
    first = _pair()
    device_id = first["me"]["device_id"]
    old_key = SESSIONS.get(client.cookies.get("tuisc_session")).api_key

    SESSIONS.clear()
    assert site._restore(SESSIONS.create(), _web_id()) is True
    assert app_env["auth"].verify(old_key) is not None

    app_env["store"].revoke_device(device_id)
    assert app_env["auth"].verify(old_key) is None


def test_same_browser_keeps_one_device_row(app_env) -> None:
    """同一个浏览器反复登录，设备列表里始终只有一台「网页播放器」。"""
    _reset_browser()
    first = _pair()
    client.delete("/music/api/session")  # 主动退出（绑定会被清掉，指纹还在）
    second = _pair()

    assert first["me"]["device_id"] == second["me"]["device_id"]
    devices = app_env["store"].list_devices(None, own_only=False)
    assert len(devices) == 1, [d["name"] for d in devices]


def test_remember_stores_pointer_not_key(app_env) -> None:
    """Redis 里只存「指纹 → 设备」的指针，翻不出能直接用的凭证。"""
    _reset_browser()
    data = _pair()
    binding = app_env["store"].get_web_login(hash_key(_web_id()))

    assert binding is not None
    assert binding["device_id"] == data["me"]["device_id"]
    assert set(binding) == {"device_id", "user_id", "created_at"}


def test_logout_forgets_remember(app_env) -> None:
    """主动退出 = 真的退出：绑定删掉，重启后也不会自动恢复。"""
    _reset_browser()
    _pair()
    client.delete("/music/api/session")
    SESSIONS.clear()

    assert client.get("/music/api/session").json()["authenticated"] is False
    assert app_env["store"].get_web_login(hash_key(_web_id())) is None


def test_deleted_device_cannot_restore(app_env) -> None:
    """从设备列表里删掉这台设备，指纹也跟着失效（不能靠它自动复活）。"""
    _reset_browser()
    data = _pair()
    device_id = data["me"]["device_id"]

    assert client.delete(f"/api/devices/{device_id}").status_code == 200
    SESSIONS.clear()

    assert client.get("/music/api/session").json()["authenticated"] is False
    assert app_env["store"].get_web_login(hash_key(_web_id())) is None


def test_manual_login_also_remembers(app_env) -> None:
    """手动粘贴 key 登录同样记指纹，下次重启也能自动恢复。"""
    _reset_browser()
    key = _make_api_key(app_env["auth"], "manual")
    resp = client.post("/music/api/session/manual", json={"api_key": key})
    assert resp.status_code == 200, resp.text

    SESSIONS.clear()
    data = client.get("/music/api/session").json()
    assert data["authenticated"] is True
    assert data["me"]["username"] == "manual"


# ------------------------------------------------------------------ 下载入口
def test_download_root_redirects_home(app_env) -> None:
    resp = client.get("/download", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/"


def test_download_unknown_platform(app_env) -> None:
    resp = client.get("/download/beos")
    assert resp.status_code == 404
    assert "beos" in resp.text


# ------------------------------------------------------------------ 爬虫
def test_robots_txt(app_env) -> None:
    """``/robots.txt`` 是真路由，不是 404 兜底里捞静态文件捞出来的。"""
    resp = client.get("/robots.txt")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    lines = resp.text.splitlines()
    assert lines[0] == "User-agent: *"
    assert "Disallow: /api/" in lines
    assert "Disallow: /music/api/" in lines
    assert "Disallow: /login" in lines
    assert "Allow: /" in lines


def test_robots_txt_supports_head(app_env) -> None:
    """Starlette 给 GET 自动加 HEAD，爬虫和 CDN 探测都靠它。"""
    resp = client.head("/robots.txt")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")

