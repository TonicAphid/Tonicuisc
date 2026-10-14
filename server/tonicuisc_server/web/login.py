"""``/login`` 的流程：设备码 → 选账户 / 注册 / 恢复码重置 → 批准设备。

以前这里是一个 95 行的 ``if/elif`` 链，每个分支都要重复一遍「设备码为空 → 回首页」
「查不到这个码 → 回首页」；限速三件套（查锁定 / 记失败 / 清失败）也抄了两份。
现在：

* :class:`LoginRequest` 只解析一次表单；
* :func:`_pending` 统一做「取待批请求」的前置检查；
* 每个 action 一个 handler，:data:`HANDLERS` 是派发表，:func:`render` 只负责派发。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..auth import AuthManager, DeviceStatus, normalize_user_code
from ..auth.ratelimit import FailedLoginGuard
from . import pages


@dataclass(frozen=True)
class LoginRequest:
    """一次表单提交（字段只解析一次，handler 之间不用再互相传 dict）。"""

    action: str = "lookup"
    raw_code: str = ""
    user_code: str = ""
    username: str = ""
    password: str = ""
    recovery_code: str = ""

    @classmethod
    def from_form(cls, form: dict[str, Any]) -> LoginRequest:
        raw = str(form.get("user_code") or "")
        return cls(
            action=str(form.get("action") or "lookup"),
            raw_code=raw,
            user_code=normalize_user_code(raw),
            username=str(form.get("username") or "").strip(),
            password=str(form.get("password") or ""),
            recovery_code=str(form.get("recovery_code") or ""),
        )


@dataclass
class LoginContext:
    auth: AuthManager
    request: LoginRequest
    client_key: str = ""
    guard: FailedLoginGuard | None = None


# ------------------------------------------------------------------ 小工具
def _pending(ctx: LoginContext) -> tuple[dict | None, str]:
    """统一前置：取出这次要批准的待批请求。

    返回 ``(pending, 页面)``；``pending`` 为 None 时，页面就是要直接返回的错误页。
    """
    req = ctx.request
    if not req.user_code:
        return None, pages.code_step(req.raw_code, "缺少设备码。")
    pending = ctx.auth.pending_request(req.user_code)
    if pending is None:
        return None, pages.code_step(req.raw_code, "这个设备码不存在或已过期，请在 App 里重新生成。")
    return pending, ""


def _blocked_page(ctx: LoginContext, username: str) -> str:
    """还在锁定期里就返回限速页面，否则返回空串。"""
    if ctx.guard is None:
        return ""
    retry_after = ctx.guard.blocked_for(ctx.client_key, username)
    return pages.rate_limited_step(retry_after) if retry_after else ""


def _forgot(ctx: LoginContext, username: str) -> None:
    if ctx.guard is not None:
        ctx.guard.record_failure(ctx.client_key, username)


def _forgive(ctx: LoginContext, username: str) -> None:
    if ctx.guard is not None:
        ctx.guard.clear(ctx.client_key, username)


def _device_name(pending: dict) -> str:
    return str(pending.get("device_name") or "未命名设备")


def _approve(ctx: LoginContext, user: dict, pending: dict) -> str:
    if not ctx.auth.approve(ctx.request.user_code, user["id"]):
        return pages.code_step(ctx.request.raw_code, "批准失败，设备码可能已经过期。")
    return pages.success_step(user["username"], _device_name(pending))


# ------------------------------------------------------------------ handlers
def _lookup(ctx: LoginContext) -> str:
    """第一步：把设备码换成「登录 / 注册」表单。"""
    req = ctx.request
    if not req.user_code:
        return pages.code_step(req.raw_code, "设备码格式不对，应该是 A1B1-C1D1 这样。")
    pending = ctx.auth.pending_request(req.user_code)
    if pending is None:
        return pages.code_step(req.raw_code, "这个设备码不存在或已过期，请在 App 里重新生成。")
    if pending["status"] != DeviceStatus.PENDING:
        return pages.already_handled_step()
    return pages.account_step(req.user_code)


def _login_existing(ctx: LoginContext) -> str:
    pending, error = _pending(ctx)
    if pending is None:
        return error
    blocked = _blocked_page(ctx, ctx.request.username)
    if blocked:
        return blocked
    user = ctx.auth.authenticate(ctx.request.username, ctx.request.password)
    if user is None:
        _forgot(ctx, ctx.request.username)
        return pages.account_step(ctx.request.user_code, "用户名或密码不对。")
    _forgive(ctx, ctx.request.username)
    return _approve(ctx, user, pending)


def _register(ctx: LoginContext) -> str:
    pending, error = _pending(ctx)
    if pending is None:
        return error
    req = ctx.request
    if len(req.password) < 6:
        return pages.account_step(req.user_code, "密码至少 6 位。")
    # 注册也要过同一把锁：不然「用户名已被占用」就是现成的枚举接口，
    # 登录那边费力用假哈希抹平耗时就白做了
    blocked = _blocked_page(ctx, req.username)
    if blocked:
        return blocked
    user = ctx.auth.register(req.username, req.password)
    if user is None:
        _forgot(ctx, req.username)
        return pages.account_step(req.user_code, "用户名已被占用或格式不对（2-32 位字母数字._@-）。")
    _forgive(ctx, req.username)
    return _approve(ctx, user, pending)


def _reset_form(ctx: LoginContext) -> str:
    pending, error = _pending(ctx)
    return error if pending is None else pages.reset_step(ctx.request.user_code)


def _reset_form_back(ctx: LoginContext) -> str:
    pending, error = _pending(ctx)
    return error if pending is None else pages.account_step(ctx.request.user_code)


def _reset_password(ctx: LoginContext) -> str:
    pending, error = _pending(ctx)
    if pending is None:
        return error
    req = ctx.request
    blocked = _blocked_page(ctx, req.username)
    if blocked:
        return blocked
    if len(req.password) < 6:
        return pages.reset_step(req.user_code, "新密码至少 6 位。")
    result = ctx.auth.reset_password(req.username, req.recovery_code, req.password)
    if result is None:
        _forgot(ctx, req.username)
        return pages.reset_step(req.user_code, "用户名或恢复码不对（恢复码用过一次就作废了，要用最新的那个）。")
    _forgive(ctx, req.username)
    user, new_code = result
    approved = ctx.auth.approve(req.user_code, user["id"])
    return pages.reset_done_step(user["username"], _device_name(pending), new_code, approved)


def _unknown(ctx: LoginContext) -> str:
    return pages.code_step(ctx.request.raw_code, "未知操作。")


#: action → handler。表单里多一个 ``action`` 就多一条，不用再动分支逻辑。
HANDLERS: dict[str, Callable[[LoginContext], str]] = {
    "lookup": _lookup,
    "login_existing": _login_existing,
    "register": _register,
    "reset_form": _reset_form,
    "reset_form_back": _reset_form_back,
    "reset_password": _reset_password,
}


def render(
    form: dict[str, Any],
    auth: AuthManager,
    client_key: str = "",
    guard: FailedLoginGuard | None = None,
) -> str:
    """表单 → 页面（签名保持老样子，``api`` 层不用改）。"""
    request = LoginRequest.from_form(form)
    ctx = LoginContext(auth=auth, request=request, client_key=client_key, guard=guard)
    return HANDLERS.get(request.action, _unknown)(ctx)
