"""请求级依赖：服务 / 鉴权单例、当前账户、限速用的来源标识。

`_service` / `_auth` 是**进程级单例**（测试通过给这两个名字赋值来注入夹具），
所以这里不用 ``lru_cache``——那玩意测不了。
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from ..auth import AuthManager
from ..core.config import SETTINGS
from ..services import MusicService

#: 鉴权关闭（``TONICUISC_AUTH=0``）时收藏数据归到这个「本地」账户下
LOCAL_USER_ID = "local"

_service: MusicService | None = None
_auth: AuthManager | None = None


def get_service() -> MusicService:
    global _service
    if _service is None:
        _service = MusicService()
    return _service


def get_auth() -> AuthManager:
    global _auth
    if _auth is None:
        _auth = AuthManager(get_service().storage)
    return _auth


def client_key(request: Request) -> str:
    """限速用的来源标识。

    默认信任反向代理（Caddy）写的 ``X-Forwarded-For``；
    如果把端口直接暴露到公网，记得设 ``TONICUISC_TRUST_PROXY=0``，
    否则攻击者可以伪造这个头绕过限速。
    """
    if SETTINGS.trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]


def request_user_id(request: Request) -> str | None:
    """当前请求所属账户；没绑账户（孤儿设备 / 鉴权关闭）返回 None。"""
    device = getattr(request.state, "device", {}) or {}
    user_id = device.get("user_id")
    return str(user_id) if user_id else None


def current_user(request: Request) -> str:
    """从已校验的设备里取账户；设备没绑账户就没法用收藏类功能。

    鉴权关闭时没有「设备」概念，退到 :data:`LOCAL_USER_ID`，
    保证 ``TONICUISC_AUTH=0`` 下收藏接口也能用（README 承诺过「任何人可调」）。
    """
    user_id = request_user_id(request)
    if user_id:
        return user_id
    if not get_auth().enabled:
        return LOCAL_USER_ID
    raise HTTPException(status_code=400, detail="这台设备没有关联账户，请重新登录")
