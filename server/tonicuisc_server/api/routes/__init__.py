"""路由模块。每个模块暴露一个 ``router``，由 ``api.app.create_app`` 挂上去。

声明顺序有意义：``/api/library/summary`` 必须在 ``/api/library/{kind}`` 之前，
所以 ``create_app`` 里的 include 顺序 = 路由匹配优先级，改动时要留意。
"""

from __future__ import annotations

from . import account, health, library, login, media, search, update

__all__ = ["account", "health", "library", "login", "media", "search", "update"]
