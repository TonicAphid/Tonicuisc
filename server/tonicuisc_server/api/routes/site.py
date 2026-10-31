"""内置网页站点：落地页（``/``）、播放器（``/music``）、网页会话、下载跳转。

以前这三件事由 ``webapp/server.js``（Node，零依赖）负责，Python 只提供 ``/api``。
现在搬进 Python 自己的 FastAPI 应用里，好处是 **``python -m tonicuisc_server`` 一条命令
就把「接口 + 网页 + 会话」全跑起来**，不用再起第二个进程、也不用 Node 运行时：

* 静态文件直接读 ``webapp/public``（HTML / CSS / JS 不构建、不改）；
* 网页会话（HttpOnly Cookie ↔ 内存里的设备 key）由 :mod:`~tonicuisc_server.web.sessions` 管；
* 因为会话和 API 同进程，**不需要反代**：:mod:`~tonicuisc_server.api.middleware` 在
  鉴权闸门之前把 Cookie 换成 ``X-API-Key``，浏览器依旧拿不到 key。

**两枚 Cookie**：

* ``tuisc_session`` —— 短会话（默认 7 天），只活在进程内存里；
* ``tuisc_web_id``  —— 长期指纹（默认 1 年），相当于「网页播放器的 MAC 地址」。

后者解决两个老问题：① 服务端一重启内存会话就没了，浏览器被迫重新配对；② 每配对一次
设备列表就多一台「网页播放器」。现在指纹会作为 ``device_id`` 带进设备码流程（复用同一条
记录），并在 Redis 里留一条「指纹 → 设备」的绑定，重启后自动重新签发 key 恢复登录。

保留 Node 入口的兼容拓扑：``TONICUISC_WEB_BUILTIN=0`` 时 Caddy 仍反代到
``TONICUISC_WEB_PORT``（Node），本模块的路由留着也不影响。
"""

from __future__ import annotations

import asyncio
import mimetypes
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)

from ...auth import CODE_ALPHABET, DEVICE_ID_RE, DeviceStatus, hash_key, new_api_key, normalize_user_code
from ...core.config import SETTINGS
from ...web import pages
from ...web.sessions import SESSION_COOKIE, SESSIONS, WEB_ID_COOKIE, WEB_ID_TTL
from ..deps import client_key, get_auth, get_service
from ..limiters import device_start_limiter

router = APIRouter()

#: 设备码只有 32^8 种，撞上了（``start_device_request`` 返回 None）就换一个再试
_CODE_ATTEMPTS = 6

#: 网页播放器在日志 / 页面上的名字（会成为设备列表里的一行）
WEB_DEVICE_NAME = "网页播放器"

#: 静态文件类型。``mimetypes`` 在部分 Windows 上把 ``.js`` 认成 ``text/plain``，
#: 浏览器会拒绝执行，所以这几个常见类型写死。
MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
    ".woff2": "font/woff2",
}

#: 下载入口支持哪些平台（和桌面端 Release 的资产命名一致）
PLATFORMS = frozenset({"windows", "macos", "linux"})


# ---------------------------------------------------------------- 小工具
def _is_https(request: Request) -> bool:
    """判断对外是不是 HTTPS：优先信反代写的 ``X-Forwarded-Proto``。"""
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if forwarded in {"http", "https"}:
        return forwarded == "https"
    return request.url.scheme == "https"


def _cookie_header(request: Request, name: str, value: str, max_age: int) -> str:
    """拼 ``Set-Cookie``。HTTPS 下加 ``Secure``，其余场景加了浏览器会直接丢掉。"""
    parts = [
        f"{name}={value}",
        "Path=/",
        "HttpOnly",
        "SameSite=Lax",
        f"Max-Age={max_age}",
    ]
    if _is_https(request):
        parts.append("Secure")
    return "; ".join(parts)


def _session_of(request: Request):
    return SESSIONS.get(request.cookies.get(SESSION_COOKIE))


def _web_id_of(request: Request) -> tuple[str, bool]:
    """取浏览器指纹，返回 ``(指纹, 是不是刚生成的)``。

    指纹就是 32 位十六进制的随机 ID——「网页播放器版的 MAC 地址」。浏览器拿不到真 MAC
    （隐私限制），所以用服务端发的长期 Cookie 代替：同一台机器的同一个浏览器永远带同一个值。
    """
    raw = (request.cookies.get(WEB_ID_COOKIE) or "").strip().lower()
    if DEVICE_ID_RE.fullmatch(raw):
        return raw, False
    return secrets.token_hex(16), True


def _session_cookies(session, web_id: str, *, new_web_id: bool = False) -> list[tuple[str, str, int]]:
    cookies = [(SESSION_COOKIE, session.id, int(SESSIONS.ttl))]
    if new_web_id and web_id:
        cookies.append((WEB_ID_COOKIE, web_id, WEB_ID_TTL))
    return cookies


def _reply(
    payload: dict[str, Any],
    *,
    request: Request,
    status: int = 200,
    cookies: list[tuple[str, str, int]] = (),
) -> JSONResponse:
    """返回 JSON，并按需下发 / 清除 Cookie。"""
    response = JSONResponse(payload, status_code=status)
    for name, value, max_age in cookies:
        response.headers.append("set-cookie", _cookie_header(request, name, value, max_age))
    return response


def _ready(session) -> dict[str, Any]:
    return {"authenticated": True, "state": "ready", "me": session.me}


def _me(device: dict[str, Any]) -> dict[str, Any]:
    """设备记录 → ``/api/me`` 的形状（前端「我的」页直接用这个）。"""
    return {
        "device_id": device.get("id"),
        "device_name": device.get("name"),
        "username": device.get("username"),
        "user_id": device.get("user_id"),
        "created_at": device.get("created_at"),
        "last_seen": device.get("last_seen"),
    }


def _random_user_code() -> str:
    """``A1B1-C1D1``：字母表去掉了 0/O/1/I，和 App 侧一致。"""
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def _page(title: str, detail: str, status: int, *, href: str = "/", action: str = "返回首页") -> HTMLResponse:
    return HTMLResponse(pages.message_page(title, detail, href=href, action=action), status_code=status)


def _pending_payload(session) -> dict[str, Any]:
    pending = session.pending or {}
    left = max(0, round(float(pending.get("expires_at", 0)) - time.time()))
    return {
        "authenticated": False,
        "state": DeviceStatus.PENDING,
        "user_code": pending.get("user_code"),
        "expires_in": left,
    }


# ---------------------------------------------------------------- 记住这台浏览器
def _remember(session, web_id: str) -> None:
    """把「这个指纹 = 这台设备」写进 Redis，重启后靠它自动恢复登录。

    存的是**设备指针**，不是 key：恢复时按设备记录重新签发（见 :func:`_restore`）。
    """
    if not SETTINGS.web_remember or not web_id or not session.me:
        return
    device_id = str(session.me.get("device_id") or "")
    if not device_id:
        return
    get_service().storage.remember_web_login(
        hash_key(web_id),
        device_id,
        session.me.get("user_id"),
        SETTINGS.web_remember_ttl,
    )


def _forget(web_id: str) -> None:
    """退出登录 / 恢复失败时把绑定删掉，别留下一条「能自动复活」的记录。"""
    if not web_id:
        return
    storage = get_service().storage
    token_hash = hash_key(web_id)
    binding = storage.get_web_login(token_hash)
    storage.forget_web_login(token_hash, (binding or {}).get("device_id"))


def _restore(session, web_id: str) -> bool:
    """进程重启后内存会话没了：按指纹找回设备，**重新签发**一把 key。

    为什么重新签发而不是把 key 存起来：``devices`` 只存 ``sha256(key)``，Redis 里翻不出
    能直接用的凭证。这里存的只是一条「这台浏览器属于这台设备」的指针。

    key 是**追加**而不是替换（``reissue_device_key``）：同一个人开着两个标签页，重启后
    两个标签会各恢复一次，换成「换 key」的语义它们就会互相踢下线、无限互踢。
    """
    if not SETTINGS.web_remember or not web_id:
        return False
    storage = get_service().storage
    token_hash = hash_key(web_id)
    binding = storage.get_web_login(token_hash)
    if binding is None:
        return False

    device = storage.get_device(str(binding.get("device_id") or ""))
    if device is None or device.get("revoked") or not device.get("user_id"):
        # 设备被吊销 / 从列表里删掉了：绑定作废，这台浏览器下次要重新配对
        storage.forget_web_login(token_hash, (binding or {}).get("device_id"))
        return False

    api_key = new_api_key()
    if not storage.reissue_device_key(str(device["id"]), hash_key(api_key)):
        return False
    row = get_auth().verify(api_key)
    if row is None:
        return False
    session.api_key = api_key
    session.me = _me(row)
    session.pending = None
    # 恢复一次就续期一次，常来的浏览器不会因为 TTL 到点而被踢
    storage.remember_web_login(token_hash, str(row["id"]), row.get("user_id"), SETTINGS.web_remember_ttl)
    print(f"[web] 已按浏览器指纹恢复登录：{row.get('name')} / {row.get('username')}", flush=True)
    return True


# ---------------------------------------------------------------- 会话 API
@router.post("/music/api/session")
async def session_start(request: Request):
    """浏览器进站：已登录就直接用；重启过就按指纹自动恢复；都没有才登记设备码。"""
    session = _session_of(request)
    web_id, fresh = _web_id_of(request)
    if session is None:
        session = SESSIONS.create()
    cookies = _session_cookies(session, web_id, new_web_id=fresh)

    if session.api_key:
        return _reply(_ready(session), request=request, cookies=cookies)

    # 服务端重启后内存会话没了，但浏览器还带着指纹：先试着自动恢复，别弹设备码
    if await asyncio.to_thread(_restore, session, web_id):
        return _reply(_ready(session), request=request, cookies=cookies)

    # 和 App 走同一条限速：不然刷这个接口就能无限造设备码，把 /login 的码池刷满
    allowed, retry_after = device_start_limiter.check(client_key(request))
    if not allowed:
        return _reply(
            {"detail": f"请求太频繁，请 {retry_after} 秒后再试"},
            request=request,
            status=429,
            cookies=cookies,
        )

    auth = get_auth()
    for _ in range(_CODE_ATTEMPTS):
        user_code = _random_user_code()
        poll_secret = secrets.token_urlsafe(24)
        result = await asyncio.to_thread(
            auth.start_device_request,
            user_code,
            poll_secret,
            WEB_DEVICE_NAME,
            # 带上指纹：批准时 `create_device` 会**复用同一条设备记录**，
            # 设备列表里不会每登录一次就多一台「网页播放器」
            web_id,
        )
        if result is None:
            continue  # 码被占了，换一个
        session.pending = {
            "user_code": normalize_user_code(result["user_code"]) or user_code,
            "poll_secret": poll_secret,
            "expires_at": float(result["expires_at"]),
        }
        payload = _pending_payload(session)
        payload["login_url"] = f"/login?code={payload['user_code']}"
        return _reply(payload, request=request, cookies=cookies)

    return _reply(
        {"detail": "连续生成的设备码都被占用了，请稍后再试"},
        request=request,
        status=500,
        cookies=cookies,
    )


@router.get("/music/api/session")
async def session_status(request: Request):
    """前端轮询：``ready`` / ``pending`` / ``anonymous`` / ``expired`` / ``denied`` …"""
    session = _session_of(request)
    web_id, fresh = _web_id_of(request)
    if session is None:
        session = SESSIONS.create()
    cookies = _session_cookies(session, web_id, new_web_id=fresh)

    if session.api_key:
        device = await asyncio.to_thread(get_auth().verify, session.api_key)
        if device is not None:
            session.me = _me(device)
            return _reply(_ready(session), request=request, cookies=cookies)
        # key 失效（被吊销 / 换了）：先让指纹试一次，救不回来才算过期
        session.api_key = None
        session.me = None
        if await asyncio.to_thread(_restore, session, web_id):
            return _reply(_ready(session), request=request, cookies=cookies)
        return _reply({"authenticated": False, "state": "expired"}, request=request, cookies=cookies)

    if session.pending:
        if float(session.pending.get("expires_at", 0)) <= time.time():
            session.pending = None
            return _reply({"authenticated": False, "state": "expired"}, request=request, cookies=cookies)

        result = await asyncio.to_thread(
            get_auth().request_status,
            session.pending["user_code"],
            session.pending["poll_secret"],
        )
        state = str(result.get("status") or "")
        if state == DeviceStatus.APPROVED and result.get("api_key"):
            session.api_key = str(result["api_key"])
            session.pending = None
            device = await asyncio.to_thread(get_auth().verify, session.api_key)
            if device is not None:
                session.me = _me(device)
            _remember(session, web_id)
            return _reply(_ready(session), request=request, cookies=cookies)
        if state == DeviceStatus.PENDING:
            return _reply(_pending_payload(session), request=request, cookies=cookies)
        session.pending = None
        return _reply({"authenticated": False, "state": state or "expired"}, request=request, cookies=cookies)

    if await asyncio.to_thread(_restore, session, web_id):
        return _reply(_ready(session), request=request, cookies=cookies)
    return _reply({"authenticated": False, "state": "anonymous"}, request=request, cookies=cookies)


@router.delete("/music/api/session")
async def session_logout(request: Request):
    """退出登录 = 丢掉服务端会话 + 让浏览器把会话 Cookie 删掉。

    同时删掉「记住这台浏览器」的绑定——主动退出就该是真的退出，下次要重新配对。
    指纹 Cookie 保留，下次登录还是同一台设备，设备列表里不会多一行。
    """
    SESSIONS.drop(request.cookies.get(SESSION_COOKIE))
    web_id, _ = _web_id_of(request)
    await asyncio.to_thread(_forget, web_id)
    return _reply({"ok": True}, request=request, cookies=[(SESSION_COOKIE, "", 0)])


@router.post("/music/api/session/cancel")
async def session_cancel(request: Request):
    """「换一个设备码」：只丢掉待批请求，已登录的会话不动。"""
    session = _session_of(request)
    if session is not None:
        session.pending = None
    return _reply({"ok": True}, request=request)


@router.post("/music/api/session/manual")
async def session_manual(request: Request):
    """手动粘贴 API Key（App「我的」页能看到），不走设备码。"""
    try:
        payload = await request.json()
    except Exception:
        return _reply({"detail": "请求体不是合法 JSON"}, request=request, status=400)
    api_key = str((payload or {}).get("api_key") or "").strip() if isinstance(payload, dict) else ""
    if len(api_key) < 16:
        return _reply({"detail": "API Key 看起来不完整"}, request=request, status=400)

    device = await asyncio.to_thread(get_auth().verify, api_key)
    if device is None:
        return _reply({"detail": "这个 API Key 无效或已被吊销"}, request=request, status=401)

    session = _session_of(request) or SESSIONS.create()
    session.api_key = api_key
    session.me = _me(device)
    session.pending = None
    web_id, fresh = _web_id_of(request)
    _remember(session, web_id)
    return _reply(
        _ready(session),
        request=request,
        cookies=_session_cookies(session, web_id, new_web_id=fresh),
    )


# ---------------------------------------------------------------- 下载入口
@router.get("/download/{platform}")
async def download_latest(platform: str, request: Request, arch: str = ""):
    """查一次最新 Release，302 到安装包（和落地页按钮同源，换域名不用改）。"""
    platform = platform.strip().lower().rstrip("/")
    if platform not in PLATFORMS:
        return _page(
            "没有这个平台的安装包",
            f"不认识 {platform}，可选 windows / macos / linux。",
            404,
        )
    arch = (arch or ("arm64" if platform == "macos" else "x64"))[:16]

    from ...services.updates import get_update_service

    try:
        data = await asyncio.to_thread(get_update_service().check, platform, arch, None, None)
    except Exception as exc:  # GitHub API / 网络错误
        return _page("检查更新失败", f"{exc}，稍后再试。", 502)

    asset = (data or {}).get("asset") or {}
    target = str(asset.get("url") or asset.get("direct_url") or "")
    if not target:
        return _page("还没有可下载的安装包", str((data or {}).get("message") or "稍后再试。"), 404)

    return RedirectResponse(target, status_code=302, headers={"cache-control": "no-store"})


@router.get("/download")
async def download_root():
    return RedirectResponse("/", status_code=302)


# ---------------------------------------------------------------- 爬虫
@router.api_route(
    "/robots.txt",
    methods=["GET", "HEAD"],
    response_class=PlainTextResponse,
    include_in_schema=False,
)
async def robots_txt() -> PlainTextResponse:
    """给爬虫看的规则：接口和登录页别抓，其余随意。

    写在路由里而不是往 ``webapp/public`` 丢一个文件：``TONICUISC_WEB_PUBLIC`` 指到别处、
    或者只部署了后端时也照样有；改规则也不用动前端目录。
    内容是纯文本，别让 FastAPI 按 JSON 序列化，所以显式给 ``PlainTextResponse``。
    方法显式列了 ``HEAD``：FastAPI 不像 Starlette 那样给 GET 自动补 HEAD（爬虫和 CDN 会探）。
    """
    body = (
        "User-agent: *\n"
        "Disallow: /api/\n"
        "Disallow: /music/api/\n"
        "Disallow: /login\n"
        "Allow: /\n"
    )
    # 爬虫不会频繁回来，给 1 小时缓存，省得每次都被打一遍
    return PlainTextResponse(body, headers={"cache-control": "public, max-age=3600"})


# ---------------------------------------------------------------- 静态文件
def _public_dir() -> Path:
    return Path(SETTINGS.web_public_dir)


def _resolve(pathname: str) -> Path | None:
    """URL 路径 → 磁盘文件；越界（``../``）或不存在都返回 None。"""
    root = _public_dir()
    relative = pathname.lstrip("/")
    if relative in {"", "index.html"}:
        relative = "index.html"
    elif relative in {"music", "music/", "music/index.html"}:
        relative = "music/index.html"
    try:
        target = (root / relative).resolve()
    except (OSError, ValueError):
        return None
    if target != root and root not in target.parents:
        return None
    return target if target.is_file() else None


def _file_response(target: Path, request: Request) -> Response:
    """按扩展名给类型 + 缓存策略。HTML / CSS / JS 每次回源校验。"""
    ext = target.suffix.lower()
    media_type = MIME.get(ext) or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    # 手工拷贝部署时不会出现「新 HTML + 旧 JS」
    headers = {"cache-control": "no-cache" if ext in {".html", ".css", ".js", ".mjs"} else "public, max-age=300"}
    if request.method == "HEAD":
        headers["content-length"] = str(target.stat().st_size)
        return Response(status_code=200, headers=headers, media_type=media_type)
    return FileResponse(target, media_type=media_type, headers=headers)


def _serve(relative: str, request: Request) -> Response:
    target = _resolve(relative)
    if target is None:
        return _page("页面不存在", f"没有找到 /{relative}。", 404)
    return _file_response(target, request)


@router.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
async def landing(request: Request):
    """落地页。"""
    return _serve("index.html", request)


@router.api_route("/music", methods=["GET", "HEAD"], include_in_schema=False)
@router.api_route("/music/", methods=["GET", "HEAD"], include_in_schema=False)
@router.api_route("/music/index.html", methods=["GET", "HEAD"], include_in_schema=False)
async def player(request: Request):
    """网页播放器（三种写法都认，省得有人手打地址少个斜杠就 404）。"""
    return _serve("music/index.html", request)


async def not_found(request: Request, exc: Exception) -> Response:
    """兜底：没匹配到任何路由时，先当静态文件找，找不到再给 404。

    为什么用 404 处理器而不是 ``/{path}`` 兜底路由：兜底路由会把「路径对、方法不对」
    也吃掉，``GET /api/device/status`` 就从 405 变成 404 了。挂在 404 上，
    Starlette 的 405 判定照旧生效。
    """
    path = request.url.path
    # 接口的 404 一律回 JSON（含路由里自己抛的 404），别让前端拿到一坨 HTML
    if path == "/api" or path.startswith("/api/"):
        detail = getattr(exc, "detail", "Not Found")
        return JSONResponse({"detail": detail}, status_code=404)
    if request.method not in {"GET", "HEAD"}:
        return JSONResponse({"detail": "Not Found"}, status_code=404)

    if not _public_dir().is_dir():
        return _page(
            "网页文件缺失",
            f"找不到静态目录 {_public_dir()}。请把仓库里的 webapp/public 一起部署，"
            "或者用 TONICUISC_WEB_PUBLIC 指向它的实际位置。",
            500,
        )

    target = _resolve(path)
    if target is None:
        return _page("页面不存在", f"没有找到 {path}。", 404)
    return _file_response(target, request)


__all__ = ["router", "not_found", "MIME", "PLATFORMS", "WEB_DEVICE_NAME"]
