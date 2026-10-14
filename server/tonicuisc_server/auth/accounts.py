"""账户：注册、密码校验、恢复码重置。

这里只关心「账户本身」，设备与登录流程在 ``device_flow.py``。
"""

from __future__ import annotations

from typing import Any

from ..storage import Storage
from .codes import hash_recovery_code, new_recovery_code, verify_recovery_code
from .tokens import dummy_password_hash, hash_password, valid_username, verify_password


class AccountService:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    # ------------------------------------------------------------------ 基础
    def register(self, username: str, password: str) -> dict[str, Any] | None:
        username = (username or "").strip()
        if not valid_username(username) or len(password or "") < 6:
            return None
        return self.storage.create_user(username, hash_password(password))

    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        user = self.storage.find_user((username or "").strip())
        if user is None:
            verify_password(password or "", dummy_password_hash())  # 时间对齐，别让人靠耗时猜用户名
            return None
        if not verify_password(password or "", user["password_hash"]):
            return None
        self.storage.touch_user_login(user["id"])
        return {"id": user["id"], "username": user["username"]}

    def users(self) -> list[dict[str, Any]]:
        return self.storage.list_users()

    # ------------------------------------------------------------- 忘记密码
    def set_password(self, username: str, new_password: str) -> dict[str, Any] | None:
        """本机管理命令用：不问旧密码直接换。

        能跑这条命令的人本来就能改 Redis，所以这里不做额外验证（跟 ``revoke`` 一个性质）。
        """
        user = self.storage.find_user((username or "").strip())
        if user is None or len(new_password or "") < 6:
            return None
        self.storage.update_user(user["id"], password_hash=hash_password(new_password))
        return {"id": user["id"], "username": user["username"]}

    def issue_recovery_code(self, username: str) -> str | None:
        """生成（或换掉）一个恢复码，明文只返回这一次，旧的作废。"""
        user = self.storage.find_user((username or "").strip())
        if user is None:
            return None
        code = new_recovery_code()
        self.storage.update_user(user["id"], recovery_hash=hash_recovery_code(code))
        return code

    def reset_password(
        self, username: str, recovery_code: str, new_password: str
    ) -> tuple[dict[str, Any], str] | None:
        """恢复码对了就换密码，并**换一个新的恢复码**（一次性，用过即废）。

        返回 ``(账户, 新恢复码明文)``；密码太短、账户不存在、没设过恢复码、
        码不对，一律返回 None（调用方据此记失败、算限速）。
        """
        if len(new_password or "") < 6:
            return None
        user = self.storage.find_user((username or "").strip())
        stored = str((user or {}).get("recovery_hash") or "")
        if not stored:
            # 跟 authenticate 一样抹平耗时：没设恢复码的账户不该更快被发现
            verify_recovery_code(recovery_code or "", dummy_password_hash())
            return None
        if not verify_recovery_code(recovery_code, stored):
            return None
        new_code = new_recovery_code()
        self.storage.update_user(
            user["id"],
            password_hash=hash_password(new_password),
            recovery_hash=hash_recovery_code(new_code),
        )
        return {"id": user["id"], "username": user["username"]}, new_code
