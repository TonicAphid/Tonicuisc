"""鉴权：常量、哈希、账户、设备码登录流与 API Key 校验。

对外（``api`` / ``web`` / ``cli`` / 测试）只需要 :class:`AuthManager` 和几个工具函数；
实现按职责拆在 ``constants`` / ``tokens`` / ``codes`` / ``accounts`` / ``device_flow`` / ``ratelimit``。
"""

from __future__ import annotations

from .accounts import AccountService
from .codes import (
    hash_recovery_code,
    new_recovery_code,
    new_user_code,
    normalize_recovery_code,
    normalize_user_code,
    verify_recovery_code,
)
from .constants import (
    API_KEY_HEADER,
    CODE_ALPHABET,
    DEVICE_ID_RE,
    PASSWORD_ITERATIONS,
    PUBLIC_PATHS,
    RECOVERY_CODE_RE,
    USER_CODE_RE,
    DeviceStatus,
)
from .device_flow import DeviceFlow
from .manager import AuthManager
from .ratelimit import FailedLoginGuard, SlidingWindowLimiter
from .tokens import (
    dummy_password_hash,
    hash_key,
    hash_password,
    new_api_key,
    valid_username,
    verify_password,
)

__all__ = [
    "AuthManager",
    "AccountService",
    "DeviceFlow",
    "DeviceStatus",
    "FailedLoginGuard",
    "SlidingWindowLimiter",
    "API_KEY_HEADER",
    "PUBLIC_PATHS",
    "CODE_ALPHABET",
    "DEVICE_ID_RE",
    "USER_CODE_RE",
    "RECOVERY_CODE_RE",
    "PASSWORD_ITERATIONS",
    "hash_key",
    "new_api_key",
    "hash_password",
    "verify_password",
    "valid_username",
    "dummy_password_hash",
    "new_user_code",
    "normalize_user_code",
    "new_recovery_code",
    "normalize_recovery_code",
    "hash_recovery_code",
    "verify_recovery_code",
]
