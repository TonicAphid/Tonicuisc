"""桌面端检查更新（iOS/Android 返回不支持）。"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query

from ...services.updates import get_update_service

router = APIRouter()


@router.get("/api/update")
async def check_update(
    platform: str = Query(..., description="windows / macos / linux"),
    arch: str = Query("", description="macOS 用来区分 arm64 / x64"),
    version: str = Query("", description="客户端当前版本，如 v0.2.4"),
    build: str = Query("", description="客户端 build number"),
) -> dict:
    try:
        return await asyncio.to_thread(
            get_update_service().check,
            platform,
            arch or None,
            version or None,
            build or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # GitHub API / 网络错误
        raise HTTPException(status_code=502, detail=f"检查更新失败: {exc}")
