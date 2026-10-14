"""HTTP 中间件：鉴权闸门 + 访问日志。

两个都要用 ``app.middleware("http")`` 注册；**注册顺序决定嵌套顺序**，
``access_log`` 后注册 → 在最外层 → 连 401 的响应也会被记一行耗时。
"""

from __future__ import annotations

import asyncio
import time

from fastapi import Request
from fastapi.responses import JSONResponse

from ..auth import API_KEY_HEADER, PUBLIC_PATHS
from .deps import get_auth


async def auth_guard(request: Request, call_next):
    """除 PUBLIC_PATHS 外，所有 ``/api`` 请求都要带设备 key。"""
    auth = get_auth()
    path = request.url.path
    if auth.enabled and path.startswith("/api") and path not in PUBLIC_PATHS:
        device = await asyncio.to_thread(auth.verify, request.headers.get(API_KEY_HEADER))
        if device is None:
            return JSONResponse({"detail": "未授权：请先在 App 里配对设备"}, status_code=401)
        request.state.device = device
    return await call_next(request)


async def access_log(request: Request, call_next):
    """给每个 ``/api`` 请求打一行耗时日志：``[access] GET /api/search 200 5.482s``。

    **只打路径、不打 query**：``/api/device/status`` 的 ``poll_secret`` 不能进日志。
    """
    started = time.perf_counter()
    response = await call_next(request)
    if request.url.path.startswith("/api"):
        elapsed = time.perf_counter() - started
        print(f"[access] {request.method} {request.url.path} {response.status_code} {elapsed:.3f}s", flush=True)
    return response
