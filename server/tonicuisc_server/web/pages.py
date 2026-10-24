"""登录页的 HTML 与样式（无模板引擎、无 multipart 依赖）。

只放「怎么画」：每个函数返回一整页字符串，不碰 auth / 存储。
"""

from __future__ import annotations

import html

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
button.link { width: auto; margin-top: 6px; background: none; color: #3f51b5; font-weight: 500; padding: 6px 0; }
.account { display: flex; align-items: center; gap: 10px; padding: 12px 14px; border: 1px solid #d1d5db;
           border-radius: 10px; margin-bottom: 8px; cursor: pointer; }
.account input { width: auto; }
.code { font-family: ui-monospace, Consolas, monospace; font-size: 26px; letter-spacing: 3px; font-weight: 700;
        text-align: center; padding: 14px; border-radius: 10px; background: #eef0fb; color: #3f51b5; }
.error { background: #fee2e2; color: #991b1b; padding: 10px 12px; border-radius: 10px; font-size: 13px; margin-bottom: 4px; }
.ok { background: #dcfce7; color: #166534; padding: 14px; border-radius: 10px; font-size: 14px; line-height: 1.6; }
.hint { background: #fef3c7; color: #92400e; padding: 12px 14px; border-radius: 10px; font-size: 13px; line-height: 1.7; }
.hint code { font-family: ui-monospace, Consolas, monospace; font-size: 12px; background: rgba(0,0,0,.06); padding: 1px 5px; border-radius: 4px; }
.recovery { font-family: ui-monospace, Consolas, monospace; font-size: 17px; letter-spacing: 1px; font-weight: 700;
            text-align: center; padding: 14px 10px; border-radius: 10px; background: #eef0fb; color: #3f51b5;
            word-break: break-all; user-select: all; }
hr { border: 0; border-top: 1px solid #e5e7eb; margin: 24px 0 4px; }
.footer { max-width: 420px; margin: 18px auto 0; text-align: center; font-size: 12px; line-height: 1.6; color: #9ca3af; }
.footer a { color: inherit; text-decoration: none; }
.footer a:hover, .footer a:focus { text-decoration: underline; }
@media (prefers-color-scheme: dark) {
  body { background: #111114; color: #e5e7eb; }
  .card { background: #1c1c1f; box-shadow: none; }
  input[type=text], input[type=password] { background: #26262b; border-color: #3f3f46; }
  .account { border-color: #3f3f46; }
  .code, .recovery { background: #26262b; }
  .hint { background: #3a2f10; color: #fcd34d; }
  .hint code { background: rgba(255,255,255,.08); }
  button.secondary { background: #2f2f36; color: #e5e7eb; }
  .footer { color: #6b7280; }
}
"""


def page(title: str, body: str) -> str:
    # 页脚统一挂 ICP 备案号：登录流程的每个页面（设备码 / 登录 / 注册 / 重置 / 结果）
    # 都走这里，所以只写一处。链接到工信部备案系统是备案要求的展示方式。
    return f"""<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex, nofollow">
<title>{html.escape(title)}</title>
<style>{STYLE}</style>
</head><body><div class="card">{body}</div>
<footer class="footer"><a href="https://beian.miit.gov.cn/" target="_blank" rel="noopener noreferrer">蒙ICP备2026011401号-1</a></footer>
</body></html>"""


def _error(message: str) -> str:
    return f'<div class="error">{html.escape(message)}</div>' if message else ""


def code_step(user_code: str = "", message: str = "") -> str:
    return page(
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
    return page(
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
<form method="post" action="/login">
  <input type="hidden" name="action" value="reset_form">
  <input type="hidden" name="user_code" value="{html.escape(user_code)}">
  <button type="submit" class="link">忘记密码？用恢复码重置</button>
</form>
""",
    )


def reset_step(user_code: str, message: str = "") -> str:
    """忘记密码：用户名 + 恢复码 + 新密码。"""
    return page(
        "重置密码",
        f"""
<h1>重置密码</h1>
<p class="sub">输入用户名和恢复码，再设一个新密码。恢复码用过一次就作废。</p>
<div class="code">{html.escape(user_code)}</div>
{_error(message)}
<form method="post" action="/login">
  <input type="hidden" name="action" value="reset_password">
  <input type="hidden" name="user_code" value="{html.escape(user_code)}">
  <label for="ruser">用户名</label>
  <input id="ruser" name="username" type="text" autocapitalize="none" autocomplete="username"
         spellcheck="false" required>
  <label for="rcode">恢复码</label>
  <input id="rcode" name="recovery_code" type="text" autocapitalize="characters" autocomplete="off"
         spellcheck="false" placeholder="XXXXX-XXXXX-XXXXX-XXXXX" required>
  <label for="rnewpw">新密码（至少 6 位）</label>
  <input id="rnewpw" name="password" type="password" autocomplete="new-password" minlength="6" required>
  <button type="submit">重置密码并批准这台设备</button>
</form>
<div class="hint">
  没有恢复码、或者码已经用掉了？在服务器上生成一个新的：<br>
  <code>python -m tonicuisc_server recovery-code 用户名</code>
</div>
<form method="post" action="/login">
  <input type="hidden" name="action" value="reset_form_back">
  <input type="hidden" name="user_code" value="{html.escape(user_code)}">
  <button type="submit" class="link">← 返回登录</button>
</form>
""",
    )


def reset_done_step(username: str, device_name: str, recovery_code: str, approved: bool) -> str:
    who = html.escape(username)
    if approved:
        note = (
            f"<div class='ok'>账户 <b>{who}</b> 的密码已重置，设备 "
            f"<b>{html.escape(device_name)}</b> 已批准，现在回到 App 它会自动完成登录。</div>"
        )
    else:
        # 密码已经改成功了，只是设备码过期批不了：必须照样把新恢复码给出去，
        # 否则用户改完密码却没拿到下次重置的凭证，等于白改。
        note = (
            f"<div class='ok'>账户 <b>{who}</b> 的密码已经重置好了。</div>"
            "<div class='error'>但批准设备没成功（设备码可能过期了）：回 App 里重新点一次登录，"
            "用<b>新密码</b>走一遍就行，恢复码不用再输。</div>"
        )
    return page(
        "密码已重置",
        f"""
<h1>密码已重置</h1>
{note}
<hr>
<p class="sub">这是<b>新的恢复码</b>，只显示这一次，请立刻抄下来（旧的已经作废）：</p>
<div class="recovery">{html.escape(recovery_code)}</div>
<div class="hint">忘了新密码就用它重置；每用一次都会自动换一个新的。</div>
""",
    )


def success_step(username: str, device_name: str) -> str:
    return page(
        "已批准",
        f"""
<h1>已批准</h1>
<div class="ok">
  账户 <b>{html.escape(username)}</b> 已批准设备 <b>{html.escape(device_name)}</b>。<br>
  现在回到 App，它会自动完成登录。
</div>
""",
    )


def already_handled_step() -> str:
    """码已经被批准 / 拒绝过了（重复点浏览器「后退 + 提交」时会看到）。"""
    return page("登录 Tonicuisc", '<div class="ok">这台设备已经处理过了。</div>')


def rate_limited_step(retry_after: int) -> str:
    return page(
        "请求太频繁",
        f"""
<h1>请求太频繁</h1>
<div class="error">密码错误次数过多，请在 {retry_after} 秒后再试。</div>
""",
    )
