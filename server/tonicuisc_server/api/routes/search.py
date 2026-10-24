"""搜索、歌手主页、搜索历史、音源列表、封面补齐。"""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ...core.config import SETTINGS, SOURCE_ALIASES, resolve_sources, source_label
from ..deps import get_service

router = APIRouter()


def _source_list(sources: str | None) -> list[str] | None:
    if not sources:
        return None
    return [s for s in sources.replace(" ", ",").split(",") if s]


@router.get("/api/artist")
async def artist(
    name: str = Query(..., min_length=1, description="歌手名"),
    sources: str | None = Query(None, description="逗号分隔，如 migu,kuwo"),
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    """歌手主页：这个歌手在音源上能搜到的歌。"""
    try:
        return await asyncio.to_thread(get_service().artist, name, _source_list(sources), limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # musicdl network failures
        # 打一行真实原因：不然 AttributeError 这类 bug 也会被包装成「搜索失败 502」，很难查
        print(f"[error] /api/artist 失败 {type(exc).__name__}: {exc}", flush=True)
        raise HTTPException(status_code=502, detail=f"搜索失败: {exc}")


@router.get("/api/history")
async def history(limit: int = Query(20, ge=1, le=200)) -> dict:
    """搜索历史（按关键词聚合，最近的在前）。"""
    items = await asyncio.to_thread(get_service().history, limit)
    return {"total": len(items), "items": items}


@router.get("/api/sources")
async def sources() -> dict:
    configured = set(resolve_sources(SETTINGS.sources))
    return {
        "items": [
            {"alias": alias, "name": class_name, "label": source_label(class_name), "enabled": True}
            for alias, class_name in SOURCE_ALIASES.items()
            if class_name in configured
        ]
    }


@router.get("/api/search")
async def search(
    keyword: str = Query(..., min_length=1, description="搜索关键词"),
    sources: str | None = Query(None, description="逗号分隔，如 migu,kuwo"),
    offset: int = Query(0, ge=0, description="从第几条开始（滑到底要下一页时传）"),
    limit: int | None = Query(None, ge=1, le=100, description="这一页要多少条"),
    refresh: bool = Query(False, description="true = 跳过服务端搜索缓存，强制重新联网搜索"),
) -> dict:
    started = time.perf_counter()
    try:
        # refresh 直接透给 search_page，只搜一次（以前这里先预搜一遍，等于搜两趟）
        page = await asyncio.to_thread(get_service().search_page, keyword, _source_list(sources), offset, limit, refresh)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # musicdl network failures
        print(f"[error] /api/search 失败 {type(exc).__name__}: {exc}", flush=True)
        raise HTTPException(status_code=502, detail=f"搜索失败: {exc}")
    total = time.perf_counter() - started
    print(f"[timing] /api/search 全部结束 {total:.2f}s", flush=True)
    return {
        "keyword": keyword,
        "total": page["total"],
        "offset": page["offset"],
        "has_more": page["has_more"],
        # 搜索耗时（秒），客户端用它显示「用时 x.x 秒」
        "elapsed": round(total, 2),
        "items": page["items"],
    }


class CoversRequest(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=200, description="歌曲 id 列表")


@router.post("/api/covers")
async def covers(payload: CoversRequest) -> dict:
    """按需补封面：只返回查到 QQ 封面的那些 id，查不到的不返回（客户端保持原图）。"""
    found = await asyncio.to_thread(get_service().covers, payload.ids)
    return {"covers": found}
