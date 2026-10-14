"""设备码登录流程（OAuth Device Authorization 的简化版，方向反过来）。

1. App 生成设备码 + 只有它知道的 ``poll_secret``，``POST /api/device/start`` 登记；
2. 用户在 ``/login`` 输入设备码，选账户 / 注册，批准这台设备；
3. App 轮询 ``/api/device/status``，取走一次性明文 key。

**一台设备只留一条记录**：App 若带了自己本机的 ``device_id``，且那条记录还在、
还属于同一个账户，就就地复用（换新 key、旧 key 同时作废）。
"""

from __future__ import annotations

import hmac
import threading
import time

from ..storage import Storage
from .codes import normalize_user_code
from .constants import DEVICE_ID_RE, USER_CODE_RE, DeviceStatus
from .tokens import hash_key, new_api_key


class DeviceFlow:
    def __init__(self, storage: Storage, ttl: int) -> None:
        self.storage = storage
        self.ttl = ttl
        #: 领取 api_key 时「读 + 清」的互斥
        self._claim_lock = threading.Lock()

    # ------------------------------------------------------------------ App 侧
    def start(
        self,
        user_code: str,
        poll_secret: str,
        device_name: str,
        device_id: str = "",
    ) -> dict | None:
        """App 发起：登记一个待批准的设备码。码重复或格式不对返回 None。

        ``device_id`` 是 App 本机存着的旧设备 ID（重新配对时才带）：格式不对就当没带，
        正确格式也只是「申请复用」，能不能真复用在 approve 时按记录归属核对。
        """
        code = normalize_user_code(user_code)
        if not USER_CODE_RE.match(code) or not poll_secret:
            return None
        claimed = (device_id or "").strip().lower()
        if not DEVICE_ID_RE.fullmatch(claimed):
            claimed = ""
        return self.storage.create_device_request(
            user_code=code,
            poll_hash=hash_key(poll_secret),
            device_name=(device_name or "未命名设备").strip()[:60],
            ttl=self.ttl,
            device_id=claimed,
        )

    def request_status(self, user_code: str, poll_secret: str) -> dict:
        """App 轮询：pending / approved / denied / expired / claimed。

        「读 key → 清 key」必须整段上锁：断线重连、多进程轮询同时到，
        否则两个请求都能把明文 key 拿走（README 承诺只给一次）。
        """
        code = normalize_user_code(user_code)
        with self._claim_lock:
            request = self.storage.get_device_request(code)
            if request is None:
                return {"status": DeviceStatus.EXPIRED}
            if not hmac.compare_digest(str(request["poll_hash"]), hash_key(poll_secret or "")):
                # 别人拿着同一个码来问，什么也不给
                return {"status": DeviceStatus.EXPIRED}
            if request["expires_at"] < time.time() and request["status"] == DeviceStatus.PENDING:
                return {"status": DeviceStatus.EXPIRED}
            if request["status"] != DeviceStatus.APPROVED:
                return {"status": request["status"]}
            if not request["api_key"]:
                # 已经取走过一次了
                return {"status": DeviceStatus.CLAIMED}
            api_key = request["api_key"]
            self.storage.clear_device_request_key(code)
        user = self.storage.get_user(request["user_id"]) if request["user_id"] else None
        return {
            "status": DeviceStatus.APPROVED,
            "api_key": api_key,
            "device_id": request["device_id"],
            "username": (user or {}).get("username"),
        }

    # ----------------------------------------------------------------- 网页侧
    def approve(self, user_code: str, user_id: str, device_name: str | None = None) -> bool:
        """网页端批准：为这台设备建一条记录并发一把 key（复用见模块 docstring）。"""
        code = normalize_user_code(user_code)
        request = self.storage.get_device_request(code)
        if request is None or request["status"] != DeviceStatus.PENDING or request["expires_at"] < time.time():
            return False
        api_key = new_api_key()
        device = self.storage.create_device(
            name=(device_name or request["device_name"] or "未命名设备")[:60],
            key_hash=hash_key(api_key),
            device_id=str(request.get("device_id") or "") or None,
            user_id=user_id,
        )
        if not self.storage.approve_device_request(code, user_id, api_key, device["id"]):
            # 批准这一步失败（码刚好过期/被抢）：把刚建的设备删掉，
            # 不然会留下一条「有账户、却没人持有 key」的孤儿设备，cleanup 永远删不到
            self.storage.delete_device(device["id"], own_only=False)
            return False
        return True

    def deny(self, user_code: str) -> bool:
        return self.storage.deny_device_request(normalize_user_code(user_code))

    def pending_request(self, user_code: str) -> dict | None:
        request = self.storage.get_device_request(normalize_user_code(user_code))
        if request is None or request["expires_at"] < time.time():
            return None
        return request
