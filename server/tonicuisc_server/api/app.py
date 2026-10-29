"""FastAPI 应用装配：lifespan、中间件、路由表。

对外的应用对象是 :data:`app`（``uvicorn tonicuisc_server.api:app``）。
"""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..core.config import SETTINGS, display_host
from ..core.runtime import configure_thread_pool, shutdown_thread_pool
from ..storage import redis_runtime
from ..web.sessions import SESSIONS
from . import middleware as mw
from .deps import get_auth, get_service
from .routes import account, health, library, login, media, search, site, update

#: 网页会话多久清一次过期项（秒）。会话本身按 TTL 判过期，这里只是别让字典一直涨。
SESSION_SWEEP_SECONDS = 10 * 60


def login_banner(settings, enabled: bool = True) -> str:
    """启动横幅：告诉用户去哪登录。"""
    if not enabled:
        return (
            "\n" + "=" * 56 + "\n"
            "  警告：API Key 校验已关闭 (TONICUISC_AUTH=0)\n"
            "  任何人都能调用本服务的接口\n" + "=" * 56
        )
    # 没配 PUBLIC_URL 时就地拼一个，但**别把 0.0.0.0 当主机名**——那是「所有网卡」，
    # 打出来的链接点不开（家里部署时应该配 PUBLIC_URL，指向域名）
    base = (settings.public_url or f"{settings.scheme}://{display_host(settings.host)}:{settings.port}").rstrip("/")
    return (
        "\n" + "=" * 56 + "\n"
        "  Tonicuisc 服务已启动\n"
        f"  落地页:     {base}/\n"
        f"  网页播放器: {base}/music\n"
        f"  登录页:     {base}/login\n"
        "  在 App 里点「登录」拿到设备码，再用手机浏览器打开上面的链接\n" + "=" * 56
    )


async def _sweep_sessions() -> None:
    """后台定时清过期会话；进程退出时由 lifespan 取消。"""
    while True:
        await asyncio.sleep(SESSION_SWEEP_SECONDS)
        SESSIONS.sweep()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时提示登录地址；退出时把「我们拉起来的」Redis 一起带走。"""
    auth = get_auth()
    configure_thread_pool(SETTINGS.thread_pool_size)
    print(login_banner(SETTINGS, enabled=auth.enabled), flush=True)
    if redis_runtime.RUNTIME.started_by_us:
        print(f"[redis] 已启动本地 Redis（{get_service().storage.describe()}），服务退出时会自动关闭", flush=True)
    sweeper = asyncio.create_task(_sweep_sessions())
    try:
        yield
    finally:
        sweeper.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sweeper
        # 先收封面线程池（否则退出要等队列里几百个查询跑完），再关 Redis
        try:
            get_service().close_cover_pool()
        except Exception:
            pass
        try:
            get_service().close_lyrics()
        except Exception:
            pass
        shutdown_thread_pool()
        redis_runtime.shutdown_redis()


def create_app() -> FastAPI:
    app = FastAPI(title="Tonicuisc API", version="1.0.0", description="酷我 音源代理", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Range", "Accept-Ranges", "Content-Length", "Content-Disposition"],
    )
    # 注册顺序决定嵌套顺序：后注册的在外层。
    # web_session 必须在 auth_guard 外层——它要把 Cookie 换成 X-API-Key，闸门才认；
    # access_log 在最外层，连 401 也记一行。
    app.middleware("http")(mw.auth_guard)
    app.middleware("http")(mw.web_session)
    app.middleware("http")(mw.access_log)
    for module in (health, account, library, media, search, update, login):
        app.include_router(module.router)
    # 内置网页：/、/music、/music/api/session、/download/*
    # 静态文件走 404 处理器（不是兜底路由），这样「路径对、方法不对」还能正常回 405
    if SETTINGS.web_builtin:
        app.include_router(site.router)
        app.add_exception_handler(404, site.not_found)
    return app


app = create_app()
