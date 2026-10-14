"""FastAPI 应用装配：lifespan、中间件、路由表。

对外的应用对象是 :data:`app`（``uvicorn tonicuisc_server.api:app``）。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..core.config import SETTINGS
from ..core.runtime import configure_thread_pool, shutdown_thread_pool
from ..storage import redis_runtime
from . import middleware as mw
from .deps import get_auth, get_service
from .routes import account, health, library, login, media, search


def login_banner(settings, enabled: bool = True) -> str:
    """启动横幅：告诉用户去哪登录。"""
    if not enabled:
        return (
            "\n" + "=" * 56 + "\n"
            "  警告：API Key 校验已关闭 (TONICUISC_AUTH=0)\n"
            "  任何人都能调用本服务的接口\n" + "=" * 56
        )
    base = settings.public_url or f"{settings.scheme}://{settings.host}:{settings.port}"
    return (
        "\n" + "=" * 56 + "\n"
        "  Tonicuisc 服务已启动\n"
        f"  登录页: {base.rstrip('/')}/login\n"
        "  在 App 里点「登录」拿到设备码，再用手机浏览器打开上面的链接\n" + "=" * 56
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时提示登录地址；退出时把「我们拉起来的」Redis 一起带走。"""
    auth = get_auth()
    configure_thread_pool(SETTINGS.thread_pool_size)
    print(login_banner(SETTINGS, enabled=auth.enabled), flush=True)
    if redis_runtime.RUNTIME.started_by_us:
        print(f"[redis] 已启动本地 Redis（{get_service().storage.describe()}），服务退出时会自动关闭", flush=True)
    try:
        yield
    finally:
        # 先收封面线程池（否则退出要等队列里几百个查询跑完），再关 Redis
        try:
            get_service().close_cover_pool()
        except Exception:
            pass
        shutdown_thread_pool()
        redis_runtime.shutdown_redis()


def create_app() -> FastAPI:
    app = FastAPI(title="Tonicuisc API", version="1.0.0", description="咪咕 / 酷我 音源代理", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Range", "Accept-Ranges", "Content-Length", "Content-Disposition"],
    )
    # 注册顺序决定嵌套顺序：后注册的在外层，所以 access_log 会连 401 也记一行
    app.middleware("http")(mw.auth_guard)
    app.middleware("http")(mw.access_log)
    for module in (health, account, library, media, search, login):
        app.include_router(module.router)
    return app


app = create_app()
