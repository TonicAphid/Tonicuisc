"""``/login`` 网页路由：只负责 HTTP 细节，页面与流程在 ``web`` 包里。"""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ...auth import DeviceStatus, normalize_user_code
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
async def login_page(code: str = "") -> HTMLResponse:
    """设备码登录页（浏览器打开）。

    带 ``?code=A1B1-C1D1`` 时**直接跳到「登录 / 注册」表单**——网页播放器的
    「打开登录页批准」按钮就是这么拼链接的（以前这一步是 Node 入口替用户提交一次
    lookup 表单，现在 Python 自己就能算，少一次往返）。
    """
    if not code:
        return HTMLResponse(pages.code_step(), headers=LOGIN_HEADERS)

    user_code = normalize_user_code(code)
    if not user_code:
        return HTMLResponse(pages.code_step(code, "设备码格式不对，应该是 A1B1-C1D1 这样。"), headers=LOGIN_HEADERS)
    pending = await asyncio.to_thread(get_auth().pending_request, user_code)
    if pending is None:
        return HTMLResponse(
            pages.code_step(user_code, "这个设备码不存在或已过期，请重新生成。"), headers=LOGIN_HEADERS
        )
    if pending["status"] != DeviceStatus.PENDING:
        return HTMLResponse(pages.already_handled_step(), headers=LOGIN_HEADERS)
    return HTMLResponse(pages.account_step(user_code), headers=LOGIN_HEADERS)


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
