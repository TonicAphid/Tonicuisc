"""账户、设备码登录与 API Key 校验。

流程（OAuth Device Authorization 的简化版，方向反过来）：

1. App 自己生成一个 8 位设备码（``A1B1-C1D1``）和一个只有它知道的 ``poll_secret``；
2. App 把设备码显示给用户，用户打开 ``/login`` 输入设备码；
3. 用户在那里**选一个已有账户**或者**注册新账户**，批准这台设备；
4. App 用设备码 + ``poll_secret`` 轮询，拿到属于该账户的设备 API Key。

安全属性：

- 密码用 PBKDF2-HMAC-SHA256（20 万次迭代 + 随机盐）存储；
- 设备 API Key 256 bit 随机，``devices`` 表里只有 ``sha256(key)``；
- 未批准的设备码谁也拿不到 key，``poll_secret`` 让别的进程无法截胡；
- 设备码默认 10 分钟过期，批准过的 key 被取走一次后立刻从请求行里清掉。
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import string
import threading
import time
from typing import Any

from .config import SETTINGS
from .storage import Storage

API_KEY_HEADER = "x-api-key"

#: 不需要 API Key 的路径
PUBLIC_PATHS = {"/api/health", "/api/device/start", "/api/device/status", "/login"}

#: 设备码格式：A1B1-C1D1（8 位大写字母数字 + 中间一个横杠）
USER_CODE_RE = re.compile(r"^[A-Z0-9]{4}-[A-Z0-9]{4}$")
USER_CODE_ALPHABET = string.ascii_uppercase + string.digits

PASSWORD_ITERATIONS = 200_000


# ------------------------------------------------------------------ 密码哈希
def hash_password(password: str, iterations: int = PASSWORD_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


# ------------------------------------------------------------------ key / code
def hash_key(key: str) -> str:
    """sha256(明文 key)。key 是 256 bit 随机串，不需要 bcrypt。"""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def new_api_key() -> str:
    return secrets.token_urlsafe(32)


def new_poll_secret() -> str:
    return secrets.token_urlsafe(24)


def new_user_code() -> str:
    """App 侧的设备码生成器（服务端也用它做「换一个码」的提示）。"""
    raw = "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def normalize_user_code(value: str) -> str:
    """统一成大写、去掉多余字符，用户手输也能对上。"""
    cleaned = re.sub(r"[^A-Za-z0-9]", "", value or "").upper()
    if len(cleaned) == 8:
        return f"{cleaned[:4]}-{cleaned[4:]}"
    return cleaned


def valid_username(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.@-]{2,32}", (value or "").strip()))


class AuthManager:
    def __init__(self, storage: Storage, ttl: int | None = None, enabled: bool | None = None) -> None:
        self.storage = storage
        #: 设备码（登录请求）有效期
        self.ttl = SETTINGS.device_code_ttl if ttl is None else ttl
        self.enabled = SETTINGS.auth_enabled if enabled is None else enabled
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ 账户
    def register(self, username: str, password: str) -> dict[str, Any] | None:
        username = (username or "").strip()
        if not valid_username(username) or len(password or "") < 6:
            return None
        return self.storage.create_user(username, hash_password(password))

    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        user = self.storage.find_user((username or "").strip())
        if user is None or not verify_password(password or "", user["password_hash"]):
            return None
        self.storage.touch_user_login(user["id"])
        return {"id": user["id"], "username": user["username"]}

    def users(self) -> list[dict[str, Any]]:
        return self.storage.list_users()

    # ------------------------------------------------------------- 设备码流程
    def start_device_request(self, user_code: str, poll_secret: str, device_name: str) -> dict[str, Any] | None:
        """App 发起：登记一个待批准的设备码。码重复或格式不对返回 None。"""
        code = normalize_user_code(user_code)
        if not USER_CODE_RE.match(code) or not poll_secret:
            return None
        return self.storage.create_device_request(
            user_code=code,
            poll_hash=hash_key(poll_secret),
            device_name=(device_name or "未命名设备").strip()[:60],
            ttl=self.ttl,
        )

    def request_status(self, user_code: str, poll_secret: str) -> dict[str, Any]:
        """App 轮询：pending / approved / denied / expired。"""
        code = normalize_user_code(user_code)
        request = self.storage.get_device_request(code)
        if request is None:
            return {"status": "expired"}
        if not hmac.compare_digest(str(request["poll_hash"]), hash_key(poll_secret or "")):
            # 别人拿着同一个码来问，什么也不给
            return {"status": "expired"}
        if request["expires_at"] < time.time() and request["status"] == "pending":
            return {"status": "expired"}
        if request["status"] != "approved":
            return {"status": request["status"]}
        if not request["api_key"]:
            # 已经取走过一次了
            return {"status": "claimed"}
        self.storage.clear_device_request_key(code)
        user = self.storage.get_user(request["user_id"]) if request["user_id"] else None
        return {
            "status": "approved",
            "api_key": request["api_key"],
            "device_id": request["device_id"],
            "username": (user or {}).get("username"),
        }

    def approve(self, user_code: str, user_id: str, device_name: str | None = None) -> bool:
        """网页端批准：为这台设备建一条记录并发一把 key。"""
        code = normalize_user_code(user_code)
        request = self.storage.get_device_request(code)
        if request is None or request["status"] != "pending" or request["expires_at"] < time.time():
            return False
        api_key = new_api_key()
        device = self.storage.create_device(
            name=(device_name or request["device_name"] or "未命名设备")[:60],
            key_hash=hash_key(api_key),
            user_id=user_id,
        )
        return self.storage.approve_device_request(code, user_id, api_key, device["id"])

    def deny(self, user_code: str) -> bool:
        return self.storage.deny_device_request(normalize_user_code(user_code))

    def pending_request(self, user_code: str) -> dict[str, Any] | None:
        request = self.storage.get_device_request(normalize_user_code(user_code))
        if request is None or request["expires_at"] < time.time():
            return None
        return request

    # ------------------------------------------------------------------ 校验
    def verify(self, api_key: str | None) -> dict[str, Any] | None:
        if not api_key:
            return None
        device = self.storage.find_device_by_hash(hash_key(api_key))
        if device is None or device.get("revoked"):
            return None
        self.storage.touch_device(str(device["id"]))
        return device
