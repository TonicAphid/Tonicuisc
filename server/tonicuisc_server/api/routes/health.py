"""健康检查（``/api/health`` 与 ``/health`` 兼容别名）。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ...core.config import SETTINGS, resolve_sources
from ...core.logging import timing_log
from ...storage import redis_runtime
from ..deps import get_auth, get_service

router = APIRouter()


@router.get("/api/health")
async def health() -> dict:
    auth = get_auth()
    service = get_service()
    return {
        "status": "ok",
        "sources": resolve_sources(SETTINGS.sources),
        "database": service.storage.describe(),
        "redis_managed": bool(getattr(redis_runtime.RUNTIME, "started_by_us", False)),
        "auth": "enabled" if auth.enabled else "disabled",
    }


#: 本机探活按约定打的是 `/health`，刷过一屏 404。路由本身没副作用，顺手记下是谁在打：
#: 每个 User-Agent 只记一行，不会因为每秒一次又把日志刷爆。
_PROBE_USER_AGENTS: set[str] = set()


@router.get("/health")
async def health_alias(request: Request) -> dict:
    """`/api/health` 的兼容别名（免鉴权，见 ``auth.PUBLIC_PATHS``）。"""
    ua = (request.headers.get("user-agent") or "?").strip() or "?"
    if ua not in _PROBE_USER_AGENTS:
        _PROBE_USER_AGENTS.add(ua)
        who = request.client.host if request.client else "?"
        timing_log(f"探活: {who} -> GET /health, user-agent={ua!r}（已用 /api/health 兜住）")
    return await health()
