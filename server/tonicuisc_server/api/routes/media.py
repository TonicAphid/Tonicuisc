"""音频相关：直链、歌词、播放流（支持 Range）、附件下载。"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse

from ...services import DownloadFailed, SongNotFound, song_to_item
from ..deps import get_service
from ..ranges import file_chunks, parse_range

router = APIRouter()

MIME_TYPES = {
    "mp3": "audio/mpeg",
    "flac": "audio/flac",
    "m4a": "audio/mp4",
    "aac": "audio/aac",
    "wav": "audio/wav",
    "ogg": "audio/ogg",
    "ape": "audio/x-ape",
    "wma": "audio/x-ms-wma",
}


def _mime_of(path: Path) -> str:
    return MIME_TYPES.get(path.suffix.lstrip(".").lower(), "application/octet-stream")


async def _prepare(item_id: str) -> Path:
    try:
        return await asyncio.to_thread(get_service().ensure_file, item_id)
    except SongNotFound:
        raise HTTPException(status_code=404, detail="歌曲不存在或已过期，请重新搜索")
    except DownloadFailed as exc:
        raise HTTPException(status_code=502, detail=f"音源下载失败: {exc}")


@router.get("/api/url/{item_id:path}")
async def direct_url(item_id: str) -> dict:
    """音源直链：客户端可以直接连 CDN 播放，最快出声。"""
    try:
        return await asyncio.to_thread(get_service().direct_url, item_id)
    except SongNotFound:
        raise HTTPException(status_code=404, detail="歌曲不存在或已过期，请重新搜索")
    except DownloadFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/api/lyric/{item_id:path}", response_class=PlainTextResponse)
async def lyric(item_id: str) -> PlainTextResponse:
    try:
        text = await asyncio.to_thread(get_service().lyric, item_id)
        return PlainTextResponse(text, media_type="text/plain; charset=utf-8")
    except SongNotFound:
        raise HTTPException(status_code=404, detail="歌曲不存在或已过期，请重新搜索")


@router.get("/api/stream/{item_id:path}")
async def stream(item_id: str, request: Request) -> StreamingResponse:
    """播放流。

    已缓存：按 Range 读本地文件（206，支持拖动进度）。
    未缓存：边从音源拉边播，同时写入缓存，所以第一次点歌不用等整首下完。
    """
    service = get_service()
    try:
        path = await asyncio.to_thread(service.cached_file, item_id)
        upstream = None
        if path is None:
            upstream = await asyncio.to_thread(service.open_upstream, item_id)
    except SongNotFound:
        raise HTTPException(status_code=404, detail="歌曲不存在或已过期，请重新搜索")
    except DownloadFailed as exc:
        raise HTTPException(status_code=502, detail=f"音源下载失败: {exc}")

    if upstream is not None:
        response, target, temp_path = upstream
        return StreamingResponse(
            service.relay(response, target, temp_path),
            media_type=_mime_of(target),
            headers={"Accept-Ranges": "none"},
        )

    size = 0
    try:
        size = path.stat().st_size
    except OSError:
        # 文件刚被缓存清理掉：当作过期，让客户端重新搜索，而不是 500
        raise HTTPException(status_code=404, detail="歌曲不存在或已过期，请重新搜索")
    if size <= 0:
        raise HTTPException(status_code=404, detail="缓存文件为空，请重新搜索")
    media_type = _mime_of(path)
    rng = parse_range(request.headers.get("range"), size)
    if rng is None:
        return StreamingResponse(
            file_chunks(path, 0, size - 1),
            media_type=media_type,
            headers={"Accept-Ranges": "bytes", "Content-Length": str(size)},
        )
    start, end = rng
    return StreamingResponse(
        file_chunks(path, start, end),
        status_code=206,
        media_type=media_type,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(end - start + 1),
            "Content-Range": f"bytes {start}-{end}/{size}",
        },
    )


@router.get("/api/download/{item_id:path}")
async def download(item_id: str) -> FileResponse:
    path = await _prepare(item_id)
    try:
        song = get_service().get_song(item_id)
        item = song_to_item(song)
        filename = f"{item['name']} - {item['singers']}.{item['ext']}"
    except SongNotFound:
        filename = path.name
    except Exception:  # 从 Redis 重建的对象缺字段时，别让整个下载 500
        filename = path.name
    # ascii 那半边只能留安全字符：\r \n " ; 都是 ASCII，留着会把响应头截断/注入
    stem = Path(filename).stem.encode("ascii", "ignore").decode()
    stem = re.sub(r'[\r\n";\\]', " ", stem).strip(" .-_")
    ascii_name = f"{stem}{path.suffix}" if any(ch.isalnum() for ch in stem) else f"tonicuisc{path.suffix}"
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
    return FileResponse(path, media_type=_mime_of(path), headers={"Content-Disposition": disposition})
