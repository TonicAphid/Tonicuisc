"""密码 / API Key 的哈希与生成。

* 密码用 **PBKDF2-HMAC-SHA256（20 万次迭代 + 随机盐）**；
* 设备 API Key 是 256 bit 随机串，库里只存 ``sha256(key)``——key 本身够随机，
  不需要慢哈希（慢哈希是给「人想出来的低熵口令」用的）。
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading

from .constants import PASSWORD_ITERATIONS

_USERNAME_RE = re.compile(r"[A-Za-z0-9_.@-]{2,32}")


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


def hash_key(key: str) -> str:
    """sha256(明文 key)，用做设备记录的反查键。"""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def new_api_key() -> str:
    return secrets.token_urlsafe(32)


def valid_username(value: str) -> bool:
    return bool(_USERNAME_RE.fullmatch((value or "").strip()))


#: 「这个用户名不存在」时拿去跑的假哈希：让失败响应的耗时和真密码一致，
#: 否则靠响应时间就能枚举用户名（登录页特意不列账户名单，就是为了防这个）。
_DUMMY_PASSWORD_HASH: str | None = None
_DUMMY_LOCK = threading.Lock()


def dummy_password_hash() -> str:
    """时间对齐用：用户名不存在 / 没设恢复码时，也跑一次同样贵的哈希。"""
    global _DUMMY_PASSWORD_HASH
    if _DUMMY_PASSWORD_HASH is None:
        with _DUMMY_LOCK:
            if _DUMMY_PASSWORD_HASH is None:
                _DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(16))
    return _DUMMY_PASSWORD_HASH
