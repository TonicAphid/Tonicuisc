"""``AuthManager``：账户 + 设备码登录 + key 校验的组合门面。

对外保持老 API（``register`` / ``start_device_request`` / ``verify`` …），
内部把职责交给 :class:`~tonicuisc_server.auth.accounts.AccountService` 与
:class:`~tonicuisc_server.auth.device_flow.DeviceFlow`——调用方不需要知道它们的存在。
"""

from __future__ import annotations

import time
from typing import Any

from ..core.config import SETTINGS
from ..core.constants import TOUCH_INTERVAL_SECONDS
from ..storage import Storage
from .accounts import AccountService
from .device_flow import DeviceFlow
from .tokens import hash_key


class AuthManager:
    def __init__(self, storage: Storage, ttl: int | None = None, enabled: bool | None = None) -> None:
        self.storage = storage
        #: 设备码（登录请求）有效期
        self.ttl = SETTINGS.device_code_ttl if ttl is None else ttl
        self.enabled = SETTINGS.auth_enabled if enabled is None else enabled
        self.accounts = AccountService(storage)
        self.devices = DeviceFlow(storage, self.ttl)

    # ------------------------------------------------------------------ 账户
    def register(self, username: str, password: str) -> dict[str, Any] | None:
        return self.accounts.register(username, password)

    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        return self.accounts.authenticate(username, password)

    def users(self) -> list[dict[str, Any]]:
        return self.accounts.users()

    def set_password(self, username: str, new_password: str) -> dict[str, Any] | None:
        return self.accounts.set_password(username, new_password)

    def issue_recovery_code(self, username: str) -> str | None:
        return self.accounts.issue_recovery_code(username)

    def reset_password(
        self, username: str, recovery_code: str, new_password: str
    ) -> tuple[dict[str, Any], str] | None:
        return self.accounts.reset_password(username, recovery_code, new_password)

    # ------------------------------------------------------------- 设备码流程
    def start_device_request(
        self,
        user_code: str,
        poll_secret: str,
        device_name: str,
        device_id: str = "",
    ) -> dict[str, Any] | None:
        return self.devices.start(user_code, poll_secret, device_name, device_id)

    def request_status(self, user_code: str, poll_secret: str) -> dict[str, Any]:
        return self.devices.request_status(user_code, poll_secret)

    def approve(self, user_code: str, user_id: str, device_name: str | None = None) -> bool:
        return self.devices.approve(user_code, user_id, device_name)

    def deny(self, user_code: str) -> bool:
        return self.devices.deny(user_code)

    def pending_request(self, user_code: str) -> dict[str, Any] | None:
        return self.devices.pending_request(user_code)

    # ------------------------------------------------------------------ 校验
    def verify(self, api_key: str | None) -> dict[str, Any] | None:
        """校验设备 key；顺带刷新 ``last_seen``。

        ``last_seen`` **节流写**：每个请求都写一次的话，翻一屏列表就是几十次 Redis 写，
        而「最后使用时间」精确到秒没有任何意义。冷却期内直接跳过写入。
        """
        if not api_key:
            return None
        device = self.storage.find_device_by_hash(hash_key(api_key))
        if device is None or device.get("revoked"):
            return None
        now = time.time()
        if now - float(device.get("last_seen") or 0.0) >= TOUCH_INTERVAL_SECONDS:
            self.storage.touch_device(str(device["id"]))
        return device
