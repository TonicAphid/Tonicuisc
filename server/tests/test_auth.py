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
