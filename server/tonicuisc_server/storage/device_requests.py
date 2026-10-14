"""设备码登录的中间状态：pending → approved（暂存明文 key）→ 被取走后清掉。"""

from __future__ import annotations

import time
from typing import Any

from .base import _number, _text

class DeviceRequestMixin:
    # --------------------------------------------------------- device requests
    def create_device_request(
        self,
        user_code: str,
        poll_hash: str,
        device_name: str,
        ttl: float,
        device_id: str = "",
    ) -> dict[str, Any] | None:
        """user_code 已被占用（且未过期）时返回 None，让 App 换一个码。

        ``device_id``：App 认领的本机旧设备 ID（可空），批准时用于就地复用旧记录。
        """
        now = time.time()
        seconds = max(int(ttl), 1)
        # 占位 key 和请求本身同寿：过期后码就能被重新用
        if not self.client.set(self._k("dreq", user_code, "lock"), "1", nx=True, ex=seconds):
            return None
        key = self._k("dreq", user_code)
        pipe = self.client.pipeline(transaction=False)
        pipe.hset(
            key,
            mapping={
                "user_code": user_code,
                "poll_hash": poll_hash,
                "device_name": _text(device_name),
                "status": "pending",
                "created_at": repr(now),
                "expires_at": repr(now + ttl),
                "user_id": "",
                "api_key": "",
                "device_id": _text(device_id),
                "claimed_at": "",
            },
        )
        pipe.expire(key, seconds)
        pipe.zadd(self._dreqs_key, {user_code: now})
        pipe.execute()
        return {"user_code": user_code, "device_name": device_name, "expires_at": now + ttl}

    def get_device_request(self, user_code: str) -> dict[str, Any] | None:
        data = self.client.hgetall(self._k("dreq", user_code))
        if not data:
            return None
        data["created_at"] = _number(data.get("created_at")) or 0.0
        data["expires_at"] = _number(data.get("expires_at")) or 0.0
        data["claimed_at"] = _number(data.get("claimed_at"))
        for field in ("user_id", "api_key", "device_id"):
            data[field] = data.get(field) or None
        return data

    def approve_device_request(
        self,
        user_code: str,
        user_id: str,
        api_key: str,
        device_id: str,
    ) -> bool:
        """批准：把明文 key 暂存在请求里，等 App 取走后清掉。"""
        request = self.get_device_request(user_code)
        if request is None or request["status"] != "pending" or request["expires_at"] <= time.time():
            return False
        self.client.hset(
            self._k("dreq", user_code),
            mapping={"status": "approved", "user_id": user_id, "api_key": api_key, "device_id": device_id},
        )
        return True

    def deny_device_request(self, user_code: str) -> bool:
        request = self.get_device_request(user_code)
        if request is None or request["status"] != "pending":
            return False
        self.client.hset(self._k("dreq", user_code), "status", "denied")
        return True

    def clear_device_request_key(self, user_code: str) -> None:
        self.client.hset(
            self._k("dreq", user_code),
            mapping={"api_key": "", "claimed_at": repr(time.time())},
        )

    def drop_expired_device_requests(self) -> int:
        """请求本身靠 Redis 的 TTL 过期，这里只清理索引里的残留。"""
        codes = self.client.zrange(self._dreqs_key, 0, -1)
        gone = [code for code in codes if not self.client.exists(self._k("dreq", code))]
        if gone:
            self.client.zrem(self._dreqs_key, *gone)
        return len(gone)
