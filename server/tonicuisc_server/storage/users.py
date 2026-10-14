"""账户：用户名索引（大小写不敏感）、密码 / 恢复码哈希、最近登录时间。"""

from __future__ import annotations

import time
import uuid
from typing import Any

from .base import _number, _text

class UserMixin:
    # ------------------------------------------------------------------ users
    def create_user(self, username: str, password_hash: str, user_id: str | None = None) -> dict[str, Any] | None:
        """用户名已存在时返回 None（大小写不敏感）。"""
        user_id = user_id or uuid.uuid4().hex
        now = time.time()
        if not self.client.set(self._k("username", username.lower()), user_id, nx=True):
            return None
        pipe = self.client.pipeline(transaction=False)
        pipe.hset(
            self._k("user", user_id),
            mapping={
                "id": user_id,
                "username": _text(username),
                "password_hash": password_hash,
                "created_at": repr(now),
                "last_login_at": "",
            },
        )
        pipe.zadd(self._users_key, {user_id: now})
        pipe.execute()
        return {"id": user_id, "username": username, "created_at": now}

    def update_user(self, user_id: str, **fields: str | None) -> bool:
        """改账户上的可变字段（目前只有两个哈希），白名单之外的一律丢掉。"""
        allowed = {"password_hash", "recovery_hash"}
        mapping = {key: _text(value) for key, value in fields.items() if key in allowed and value is not None}
        if not mapping:
            return False
        return bool(self.client.hset(self._k("user", user_id), mapping=mapping))

    def _user(self, user_id: str) -> dict[str, Any] | None:
        data = self.client.hgetall(self._k("user", user_id))
        if not data:
            return None
        data["created_at"] = _number(data.get("created_at")) or 0.0
        data["last_login_at"] = _number(data.get("last_login_at"))
        return data

    def find_user(self, username: str) -> dict[str, Any] | None:
        user_id = self.client.get(self._k("username", (username or "").strip().lower()))
        return None if not user_id else self._user(user_id)

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        return self._user(user_id)

    def list_users(self) -> list[dict[str, Any]]:
        """给登录页用；不带密码哈希。"""
        user_ids = self.client.zrange(self._users_key, 0, -1)
        if not user_ids:
            return []
        pipe = self.client.pipeline(transaction=False)
        for user_id in user_ids:
            pipe.hgetall(self._k("user", user_id))
        rows = pipe.execute()
        items: list[dict[str, Any]] = []
        for row in rows:
            if not row:
                continue
            row.pop("password_hash", None)
            row["created_at"] = _number(row.get("created_at")) or 0.0
            row["last_login_at"] = _number(row.get("last_login_at"))
            items.append(row)
        return sorted(items, key=lambda item: item["created_at"])

    def touch_user_login(self, user_id: str) -> None:
        self.client.hset(self._k("user", user_id), "last_login_at", repr(time.time()))
