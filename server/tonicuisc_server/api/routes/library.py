"""喜欢 / 收藏 / 播放历史（按账户隔离）。"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..deps import current_user, get_service

router = APIRouter()

LIBRARY_KINDS = {"like", "favorite", "history"}


class LibraryAddRequest(BaseModel):
    song_id: str = Field(..., min_length=3, max_length=200)


def _kind_or_400(kind: str) -> str:
    if kind not in LIBRARY_KINDS:
        raise HTTPException(status_code=400, detail=f"未知列表类型：{kind}")
    return kind


@router.get("/api/library/summary")
async def library_summary(request: Request) -> dict:
    """「列表」页用：三个列表的数量 + 喜欢/收藏的 id（搜索页显示红心）。

    一次 pipeline 取齐，别拆成三个来回。
    """
    user_id = current_user(request)
    store = get_service().storage
    counts, likes, favorites = await asyncio.to_thread(store.library_bundle, user_id)
    return {"counts": counts, "like_ids": likes, "favorite_ids": favorites}


@router.get("/api/library/{kind}")
async def library_list(kind: str, request: Request, limit: int = Query(200, ge=1, le=1000)) -> dict:
    user_id = current_user(request)
    items = await asyncio.to_thread(get_service().storage.library_items, user_id, _kind_or_400(kind), limit)
    for item in items:
        item["has_lyric"] = bool(item.get("has_lyric"))
    return {"kind": kind, "total": len(items), "items": items}


@router.post("/api/library/{kind}")
async def library_add(kind: str, payload: LibraryAddRequest, request: Request) -> dict:
    user_id = current_user(request)
    store = get_service().storage
    _kind_or_400(kind)
    if not await asyncio.to_thread(store.known_song, payload.song_id):
        # 歌曲记录不在库里就没法显示（列表按 songs 取元信息），
        # 收下只会在列表里留一个空洞，还让 counts 和条数对不上
        raise HTTPException(status_code=404, detail="歌曲不存在或已过期，请重新搜索")
    await asyncio.to_thread(store.add_to_library, user_id, kind, payload.song_id)
    counts = await asyncio.to_thread(store.library_counts, user_id)
    return {"kind": kind, "song_id": payload.song_id, "counts": counts}


@router.delete("/api/library/{kind}/{item_id:path}")
async def library_remove(kind: str, item_id: str, request: Request) -> dict:
    user_id = current_user(request)
    removed = await asyncio.to_thread(get_service().storage.remove_from_library, user_id, _kind_or_400(kind), item_id)
    if not removed:
        raise HTTPException(status_code=404, detail="这条记录不存在")
    return {"kind": kind, "song_id": item_id, "removed": True}


@router.delete("/api/library/{kind}")
async def library_clear(kind: str, request: Request) -> dict:
    """清空某个列表（历史播放用得上）。"""
    user_id = current_user(request)
    removed = await asyncio.to_thread(get_service().storage.clear_library, user_id, _kind_or_400(kind))
    return {"kind": kind, "removed": removed}
