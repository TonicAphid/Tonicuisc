"""忘记密码（恢复码）：AuthManager 单元 + /login 网页流程 + CLI 兜底。不联网。"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from conftest import make_storage
from tonicuisc_server.cli import commands as cli
from tonicuisc_server.auth import (
    RECOVERY_CODE_RE,
    AuthManager,
    hash_recovery_code,
    new_recovery_code,
    normalize_recovery_code,
    verify_recovery_code,
)
from tonicuisc_server.api import app

client = TestClient(app)

RECOVERY_RE_IN_TEXT = re.compile(r'([A-Z0-9]{5}(?:-[A-Z0-9]{5}){3})')


@pytest.fixture()
def auth():
    store = make_storage()
    manager = AuthManager(store, ttl=300)
    try:
        yield manager
    finally:
        store.close()


# ------------------------------------------------------------------ 恢复码本身
def test_recovery_code_shape() -> None:
    code = new_recovery_code()
    assert RECOVERY_CODE_RE.match(code), code
    assert new_recovery_code() != code, "每次都该不一样"
    assert not set(code) & set("0O1I"), "别混进 0/O/1/I 这种看混的字符"


def test_recovery_code_normalize_tolerates_typing() -> None:
    code = new_recovery_code()
    assert normalize_recovery_code(code.lower()) == code
    assert normalize_recovery_code(code.replace("-", " ")) == code
    assert normalize_recovery_code(code.replace("-", "")) == code
    assert normalize_recovery_code("short") == "SHORT", "长度不对就别乱分组"


def test_recovery_code_hash_is_one_way() -> None:
    stored = hash_recovery_code("K7MP2-QX9RT-3WZHB-ND5LF")
    assert stored.startswith("pbkdf2_sha256$")
    assert "K7MP2" not in stored
    assert verify_recovery_code("k7mp2 qx9rt 3wzhb nd5lf", stored), "大小写和分隔符都能忍"
    assert not verify_recovery_code("K7MP2-QX9RT-3WZHB-ND5LX", stored)
    assert not verify_recovery_code("wrong", stored)
    assert not verify_recovery_code("wrong", "")


# ------------------------------------------------------------------ AuthManager
def test_reset_password_swaps_password_and_code(auth: AuthManager) -> None:
    auth.register("aphid", "hunter2x")
    old_code = auth.issue_recovery_code("aphid")
    assert old_code and RECOVERY_CODE_RE.match(old_code)

    result = auth.reset_password("aphid", old_code, "brandnew1")
    assert result is not None
    user, new_code = result
    assert user["username"] == "aphid"
    assert new_code != old_code, "用过的码要作废并换新的"

    assert auth.authenticate("aphid", "hunter2x") is None, "旧密码必须失效"
    assert auth.authenticate("aphid", "brandnew1") is not None

    assert auth.reset_password("aphid", old_code, "thirdone1") is None, "旧恢复码不能再用"
    assert auth.reset_password("aphid", new_code, "thirdone1") is not None, "新码要能用"


def test_reset_password_rejects_bad_input(auth: AuthManager) -> None:
    auth.register("aphid", "hunter2x")
    assert auth.reset_password("aphid", new_recovery_code(), "brandnew1") is None, "没设过恢复码"
    assert auth.reset_password("nobody", new_recovery_code(), "brandnew1") is None, "账户不存在"

    code = auth.issue_recovery_code("aphid")
    assert auth.reset_password("aphid", "AAAAA-BBBBB-CCCCC-DDDDD", "brandnew1") is None
    assert auth.reset_password("aphid", code, "123") is None, "新密码太短"
    assert auth.authenticate("aphid", "hunter2x") is not None, "失败不该动密码"


def test_set_password_helper(auth: AuthManager) -> None:
    auth.register("aphid", "hunter2x")
    assert auth.set_password("aphid", "brandnew1") is not None
    assert auth.authenticate("aphid", "hunter2x") is None
    assert auth.authenticate("aphid", "brandnew1") is not None
    assert auth.set_password("aphid", "123") is None, "太短"
    assert auth.set_password("nobody", "brandnew1") is None
    assert auth.set_password("aphid", "brandnew1") is not None, "反复设也行"


def test_issue_recovery_code_invalidates_previous(auth: AuthManager) -> None:
    auth.register("aphid", "hunter2x")
    first = auth.issue_recovery_code("aphid")
    second = auth.issue_recovery_code("aphid")
    assert first != second
    assert auth.reset_password("aphid", first, "brandnew1") is None, "重新发码后旧的作废"
    assert auth.reset_password("aphid", second, "brandnew1") is not None


# ------------------------------------------------------------------ /login 网页
def _start(code: str, secret: str = "recovery-poll-1", name: str = "测试机") -> None:
    resp = client.post(
        "/api/device/start",
        json={"user_code": code, "poll_secret": secret, "name": name},
    )
    assert resp.status_code == 200, resp.text


def _register(code: str, username: str = "aphid", password: str = "hunter2x") -> None:
    _start(code, secret=f"poll-{code}")
    resp = client.post(
        "/login",
        data={"action": "register", "user_code": code, "username": username, "password": password},
    )
    assert resp.status_code == 200, resp.text


def test_web_reset_password_roundtrip(app_env) -> None:
    auth = app_env["auth"]
    _register("A1A1-A1A1")
    old_code = auth.issue_recovery_code("aphid")
    assert old_code

    # 另一台设备卡在登录页 → 点「忘记密码」
    _start("B2B2-B2B2", secret="poll-b2b2")
    form = client.post("/login", data={"action": "reset_form", "user_code": "B2B2-B2B2"})
    assert form.status_code == 200
    assert "重置密码" in form.text and "恢复码" in form.text

    done = client.post(
        "/login",
        data={
            "action": "reset_password",
            "user_code": "B2B2-B2B2",
            "username": "aphid",
            "recovery_code": old_code,
            "password": "brandnew1",
        },
    )
    assert done.status_code == 200
    assert "密码已重置" in done.text
    assert old_code not in done.text, "旧码不该再显示出来"

    new_code = (RECOVERY_RE_IN_TEXT.search(done.text) or [None, None])[1] or ""
    assert RECOVERY_CODE_RE.match(new_code), f"页面上要给出新恢复码，实际：{new_code!r}"

    assert auth.authenticate("aphid", "hunter2x") is None
    assert auth.authenticate("aphid", "brandnew1") is not None

    # 设备也批了：App 那边能直接拿到 key
    status = client.post(
        "/api/device/status",
        json={"user_code": "B2B2-B2B2", "poll_secret": "poll-b2b2"},
    ).json()
    assert status.get("api_key"), status

    # 旧码再用一次 → 拒绝，密码不变
    _start("C3C3-C3C3", secret="poll-c3c3")
    again = client.post(
        "/login",
        data={
            "action": "reset_password",
            "user_code": "C3C3-C3C3",
            "username": "aphid",
            "recovery_code": old_code,
            "password": "thirdone1",
        },
    )
    assert "恢复码不对" in again.text
    assert auth.authenticate("aphid", "brandnew1") is not None, "失败不该动密码"


def test_web_reset_without_device_code_is_refused(app_env) -> None:
    auth = app_env["auth"]
    _register("A1A1-A1A1")
    code = auth.issue_recovery_code("aphid")

    # 没有设备码：连页面都不给（顺带挡住跨站伪造——攻击者不知道受害者的码）
    resp = client.post(
        "/login",
        data={
            "action": "reset_password",
            "username": "aphid",
            "recovery_code": code,
            "password": "brandnew1",
        },
    )
    assert resp.status_code == 200
    assert "缺少设备码" in resp.text
    assert auth.authenticate("aphid", "hunter2x") is not None


def test_web_reset_is_rate_limited(app_env) -> None:
    auth = app_env["auth"]
    _register("A1A1-A1A1")
    auth.issue_recovery_code("aphid")
    _start("B2B2-B2B2", secret="poll-b2b2")

    def attempt() -> str:
        return client.post(
            "/login",
            data={
                "action": "reset_password",
                "user_code": "B2B2-B2B2",
                "username": "aphid",
                "recovery_code": "AAAAA-BBBBB-CCCCC-DDDDD",
                "password": "brandnew1",
            },
        ).text

    for _ in range(5):  # guard 的 limit=5
        assert "恢复码不对" in attempt()
    assert "请求太频繁" in attempt(), "错够次数就该锁住"


def test_web_reset_password_too_short(app_env) -> None:
    auth = app_env["auth"]
    _register("A1A1-A1A1")
    code = auth.issue_recovery_code("aphid")
    _start("B2B2-B2B2", secret="poll-b2b2")

    resp = client.post(
        "/login",
        data={
            "action": "reset_password",
            "user_code": "B2B2-B2B2",
            "username": "aphid",
            "recovery_code": code,
            "password": "123",
        },
    )
    assert "至少 6 位" in resp.text
    # 这种情况不该消耗掉恢复码
    assert auth.reset_password("aphid", code, "brandnew1") is not None


# ------------------------------------------------------------------ CLI
def test_cli_recovery_code_command(app_env, capsys) -> None:
    auth = app_env["auth"]
    auth.register("aphid", "hunter2x")
    store = app_env["store"]

    assert cli.account_command(store, "recovery-code", "aphid") == 0
    printed = capsys.readouterr().out
    code = (RECOVERY_RE_IN_TEXT.search(printed) or [None])[0] or ""
    assert RECOVERY_CODE_RE.match(code), f"要把码打出来，实际：{printed!r}"
    assert verify_recovery_code(code, store.find_user("aphid")["recovery_hash"])
    assert "aphid" not in code, "码里不该夹带用户名"

    assert cli.account_command(store, "recovery-code", "nobody") == 1


def test_cli_set_password_command(app_env, monkeypatch) -> None:
    auth = app_env["auth"]
    auth.register("aphid", "hunter2x")
    store = app_env["store"]

    answers = iter(["brandnew1", "brandnew1"])
    monkeypatch.setattr(cli, "getpass", type("G", (), {"getpass": staticmethod(lambda prompt="": next(answers))}))
    assert cli.account_command(store, "set-password", "aphid") == 0
    assert auth.authenticate("aphid", "brandnew1") is not None
    assert auth.authenticate("aphid", "hunter2x") is None

    # 两次不一致 → 不改
    answers2 = iter(["whatever1", "different"])
    monkeypatch.setattr(cli, "getpass", type("G", (), {"getpass": staticmethod(lambda prompt="": next(answers2))}))
    assert cli.account_command(store, "set-password", "aphid") == 1
    assert auth.authenticate("aphid", "brandnew1") is not None, "输错就不该改"

    assert cli.account_command(store, "set-password", "nobody") == 1
