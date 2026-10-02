"""Tonicuisc backend API.

Endpoints
---------
GET /api/health                    服务状态
GET /api/sources                   可用音源
GET /api/search?keyword=&sources=&limit=
GET /api/url/{item_id}             直链（best effort）
GET /api/lyric/{item_id}           歌词
GET /api/stream/{item_id}          音频流（支持 Range，播放器用）
GET /api/download/{item_id}        附件下载
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse

from .config import SETTINGS, SOURCE_ALIASES, resolve_sources, source_label
from .service import DownloadFailed, MusicService, SongNotFound, song_to_item

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

app = FastAPI(title="Tonicuisc API", version="1.0.0", description="咪咕 / 酷我 音源代理")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length", "Content-Disposition"],
)

@app.middleware("http")
async def access_log(request: Request, call_next):
    """给每个 /api 请求打一行耗时日志：``[access] GET /api/search 200 5.482s``。"""
    started = time.perf_counter()
    response = await call_next(request)
    if request.url.path.startswith("/api"):
        elapsed = time.perf_counter() - started
        print(f"[access] {request.method} {request.url.path} {response.status_code} {elapsed:.3f}s", flush=True)
    return response


_service: MusicService | None = None


def get_service() -> MusicService:
    global _service
    if _service is None:
        _service = MusicService()
    return _service


def _parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Return an inclusive (start, end) byte range, or None for "whole file"."""
    if not header or not header.startswith("bytes=") or size <= 0:
        return None
    spec = header[len("bytes=") :].split(",")[0].strip()
    start_s, _, end_s = spec.partition("-")
    if not start_s:  # suffix range: bytes=-500
        length = int(end_s or 0)
        if length <= 0:
            return None
        return max(size - length, 0), size - 1
    start = int(start_s)
    end = int(end_s) if end_s else size - 1
    if start >= size:
        raise HTTPException(status_code=416, detail="Range not satisfiable", headers={"Content-Range": f"bytes */{size}"})
    return start, min(end, size - 1)


def _file_chunks(path: Path, start: int, end: int, chunk_size: int = 512 * 1024) -> Iterator[bytes]:
    remaining = end - start + 1
    with path.open("rb") as fp:
        fp.seek(start)
        while remaining > 0:
            data = fp.read(min(chunk_size, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data


async def _prepare(item_id: str) -> Path:
    try:
        return await asyncio.to_thread(get_service().ensure_file, item_id)
    except SongNotFound:
        raise HTTPException(status_code=404, detail="歌曲不存在或已过期，请重新搜索")
    except DownloadFailed as exc:
        raise HTTPException(status_code=502, detail=f"音源下载失败: {exc}")


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "sources": resolve_sources(SETTINGS.sources),
        "work_dir": str(SETTINGS.work_dir),
        "database": str(SETTINGS.db_path),
    }


@app.get("/api/history")
async def history(limit: int = Query(20, ge=1, le=200)) -> dict:
    """搜索历史（按关键词聚合，最近的在前）。"""
    items = await asyncio.to_thread(get_service().history, limit)
    return {"total": len(items), "items": items}


@app.get("/api/sources")
async def sources() -> dict:
    configured = set(resolve_sources(SETTINGS.sources))
    return {
        "items": [
            {"alias": alias, "name": class_name, "label": source_label(class_name), "enabled": class_name in configured}
            for alias, class_name in SOURCE_ALIASES.items()
        ]
    }


@app.get("/api/search")
async def search(
    keyword: str = Query(..., min_length=1, description="搜索关键词"),
    sources: str | None = Query(None, description="逗号分隔，如 migu,kuwo"),
    limit: int = Query(50, ge=1, le=200),
    refresh: bool = Query(False, description="true = 跳过服务端搜索缓存，强制重新联网搜索"),
) -> dict:
    source_list = [s for s in (sources or "").replace(" ", ",").split(",") if s] if sources else None
    started = time.perf_counter()
    try:
        items = await asyncio.to_thread(get_service().search, keyword, source_list, limit, refresh)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # musicdl network failures
        raise HTTPException(status_code=502, detail=f"搜索失败: {exc}")
    return {
        "keyword": keyword,
        "total": len(items),
        # 搜索耗时（秒），客户端用它显示「用时 x.x 秒」
        "elapsed": round(time.perf_counter() - started, 2),
        "items": items,
    }


@app.get("/api/url/{item_id:path}")
async def direct_url(item_id: str) -> dict:
    """音源直链：客户端可以直接连 CDN 播放，最快出声。"""
    try:
        return await asyncio.to_thread(get_service().direct_url, item_id)
    except SongNotFound:
        raise HTTPException(status_code=404, detail="歌曲不存在或已过期，请重新搜索")
    except DownloadFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/lyric/{item_id:path}", response_class=PlainTextResponse)
async def lyric(item_id: str) -> PlainTextResponse:
    try:
        return PlainTextResponse(await asyncio.to_thread(get_service().lyric, item_id), media_type="text/plain; charset=utf-8")
    except SongNotFound:
        raise HTTPException(status_code=404, detail="歌曲不存在或已过期，请重新搜索")


@app.get("/api/stream/{item_id:path}")
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
            media_type=MIME_TYPES.get(target.suffix.lstrip(".").lower(), "application/octet-stream"),
            headers={"Accept-Ranges": "none"},
        )

    size = path.stat().st_size
    media_type = MIME_TYPES.get(path.suffix.lstrip(".").lower(), "application/octet-stream")
    rng = _parse_range(request.headers.get("range"), size)
    if rng is None:
        return StreamingResponse(
            _file_chunks(path, 0, size - 1),
            media_type=media_type,
            headers={"Accept-Ranges": "bytes", "Content-Length": str(size)},
        )
    start, end = rng
    return StreamingResponse(
        _file_chunks(path, start, end),
        status_code=206,
        media_type=media_type,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(end - start + 1),
            "Content-Range": f"bytes {start}-{end}/{size}",
        },
    )


@app.get("/api/download/{item_id:path}")
async def download(item_id: str) -> FileResponse:
    path = await _prepare(item_id)
    try:
        song = get_service().get_song(item_id)
        item = song_to_item(song)
        filename = f"{item['name']} - {item['singers']}.{item['ext']}"
    except SongNotFound:
        filename = path.name
    stem = Path(filename).stem.encode("ascii", "ignore").decode().strip(" .-_")
    ascii_name = f"{stem}{path.suffix}" if any(ch.isalnum() for ch in stem) else f"tonicuisc{path.suffix}"
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
    return FileResponse(
        path,
        media_type=MIME_TYPES.get(path.suffix.lstrip(".").lower(), "application/octet-stream"),
        headers={"Content-Disposition": disposition},
    )
