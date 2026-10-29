"""音频相关：直链、歌词、播放流（支持 Range）、附件下载。"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
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


@router.get("/api/lyric/{item_id:path}")
async def lyric(item_id: str, fmt: str = Query("text", alias="format")):
    """歌词。

    * ``format=text``（默认）：音源自带的逐行 LRC（纯文本，老客户端不变）；
    * ``format=enhanced``：结构化逐字时间轴，没查完时返回 ``ready: false``，
      客户端过两秒再问（和封面同一套「有结论才返回」的语义）。
    """
    if fmt in {"enhanced", "json"}:
        try:
            song = await asyncio.to_thread(get_service().get_song, item_id)
        except SongNotFound:
            raise HTTPException(status_code=404, detail="歌曲不存在或已过期，请重新搜索")
        return await asyncio.to_thread(get_service().lyrics.enhanced, item_id, song)
    try:
        text = await asyncio.to_thread(get_service().lyric, item_id)
        return PlainTextResponse(text, media_type="text/plain; charset=utf-8")
    except SongNotFound:
        raise HTTPException(status_code=404, detail="歌曲不存在或已过期，请重新搜索")


def _range_start(header: str | None) -> int | None:
    """取 ``Range: bytes=N-`` 的起点；``N == 0`` 当没带。

    播放器几乎每个请求都会带 ``Range``，第一次放歌带的是 ``bytes=0-``（就是「整段」）。
    只有从中间开始（用户拖了进度条 / 点了某句歌词）才是真的「跳转」。
    """
    if not header or not header.startswith("bytes="):
        return None
    spec = header[len("bytes=") :].split(",")[0].strip()
    start = spec.partition("-")[0].strip()
    if not start.isdigit():
        return None
    value = int(start)
    return value if value > 0 else None


@router.get("/api/stream/{item_id:path}")
async def stream(item_id: str, request: Request) -> StreamingResponse:
    """播放流。

    已缓存：按 Range 读本地文件（206，支持拖动进度）。
    未缓存：边从音源拉边播，同时写入缓存，所以第一次点歌不用等整首下完。
    **未缓存时的跳转**：把客户端的 ``Range`` 原样透给音源，直接从中途开始拉（206）。
    以前这里不管 ``Range`` 一律返回 200 + ``Accept-Ranges: none``，播放器没法跳，
    只能把进度条弹回 0——表现就是「拖一下就回到开头」。
    """
    service = get_service()
    try:
        path = await asyncio.to_thread(service.cached_file, item_id)
        upstream = None
        if path is None:
            range_header = request.headers.get("range")
            start = _range_start(range_header)
            upstream = await asyncio.to_thread(
                service.open_upstream,
                item_id,
                range_header=range_header if start else None,
            )
            response, target, temp_path = upstream
            # 音源认了 Range（206）就直接透传，不写缓存：这段是从中间开始的，
            # 拼进缓存文件会把字节错位。
            if start and getattr(response, "status_code", 200) == 206:
                content_range = str(response.headers.get("content-range") or "")
                if content_range:
                    headers = {"Accept-Ranges": "bytes", "Content-Range": content_range}
                    length = response.headers.get("content-length")
                    if length:
                        headers["Content-Length"] = str(length)
                    return StreamingResponse(
                        service.forward(response),
                        status_code=206,
                        media_type=_mime_of(target),
                        headers=headers,
                    )
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
