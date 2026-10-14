"""设备码登录网页（``/login``）：HTML 渲染 + 表单流程。

* :mod:`~tonicuisc_server.web.pages` —— 只负责「怎么画」
* :mod:`~tonicuisc_server.web.login` —— 只负责「点了之后干什么」

不依赖模板引擎，也不依赖 python-multipart：表单用
``application/x-www-form-urlencoded``，服务端自己解析。
"""

from __future__ import annotations

from . import login, pages

__all__ = ["login", "pages"]
