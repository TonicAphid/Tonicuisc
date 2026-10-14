"""AuthManager 单元测试：密码哈希、设备码流程、key 只存哈希。"""

from __future__ import annotations

import time

import pytest

from conftest import make_storage
from tonicuisc_server.auth import (
    AuthManager,
    hash_key,
    hash_password,
    new_api_key,
    new_user_code,
    normalize_user_code,
    verify_password,
)
from tonicuisc_server.storage import Storage


@pytest.fixture()
def auth():
    store = make_storage()
    manager = AuthManager(store, ttl=300)
    try:
        yield manager
    finally:
        store.close()


# ------------------------------------------------------------------ 密码
def test_password_hash_roundtrip() -> None:
    stored = hash_password("hunter2x")
    assert stored.startswith("pbkdf2_sha256$")
    assert "hunter2x" not in stored
    assert verify_password("hunter2x", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("hunter2x", "garbage")


def test_password_hash_uses_random_salt() -> None:
    assert hash_password("same") != hash_password("same")


# ------------------------------------------------------------------ 用户名
def test_register_and_authenticate(auth: AuthManager) -> None:
    user = auth.register("aphid", "hunter2x")
    assert user is not None and user["username"] == "aphid"

    assert auth.authenticate("aphid", "hunter2x") is not None
    assert auth.authenticate("aphid", "bad") is None
    assert auth.authenticate("nobody", "hunter2x") is None


def test_register_rejects_bad_input(auth: AuthManager) -> None:
    assert auth.register("a", "hunter2x") is None  # 用户名太短
    assert auth.register("bad name", "hunter2x") is None  # 含空格
    assert auth.register("aphid", "123") is None  # 密码太短
    assert auth.register("aphid", "hunter2x") is not None
    assert auth.register("aphid", "hunter2x") is None  # 重名


def test_user_list_has_no_password(auth: AuthManager) -> None:
    auth.register("aphid", "hunter2x")
    users = auth.users()
    assert users and "password_hash" not in users[0]


# ------------------------------------------------------------------ 设备码
def test_user_code_format() -> None:
    code = new_user_code()
    assert len(code) == 9 and code[4] == "-"
    assert normalize_user_code(code.lower().replace("-", "")) == code


def test_device_flow_grants_key_once(auth: AuthManager) -> None:
    code, secret = new_user_code(), "poll-secret-value"
    assert auth.start_device_request(code, secret, "手机") is not None
    assert auth.request_status(code, secret)["status"] == "pending"

    user = auth.register("aphid", "hunter2x")
    assert auth.approve(code, user["id"]) is True

    granted = auth.request_status(code, secret)
    assert granted["status"] == "approved"
    assert granted["username"] == "aphid"
    assert auth.verify(granted["api_key"]) is not None

    # 再问一次就不给了
    assert auth.request_status(code, secret)["status"] == "claimed"


def test_device_flow_rejects_wrong_secret(auth: AuthManager) -> None:
    code = new_user_code()
    auth.start_device_request(code, "right-secret", "手机")
    assert auth.request_status(code, "wrong-secret")["status"] == "expired"
    assert auth.request_status(code, "right-secret")["status"] == "pending"


def test_device_flow_rejects_duplicate_code(auth: AuthManager) -> None:
    code = new_user_code()
    assert auth.start_device_request(code, "secret-1", "A") is not None
    assert auth.start_device_request(code, "secret-2", "B") is None


def test_device_flow_rejects_bad_code(auth: AuthManager) -> None:
    assert auth.start_device_request("nope", "secret-1", "A") is None
    assert auth.request_status("nope", "secret-1")["status"] == "expired"


def test_unapproved_code_gives_no_key(auth: AuthManager) -> None:
    code, secret = new_user_code(), "poll-secret-value"
    auth.start_device_request(code, secret, "手机")
    status = auth.request_status(code, secret)
    assert status["status"] == "pending" and "api_key" not in status


def test_denied_request(auth: AuthManager) -> None:
    code, secret = new_user_code(), "poll-secret-value"
    auth.start_device_request(code, secret, "手机")
    assert auth.deny(code) is True
    assert auth.request_status(code, secret)["status"] == "denied"


def test_verify_accepts_only_own_key(auth: AuthManager) -> None:
    code, secret = new_user_code(), "poll-secret-value"
    auth.start_device_request(code, secret, "手机")
    user = auth.register("aphid", "hunter2x")
    auth.approve(code, user["id"])
    api_key = auth.request_status(code, secret)["api_key"]

    assert auth.verify(api_key) is not None
    assert auth.verify(new_api_key()) is None
    assert auth.verify("") is None
    assert auth.verify(None) is None


def test_devices_store_only_hash(auth: AuthManager) -> None:
    code, secret = new_user_code(), "poll-secret-value"
    auth.start_device_request(code, secret, "手机")
    user = auth.register("aphid", "hunter2x")
    auth.approve(code, user["id"])
    api_key = auth.request_status(code, secret)["api_key"]

    device = auth.storage.find_device_by_hash(hash_key(api_key))
    assert device is not None
    assert device["key_hash"] == hash_key(api_key)
    assert device["username"] == "aphid"
    assert "api_key" not in auth.storage.list_devices(user["id"])[0]
    assert "key_hash" not in auth.storage.list_devices(user["id"])[0]


# ------------------------------------------------------- 重新配对 = 复用记录
def test_repair_reuses_device_record_and_rotates_key(auth: AuthManager) -> None:
    user = auth.register("aphid", "hunter2x")
    # 第一次配对：没带旧 ID → 服务端发一个
    code, secret = new_user_code(), "poll-secret-value"
    auth.start_device_request(code, secret, "手机")
    auth.approve(code, user["id"])
    first = auth.request_status(code, secret)
    old_id, old_key = first["device_id"], first["api_key"]
    assert auth.verify(old_key) is not None

    # 重新配对：带上旧 ID → 同一条记录、发新 key，旧 key 立刻作废
    code2 = new_user_code()
    auth.start_device_request(code2, secret, "手机", device_id=old_id)
    auth.approve(code2, user["id"])
    second = auth.request_status(code2, secret)
    assert second["device_id"] == old_id, "同一台设备重新登录不该长出第二条记录"
    assert second["api_key"] != old_key
    assert auth.verify(second["api_key"]) is not None
    assert auth.verify(old_key) is None, "换 key 后旧 key 必须立刻失效"
    assert len(auth.storage.list_devices(user["id"])) == 1


def test_repair_cannot_claim_other_users_device(auth: AuthManager) -> None:
    alice = auth.register("alice", "hunter2x")
    bob = auth.register("bob", "hunter2x")
    code, secret = new_user_code(), "poll-secret-value"
    auth.start_device_request(code, secret, "iPhone")
    auth.approve(code, alice["id"])
    first = auth.request_status(code, secret)
    alice_id, alice_key = first["device_id"], first["api_key"]

    # Bob 的配对申请冒领 Alice 的设备 ID → 不给复用，另发新记录
    code2 = new_user_code()
    auth.start_device_request(code2, secret, "iPhone", device_id=alice_id)
    auth.approve(code2, bob["id"])
    second = auth.request_status(code2, secret)
    assert second["device_id"] != alice_id, "不能认领别人账户的设备记录"
    assert auth.verify(alice_key) is not None, "Alice 的 key 不该被动到"
    assert [d["id"] for d in auth.storage.list_devices(alice["id"])] == [alice_id]
    assert [d["id"] for d in auth.storage.list_devices(bob["id"])] == [second["device_id"]]


def test_repair_ignores_malformed_device_id(auth: AuthManager) -> None:
    code, secret = new_user_code(), "poll-secret-value"
    auth.start_device_request(code, secret, "手机", device_id="NOT-A-VALID-ID")
    user = auth.register("aphid", "hunter2x")
    auth.approve(code, user["id"])
    granted = auth.request_status(code, secret)
    assert len(granted["device_id"]) == 32 and granted["device_id"].isalnum(), "格式不对就当没带"


def test_repair_revives_revoked_record(auth: AuthManager) -> None:
    user = auth.register("aphid", "hunter2x")
    code, secret = new_user_code(), "poll-secret-value"
    auth.start_device_request(code, secret, "手机")
    auth.approve(code, user["id"])
    first = auth.request_status(code, secret)
    auth.storage.revoke_device(first["device_id"])
    assert auth.verify(first["api_key"]) is None, "吊销后旧 key 应该失效"

    # 吊销过的记录重新配对（网页再次批准）→ 复活成同一条
    code2 = new_user_code()
    auth.start_device_request(code2, secret, "手机", device_id=first["device_id"])
    auth.approve(code2, user["id"])
    second = auth.request_status(code2, secret)
    assert second["device_id"] == first["device_id"]
    assert auth.verify(second["api_key"]) is not None, "重新批准 = 复活这条记录"
    assert len(auth.storage.list_devices(user["id"])) == 1
