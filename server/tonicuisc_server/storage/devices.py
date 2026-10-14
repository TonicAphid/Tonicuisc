"""设备记录与 ``sha256(api_key)`` 反查；吊销 = 直接删记录。"""

from __future__ import annotations

import time
import uuid
from typing import Any

from .base import _integer, _number, _text

class DeviceMixin:
    # ---------------------------------------------------------------- devices
    def create_device(
        self,
        name: str,
        key_hash: str,
        device_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """只存 key 的哈希，明文 key 不落库。

        ``device_id`` 是 App 重新配对时认领的旧 ID：那条记录还在且属于同一个账户
        → 就地复用（换 key、复活），旧 key 的反查一并删掉；记录不存在就用这个 ID 建；
        记录属于**别的账户**则无视认领、另发一个新 ID（不能让配对申请认走别人的设备）。
        """
        claimed = device_id or uuid.uuid4().hex
        now = time.time()
        old = self._device(claimed) if device_id else None
        reused = old is not None and (old.get("user_id") or None) == (user_id or None)
        device_id = claimed if reused or old is None else uuid.uuid4().hex
        pipe = self.client.pipeline(transaction=False)
        if reused:
            pipe.delete(self._k("devicehash", str(old.get("key_hash") or "")))  # 旧 key 作废
        pipe.hset(
            self._k("device", device_id),
            mapping={
                "id": device_id,
                "name": _text(name),
                "key_hash": key_hash,
                "created_at": repr(old.get("created_at") or now) if reused else repr(now),
                "last_seen": repr(now),
                "revoked": "0",  # 复用即重新批准，吊销状态一并解除
                "user_id": _text(user_id),
            },
        )
        pipe.set(self._k("devicehash", key_hash), device_id)
        pipe.zadd(self._devices_key, {device_id: now})
        pipe.execute()
        return {
            "id": device_id,
            "name": name,
            "created_at": (old.get("created_at") or now) if reused else now,
            "last_seen": now,
            "revoked": 0,
            "user_id": user_id,
        }

    def _device_row(self, data: dict[str, Any]) -> dict[str, Any]:
        row = dict(data)
        row["revoked"] = _integer(row.get("revoked")) or 0
        row["created_at"] = _number(row.get("created_at")) or 0.0
        row["last_seen"] = _number(row.get("last_seen")) or 0.0
        row["user_id"] = row.get("user_id") or None
        return row

    def _device(self, device_id: str) -> dict[str, Any] | None:
        data = self.client.hgetall(self._k("device", device_id))
        return None if not data else self._device_row(data)

    def _username_of(self, user_id: str | None) -> str | None:
        if not user_id:
            return None
        return self.client.hget(self._k("user", user_id), "username") or None

    def find_device_by_hash(self, key_hash: str) -> dict[str, Any] | None:
        device_id = self.client.get(self._k("devicehash", key_hash))
        if not device_id:
            return None
        row = self._device(device_id)
        if row is None:
            return None
        row["username"] = self._username_of(row.get("user_id"))
        return row

    def touch_device(self, device_id: str) -> None:
        self.client.hset(self._k("device", device_id), "last_seen", repr(time.time()))

    def list_devices(self, user_id: str | None = None, *, own_only: bool = True) -> list[dict[str, Any]]:
        """设备列表。

        ``own_only`` 时只给「属于这个账户的 + 没有账户的孤儿设备」——否则任何一台
        配对成功的设备都能看到全站账户名单（``username`` 也在里面）。
        ``own_only=False`` 只在鉴权关闭（没有账户概念）时用。
        """
        device_ids = self.client.zrange(self._devices_key, 0, -1)
        if not device_ids:
            return []
        pipe = self.client.pipeline(transaction=False)
        for device_id in device_ids:
            pipe.hgetall(self._k("device", device_id))
        rows = pipe.execute()
        items: list[dict[str, Any]] = []
        for row in rows:
            if not row:
                continue
            device = self._device_row(row)
            owner = device.get("user_id")
            if own_only and owner and owner != user_id:
                continue
            device.pop("key_hash", None)  # 哈希本身别发出去
            device["username"] = self._username_of(owner)
            items.append(device)
        return items

    def revoke_device(self, device_id: str) -> bool:
        if not self.client.exists(self._k("device", device_id)):
            return False
        self.client.hset(self._k("device", device_id), "revoked", "1")
        return True

    def delete_device(self, device_id: str, user_id: str | None = None, *, own_only: bool = True) -> bool:
        """直接从库里删掉这台设备，它的 key 立刻失效（查不到哈希了）。

        ``own_only`` 时只允许删本账户（或无主）的设备，防止跨账户吊销。
        """
        row = self._device(device_id)
        if row is None:
            return False
        owner = row.get("user_id")
        if own_only and owner and owner != user_id:
            return False
        pipe = self.client.pipeline(transaction=False)
        pipe.delete(self._k("device", device_id))
        pipe.zrem(self._devices_key, device_id)
        if row.get("key_hash"):
            pipe.delete(self._k("devicehash", row["key_hash"]))
        pipe.execute()
        return True

    def delete_devices(self, *, without_user: bool = False, revoked: bool = False) -> int:
        """清理设备记录（默认什么都不删）。

        - ``without_user``：删掉没有关联账户的（旧版配对流程、测试残留）
        - ``revoked``：删掉已吊销的
        """
        if not without_user and not revoked:
            return 0
        victims: list[str] = []
        for device_id in self.client.zrange(self._devices_key, 0, -1):
            row = self._device(device_id)
            if row is None:
                victims.append(device_id)  # 悬空记录，顺手清掉
                continue
            if without_user and (not row.get("user_id") or not self.client.exists(self._k("user", row["user_id"]))):
                victims.append(device_id)
            elif revoked and row.get("revoked"):
                victims.append(device_id)
        for device_id in victims:
            self.delete_device(device_id, own_only=False)  # 清理是全库的，不做归属过滤
        return len(victims)
