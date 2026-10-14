"""``/login`` 网页路由：只负责 HTTP 细节，页面与流程在 ``web`` 包里。"""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ...web import login as weblogin
from ...web import pages
from ..deps import client_key, get_auth
from ..limiters import failed_login_guard, login_limiter

router = APIRouter()

#: ``/login`` 的响应头：这个页面能输密码，不许被别的站点 iframe（点击劫持）
LOGIN_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; img-src 'none'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


@router.get("/login", response_class=HTMLResponse)
async def login_page() -> HTMLResponse:
    """设备码登录页（浏览器打开）。"""
    return HTMLResponse(pages.code_step(), headers=LOGIN_HEADERS)


@router.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request) -> HTMLResponse:
    source = client_key(request)
    allowed, retry_after = login_limiter.check(source)
    if not allowed:
        return HTMLResponse(pages.rate_limited_step(retry_after), status_code=429, headers=LOGIN_HEADERS)
    raw = (await request.body()).decode("utf-8", "ignore")
    form = {key: values[0] for key, values in parse_qs(raw).items() if values}
    page = await asyncio.to_thread(weblogin.render, form, get_auth(), source, failed_login_guard)
    return HTMLResponse(page, headers=LOGIN_HEADERS)
