"""HTTP 层：应用装配、依赖、中间件与路由。

* :mod:`~tonicuisc_server.api.app` —— ``create_app()`` / ``app``
* :mod:`~tonicuisc_server.api.deps` —— 服务与鉴权单例、当前账户
* :mod:`~tonicuisc_server.api.middleware` —— 鉴权闸门 + 访问日志
* :mod:`~tonicuisc_server.api.routes` —— 各业务路由
"""

from __future__ import annotations

from .app import app, create_app
from .deps import get_auth, get_service
from .limiters import (
    device_start_limiter,
    failed_login_guard,
    login_limiter,
    reset_limiters,
)

__all__ = [
    "app",
    "create_app",
    "get_auth",
    "get_service",
    "login_limiter",
    "device_start_limiter",
    "failed_login_guard",
    "reset_limiters",
]
