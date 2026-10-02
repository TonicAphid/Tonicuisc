"""设备配对与 API Key 校验。

设计要点：

- 每台设备一把随机 key（256 bit），**服务端只存 sha256(key)**；
  数据库泄露、开发者看库都还原不出明文。
- 明文 key 只在 ``POST /api/pair`` 的响应里出现一次，之后服务端不再持有，
  日志里也永远不打印。
- 首次配对需要控制台打印的一次性配对码：随机 6 位、默认 5 分钟过期、用过即废。
- 校验用 ``hmac.compare_digest``，避免时序侧信道。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from typing import Any

from .config import SETTINGS
from .storage import Storage

API_KEY_HEADER = "x-api-key"

#: 不需要 API Key 的路径
PUBLIC_PATHS = {"/api/health", "/api/pair"}


def hash_key(key: str) -> str:
    """sha256(明文 key)。key 是 256 bit 随机串，不需要 bcrypt/argon2。"""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def new_api_key() -> str:
    return secrets.token_urlsafe(32)


class AuthManager:
    def __init__(self, storage: Storage, ttl: int | None = None, enabled: bool | None = None) -> None:
        self.storage = storage
        self.ttl = SETTINGS.pairing_ttl if ttl is None else ttl
        self.enabled = SETTINGS.auth_enabled if enabled is None else enabled
        self._code: str | None = None
        self._code_expires = 0.0
        self._lock = threading.Lock()

    # ------------------------------------------------------------- 配对码
    def rotate_code(self) -> str:
        """生成新的配对码（旧的立刻作废）。"""
        with self._lock:
            return self._new_code_locked()

    def _new_code_locked(self) -> str:
        self._code = f"{secrets.randbelow(1_000_000):06d}"
        self._code_expires = time.time() + self.ttl
        return self._code

    def current_code(self) -> tuple[str, float]:
        """当前配对码和过期时间；没有或过期就新生成一个。"""
        with self._lock:
            if self._code is None or self._code_expires < time.time():
                self._new_code_locked()
            return self._code or "", self._code_expires

    # --------------------------------------------------------------- 配对
    def pair(self, code: str, name: str = "") -> dict[str, Any] | None:
        """配对码正确则创建一把新 key；返回的 api_key 只出现这一次。"""
        with self._lock:
            if self._code is None or self._code_expires < time.time():
                return None
            if not hmac.compare_digest(str(code).strip(), self._code):
                return None
            self._code, self._code_expires = None, 0.0  # 一次性
        api_key = new_api_key()
        device = self.storage.create_device(name=(name or "未命名设备").strip()[:60], key_hash=hash_key(api_key))
        return {"device_id": device["id"], "name": device["name"], "api_key": api_key}

    # --------------------------------------------------------------- 校验
    def verify(self, api_key: str | None) -> dict[str, Any] | None:
        if not api_key:
            return None
        device = self.storage.find_device_by_hash(hash_key(api_key))
        if device is None or device.get("revoked"):
            return None
        self.storage.touch_device(str(device["id"]))
        return device


def code_banner(code: str, ttl: int, enabled: bool = True) -> str:
    if not enabled:
        return (
            "\n" + "=" * 52 + "\n"
            "  警告：API Key 校验已关闭 (TONICUISC_AUTH=0)\n"
            "  任何人都能调用本服务的接口\n" + "=" * 52
        )
    minutes = max(1, ttl // 60)
    return (
        "\n" + "=" * 52 + "\n"
        "  Tonicuisc 设备配对\n"
        f"  配对码: {code[:3]} {code[3:]}      有效期 {minutes} 分钟\n"
        "  在 App 里「配对设备」输入此码（一次性，用完即废）\n" + "=" * 52
    )
