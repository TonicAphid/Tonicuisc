"""网页「记住这台浏览器」：Cookie 指纹 → 设备记录。

服务端重启后内存会话必然清空（API Key 不落盘）。为了不让网页播放器每次都重新配对，
这里存一条**绑定**：``sha256(浏览器指纹)`` → 设备 ID + 账户。

注意存的是**设备指针，不是 key**：恢复时按设备记录**重新签发**一把 key（旧 key 同时
作废），所以 Redis 里翻不出能直接用的凭证——和 ``devices`` 只存 ``sha256(key)`` 一个原则。

吊销 / 删除设备时会顺手清掉指向它的绑定（见 ``DeviceMixin.delete_device``），
所以「设备列表里删掉它」就等于「这台浏览器下次必须重新登录」。
"""

from __future__ import annotations

import time
from typing import Any

from .base import _number, _text


class WebLoginMixin:
    def _weblogin_key(self, token_hash: str) -> str:
        return self._k("weblogin", token_hash)

    def _weblogin_device_key(self, device_id: str) -> str:
        """反查索引：一台设备可能对应多个浏览器指纹（同一台机器换过浏览器）。"""
        return self._k("weblogindev", device_id)

    def remember_web_login(self, token_hash: str, device_id: str, user_id: str | None, ttl: int) -> None:
        """记下「这个浏览器指纹 = 这台设备」，``ttl`` 秒后自动过期。"""
        if not token_hash or not device_id:
            return
        ttl = max(int(ttl), 60)
        pipe = self.client.pipeline(transaction=False)
        pipe.hset(
            self._weblogin_key(token_hash),
            mapping={
                "device_id": _text(device_id),
                "user_id": _text(user_id),
                "created_at": repr(time.time()),
            },
        )
        pipe.expire(self._weblogin_key(token_hash), ttl)
        pipe.sadd(self._weblogin_device_key(device_id), token_hash)
        pipe.expire(self._weblogin_device_key(device_id), ttl)
        pipe.execute()

    def get_web_login(self, token_hash: str) -> dict[str, Any] | None:
        data = self.client.hgetall(self._weblogin_key(token_hash))
        if not data:
            return None
        return {
            "device_id": data.get("device_id") or "",
            "user_id": data.get("user_id") or None,
            "created_at": _number(data.get("created_at")) or 0.0,
        }

    def forget_web_login(self, token_hash: str, device_id: str | None = None) -> None:
        if not token_hash:
            return
        pipe = self.client.pipeline(transaction=False)
        pipe.delete(self._weblogin_key(token_hash))
        if device_id:
            pipe.srem(self._weblogin_device_key(device_id), token_hash)
        pipe.execute()

    def forget_web_logins_for_device(self, device_id: str) -> int:
        """删设备时一起清绑定：不然「删掉的设备」还能靠指纹自动复活。"""
        if not device_id:
            return 0
        index = self._weblogin_device_key(device_id)
        tokens = list(self.client.smembers(index) or [])
        pipe = self.client.pipeline(transaction=False)
        for token_hash in tokens:
            pipe.delete(self._weblogin_key(token_hash))
        pipe.delete(index)
        pipe.execute()
        return len(tokens)
