"""这一波「更快 / 更稳」改动的回归测试：节流写、pipeline 汇总、轮询改 POST、注册限速。"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from conftest import make_storage
from tonicuisc_server.api import app
from tonicuisc_server.auth import AuthManager, new_user_code

client = TestClient(app)


def _login_device(auth: AuthManager, name: str = "手机") -> tuple[str, str]:
    """走完整设备码流程，返回 (api_key, device_id)。"""
    user = auth.storage.find_user("aphid") or auth.register("aphid", "hunter2x")
    code, secret = new_user_code(), "poll-secret-value"
    auth.start_device_request(code, secret, name)
    auth.approve(code, user["id"])
    granted = auth.request_status(code, secret)
    return granted["api_key"], granted["device_id"]


# ------------------------------------------------------------------ 节流写
def test_verify_skips_touch_within_cooldown() -> None:
    store = make_storage()
    try:
        auth = AuthManager(store, ttl=300)
        api_key, device_id = _login_device(auth)

        calls: list[str] = []
        store.touch_device = lambda target: calls.append(target)  # 实例属性盖住 mixin 方法

        assert auth.verify(api_key) is not None
        assert calls == [], "刚建好的设备（last_seen = 现在）不该再写一次 Redis"

        # 把 last_seen 推到很久以前 → 到点就该写了
        store.client.hset(store.key("device", device_id), "last_seen", repr(time.time() - 3600))
        assert auth.verify(api_key) is not None
        assert calls == [device_id], "过了冷却期要刷新 last_seen"
    finally:
        store.close()


# ------------------------------------------------------------------ 一次往返
def test_library_bundle_matches_individual_reads() -> None:
    store = make_storage()
    try:
        store.client.zadd(store.key("lib", "u1", "like"), {"a": 1.0, "b": 2.0})
        store.client.zadd(store.key("lib", "u1", "favorite"), {"c": 3.0})
        store.client.zadd(store.key("lib", "u1", "history"), {"a": 4.0})

        counts, likes, favorites = store.library_bundle("u1")
        assert counts == {"like": 2, "favorite": 1, "history": 1}
        assert set(likes) == {"a", "b"}
        assert favorites == ["c"]

        # 和逐个接口读出来的结果必须一致（合并只是省来回，不该改语义）
        assert counts == store.library_counts("u1")
        assert likes == store.library_ids("u1", "like")
        assert favorites == store.library_ids("u1", "favorite")
    finally:
        store.close()


# ------------------------------------------------------------------ 轮询改 POST
def test_device_status_is_post_only(app_env) -> None:
    got = client.get("/api/device/status", params={"user_code": "A1B1-C1D1", "poll_secret": "0123456789"})
    assert got.status_code == 405, "poll_secret 不该再走 query（会进访问日志）"

    resp = client.post(
        "/api/device/status",
        json={"user_code": "A1B1-C1D1", "poll_secret": "0123456789"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "expired"


def test_access_log_never_prints_poll_secret(app_env, capsys) -> None:
    secret = "super-secret-poll-value"
    client.post("/api/device/start", json={"user_code": "A1B1-C1D1", "poll_secret": secret, "name": "手机"})
    client.post("/api/device/status", json={"user_code": "A1B1-C1D1", "poll_secret": secret})

    printed = capsys.readouterr().out
    assert "/api/device/status" in printed, "访问日志还是要有的"
    assert secret not in printed, "轮询密钥不能出现在日志里"


# ------------------------------------------------------------------ 注册限速
def test_register_is_rate_limited(app_env) -> None:
    code = "A7B7-C7D7"
    client.post("/api/device/start", json={"user_code": code, "poll_secret": "register-secret-1", "name": "手机"})
    # 先占掉一个用户名，后面每次注册都会撞名 → 记一次失败
    app_env["auth"].register("taken", "hunter2x")

    body = {"action": "register", "user_code": code, "username": "taken", "password": "hunter2x"}
    for _ in range(5):
        resp = client.post("/login", data=body)
        assert "用户名已被占用" in resp.text

    locked = client.post("/login", data=body)
    assert "请求太频繁" in locked.text, "撞名撞多了也要被锁，否则注册就是现成的用户名枚举接口"
