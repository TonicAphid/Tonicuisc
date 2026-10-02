"""``/login`` 网页：输入设备码 → 选账户或注册 → 批准设备。

不依赖模板引擎，也不依赖 python-multipart：表单用
``application/x-www-form-urlencoded``，服务端自己解析。
"""

from __future__ import annotations

import html
from typing import Any

from .auth import AuthManager, normalize_user_code
from .ratelimit import FailedLoginGuard

STYLE = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { margin: 0; padding: 24px 16px 48px; font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
       background: #f5f5f7; color: #1c1c1e; }
.card { max-width: 420px; margin: 0 auto; background: #fff; border-radius: 16px; padding: 24px;
        box-shadow: 0 8px 30px rgba(0,0,0,.08); }
h1 { font-size: 20px; margin: 0 0 6px; }
p.sub { margin: 0 0 20px; color: #6b7280; font-size: 13px; line-height: 1.5; }
label { display: block; font-size: 13px; margin: 14px 0 6px; color: #374151; }
input[type=text], input[type=password] { width: 100%; padding: 12px 14px; font-size: 16px; border-radius: 10px;
        border: 1px solid #d1d5db; background: #fff; color: inherit; }
input:focus { outline: 2px solid #3f51b5; border-color: transparent; }
button { width: 100%; margin-top: 18px; padding: 13px; font-size: 16px; border: 0; border-radius: 10px;
         background: #3f51b5; color: #fff; font-weight: 600; }
button.secondary { background: #e5e7eb; color: #111827; }
button.link { background: none; color: #3f51b5; font-weight: 500; padding: 6px; }
.account { display: flex; align-items: center; gap: 10px; padding: 12px 14px; border: 1px solid #d1d5db;
           border-radius: 10px; margin-bottom: 8px; cursor: pointer; }
.account input { width: auto; }
.code { font-family: ui-monospace, Consolas, monospace; font-size: 26px; letter-spacing: 3px; font-weight: 700;
        text-align: center; padding: 14px; border-radius: 10px; background: #eef0fb; color: #3f51b5; }
.error { background: #fee2e2; color: #991b1b; padding: 10px 12px; border-radius: 10px; font-size: 13px; margin-bottom: 4px; }
.ok { background: #dcfce7; color: #166534; padding: 14px; border-radius: 10px; font-size: 14px; line-height: 1.6; }
hr { border: 0; border-top: 1px solid #e5e7eb; margin: 24px 0 4px; }
@media (prefers-color-scheme: dark) {
  body { background: #111114; color: #e5e7eb; }
  .card { background: #1c1c1f; box-shadow: none; }
  input[type=text], input[type=password] { background: #26262b; border-color: #3f3f46; }
  .account { border-color: #3f3f46; }
  .code { background: #26262b; }
  button.secondary { background: #2f2f36; color: #e5e7eb; }
}
"""


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex, nofollow">
<title>{html.escape(title)}</title>
<style>{STYLE}</style>
</head><body><div class="card">{body}</div></body></html>"""


def _error(message: str) -> str:
    return f'<div class="error">{html.escape(message)}</div>' if message else ""


def code_step(user_code: str = "", message: str = "") -> str:
    return _page(
        "登录 Tonicuisc",
        f"""
<h1>登录 Tonicuisc</h1>
<p class="sub">在 App 里点「登录」，把显示出来的设备码填到这里。</p>
{_error(message)}
<form method="post" action="/login">
  <input type="hidden" name="action" value="lookup">
  <label for="code">设备码</label>
  <input id="code" name="user_code" type="text" inputmode="latin" autocapitalize="characters" autocomplete="off"
         spellcheck="false" placeholder="A1B1-C1D1" value="{html.escape(user_code)}" required>
  <button type="submit">下一步</button>
</form>
""",
    )


def account_step(user_code: str, message: str = "") -> str:
    """登录页：用户名自己填，不列出服务器上有哪些账户（避免泄露账户名单）。"""
    return _page(
        "登录 Tonicuisc",
        f"""
<h1>登录账户</h1>
<p class="sub">输入你的用户名和密码，批准这台设备。</p>
<div class="code">{html.escape(user_code)}</div>
{_error(message)}
<form method="post" action="/login">
  <input type="hidden" name="action" value="login_existing">
  <input type="hidden" name="user_code" value="{html.escape(user_code)}">
  <label for="user">用户名</label>
  <input id="user" name="username" type="text" autocapitalize="none" autocomplete="username"
         spellcheck="false" required>
  <label for="pw">密码</label>
  <input id="pw" name="password" type="password" autocomplete="current-password" required>
  <button type="submit">登录并批准设备</button>
</form>
<hr>
<h1>注册新账户</h1>
<p class="sub">还没有账户就在这里注册一个，注册完自动批准这台设备。</p>
<form method="post" action="/login">
  <input type="hidden" name="action" value="register">
  <input type="hidden" name="user_code" value="{html.escape(user_code)}">
  <label for="newuser">用户名</label>
  <input id="newuser" name="username" type="text" autocapitalize="none" autocomplete="username"
         pattern="[A-Za-z0-9_.@\\-]{{2,32}}" required>
  <label for="newpw">密码（至少 6 位）</label>
  <input id="newpw" name="password" type="password" autocomplete="new-password" minlength="6" required>
  <button type="submit" class="secondary">注册并批准设备</button>
</form>
""",
    )


def success_step(username: str, device_name: str) -> str:
    return _page(
        "已批准",
        f"""
<h1>已批准</h1>
<div class="ok">
  账户 <b>{html.escape(username)}</b> 已批准设备 <b>{html.escape(device_name)}</b>。<br>
  现在回到 App，它会自动完成登录。
</div>
""",
    )


def rate_limited_step(retry_after: int) -> str:
    return _page(
        "请求太频繁",
        f"""
<h1>请求太频繁</h1>
<div class="error">密码错误次数过多，请在 {retry_after} 秒后再试。</div>
""",
    )


def render(
    request: dict[str, Any],
    auth: AuthManager,
    client_key: str = "",
    guard: "FailedLoginGuard | None" = None,
) -> str:
    """根据表单内容返回页面。request 里是 action / user_code / username / password。"""
    action = str(request.get("action") or "lookup")
    raw_code = str(request.get("user_code") or "")
    user_code = normalize_user_code(raw_code)

    if action == "lookup":
        if not user_code:
            return code_step(raw_code, "设备码格式不对，应该是 A1B1-C1D1 这样。")
        pending = auth.pending_request(user_code)
        if pending is None:
            return code_step(raw_code, "这个设备码不存在或已过期，请在 App 里重新生成。")
        if pending["status"] != "pending":
            return _page("登录 Tonicuisc", '<div class="ok">这台设备已经处理过了。</div>')
        return account_step(user_code)

    if action in {"login_existing", "register"}:
        if not user_code:
            return code_step(raw_code, "缺少设备码。")
        pending = auth.pending_request(user_code)
        if pending is None:
            return code_step(raw_code, "这个设备码不存在或已过期，请在 App 里重新生成。")

        username = str(request.get("username") or "").strip()
        password = str(request.get("password") or "")

        if action == "login_existing":
            if guard is not None:
                retry_after = guard.blocked_for(client_key, username)
                if retry_after:
                    return rate_limited_step(retry_after)
            user = auth.authenticate(username, password)
            if user is None:
                if guard is not None:
                    guard.record_failure(client_key, username)
                return account_step(user_code, "用户名或密码不对。")
            if guard is not None:
                guard.clear(client_key, username)
        else:
            if len(password) < 6:
                return account_step(user_code, "密码至少 6 位。")
            user = auth.register(username, password)
            if user is None:
                return account_step(user_code, "用户名已被占用或格式不对（2-32 位字母数字._@-）。")

        if not auth.approve(user_code, user["id"]):
            return code_step(raw_code, "批准失败，设备码可能已经过期。")
        return success_step(user["username"], str(pending.get("device_name") or "未命名设备"))

    return code_step(raw_code, "未知操作。")
