"""AuthManager 单元测试：配对码一次性、过期、哈希校验。"""

from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path

import pytest

from tonicuisc_server.auth import AuthManager, hash_key, new_api_key
from tonicuisc_server.storage import Storage


@pytest.fixture()
def auth():
    root = Path(__file__).resolve().parent / "_scratch" / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    store = Storage(root / "test.db")
    manager = AuthManager(store, ttl=300)
    try:
        yield manager
    finally:
        store.close()
        shutil.rmtree(root, ignore_errors=True)


def test_pairing_code_format(auth: AuthManager) -> None:
    code, expires = auth.current_code()
    assert len(code) == 6 and code.isdigit()
    assert expires > time.time()


def test_pair_returns_key_once(auth: AuthManager) -> None:
    code, _ = auth.current_code()
    result = auth.pair(code, "手机")
    assert result is not None
    assert len(result["api_key"]) >= 32

    # 配对码用过就废
    assert auth.pair(code, "另一台") is None


def test_wrong_code_rejected(auth: AuthManager) -> None:
    code, _ = auth.current_code()
    wrong = "000000" if code != "000000" else "111111"
    assert auth.pair(wrong, "陌生人") is None


def test_expired_code_rejected(auth: AuthManager) -> None:
    code, _ = auth.current_code()
    auth._code_expires = time.time() - 1  # 手动过期
    assert auth.pair(code, "迟到的设备") is None


def test_verify_accepts_own_key_only(auth: AuthManager) -> None:
    code, _ = auth.current_code()
    api_key = auth.pair(code, "手机")["api_key"]

    assert auth.verify(api_key) is not None
    assert auth.verify(new_api_key()) is None
    assert auth.verify("") is None
    assert auth.verify(None) is None


def test_revoked_device_fails_verify(auth: AuthManager) -> None:
    code, _ = auth.current_code()
    result = auth.pair(code, "手机")
    assert auth.verify(result["api_key"]) is not None

    auth.storage.revoke_device(result["device_id"])
    assert auth.verify(result["api_key"]) is None


def test_storage_keeps_only_hash(auth: AuthManager) -> None:
    code, _ = auth.current_code()
    api_key = auth.pair(code, "手机")["api_key"]

    device = auth.storage.find_device_by_hash(hash_key(api_key))
    assert device is not None
    assert device["key_hash"] == hash_key(api_key)
    assert api_key not in device["key_hash"]

    listed = auth.storage.list_devices()
    assert listed and "key_hash" not in listed[0], "列设备时不能带出哈希"
