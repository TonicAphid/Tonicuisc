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
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import weblogin
from .auth import API_KEY_HEADER, PUBLIC_PATHS, AuthManager
from .config import SETTINGS, SOURCE_ALIASES, resolve_sources, source_label
from .ratelimit import FailedLoginGuard, SlidingWindowLimiter
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时提示登录地址。"""
    auth = get_auth()
    print(login_banner(SETTINGS, enabled=auth.enabled), flush=True)
    yield


app = FastAPI(title="Tonicuisc API", version="1.0.0", description="咪咕 / 酷我 音源代理", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length", "Content-Disposition"],
)


def login_banner(settings, enabled: bool = True) -> str:
    """启动横幅：告诉用户去哪登录。"""
    if not enabled:
        return (
            "\n" + "=" * 56 + "\n"
            "  警告：API Key 校验已关闭 (TONICUISC_AUTH=0)\n"
            "  任何人都能调用本服务的接口\n" + "=" * 56
        )
    base = settings.public_url or f"http://{settings.host}:{settings.port}"
    return (
        "\n" + "=" * 56 + "\n"
        "  Tonicuisc 服务已启动\n"
        f"  登录页: {base.rstrip('/')}/login\n"
        "  在 App 里点「登录」拿到设备码，再用手机浏览器打开上面的链接\n" + "=" * 56
    )


@app.middleware("http")
async def auth_guard(request: Request, call_next):
    """除 PUBLIC_PATHS 外，所有 /api 请求都要带设备 key。"""
    auth = get_auth()
    path = request.url.path
    if auth.enabled and path.startswith("/api") and path not in PUBLIC_PATHS:
        device = await asyncio.to_thread(auth.verify, request.headers.get(API_KEY_HEADER))
        if device is None:
            return JSONResponse({"detail": "未授权：请先在 App 里配对设备"}, status_code=401)
        request.state.device = device
    return await call_next(request)

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
_auth: AuthManager | None = None

#: 限速器：防密码爆破 / 防刷接口
login_limiter = SlidingWindowLimiter(limit=30, window=60)  # 每个来源每分钟最多提交 30 次 /login
device_start_limiter = SlidingWindowLimiter(limit=30, window=60)  # 每 IP 每分钟最多发起 30 次设备码
failed_login_guard = FailedLoginGuard(limit=5, window=300)  # 同一 IP + 用户名 5 分钟内错 5 次就锁


def reset_limiters() -> None:
    """测试用：清空所有限速计数。"""
    login_limiter.reset()
    device_start_limiter.reset()
    failed_login_guard.limiter.reset()


def client_key(request: Request) -> str:
    """限速用的来源标识。

    默认信任反向代理（Caddy）写的 ``X-Forwarded-For``；
    如果把端口直接暴露到公网，记得设 ``TONICUISC_TRUST_PROXY=0``，
    否则攻击者可以伪造这个头绕过限速。
    """
    if SETTINGS.trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]


def get_service() -> MusicService:
    global _service
    if _service is None:
        _service = MusicService()
    return _service


def get_auth() -> AuthManager:
    global _auth
    if _auth is None:
        _auth = AuthManager(get_service().storage)
    return _auth


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
    auth = get_auth()
    return {
        "status": "ok",
        "sources": resolve_sources(SETTINGS.sources),
        "work_dir": str(SETTINGS.work_dir),
        "database": str(SETTINGS.db_path),
        "auth": "enabled" if auth.enabled else "disabled",
    }


class DeviceStartRequest(BaseModel):
    user_code: str = Field(..., min_length=8, max_length=12, description="App 生成的设备码，如 A1B1-C1D1")
    poll_secret: str = Field(..., min_length=8, description="只有 App 知道的轮询密钥，别显示给用户")
    name: str = Field("", max_length=60, description="设备名")


class DeviceStatus(BaseModel):
    status: str


@app.post("/api/device/start")
async def device_start(payload: DeviceStartRequest, request: Request) -> dict:
    """App 发起登录：登记设备码，等待用户在 /login 批准。"""
    allowed, retry_after = device_start_limiter.check(client_key(request))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"请求太频繁，请 {retry_after} 秒后再试",
            headers={"Retry-After": str(retry_after)},
        )
    result = await asyncio.to_thread(
        get_auth().start_device_request, payload.user_code, payload.poll_secret, payload.name
    )
    if result is None:
        raise HTTPException(status_code=409, detail="设备码格式不对或已被占用，请重新生成")
    return {
        "user_code": result["user_code"],
        "expires_in": max(int(result["expires_at"] - time.time()), 0),
        "login_path": "/login",
    }


@app.get("/api/device/status")
async def device_status(
    user_code: str = Query(..., min_length=8, max_length=12),
    poll_secret: str = Query(..., min_length=8),
) -> dict:
    """App 轮询：pending / approved（带 api_key，只给一次） / denied / expired / claimed。"""
    return await asyncio.to_thread(get_auth().request_status, user_code, poll_secret)


@app.get("/api/me")
async def me(request: Request) -> dict:
    """「我的」页面用：当前账户 + 本设备信息。"""
    device = getattr(request.state, "device", {}) or {}
    return {
        "device_id": device.get("id"),
        "device_name": device.get("name"),
        "username": device.get("username"),
        "user_id": device.get("user_id"),
        "created_at": device.get("created_at"),
        "last_seen": device.get("last_seen"),
    }


@app.get("/login", response_class=HTMLResponse)
async def login_page() -> HTMLResponse:
    """设备码登录页（浏览器打开）。"""
    return HTMLResponse(weblogin.code_step(), headers={"Cache-Control": "no-store"})


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request) -> HTMLResponse:
    source = client_key(request)
    allowed, retry_after = login_limiter.check(source)
    if not allowed:
        return HTMLResponse(weblogin.rate_limited_step(retry_after), status_code=429, headers={"Cache-Control": "no-store"})
    raw = (await request.body()).decode("utf-8", "ignore")
    form = {key: values[0] for key, values in parse_qs(raw).items() if values}
    page = await asyncio.to_thread(weblogin.render, form, get_auth(), source, failed_login_guard)
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


@app.get("/api/devices")
async def devices(request: Request) -> dict:
    items = await asyncio.to_thread(get_service().storage.list_devices)
    current = getattr(request.state, "device", {}) or {}
    for item in items:
        # SQLite 里是 0/1，客户端按布尔判断，统一成 bool
        item["revoked"] = bool(item.get("revoked"))
        item["current"] = item["id"] == current.get("id")
        item["blocked"] = bool(item.get("revoked")) or not item.get("user_id")
    return {"total": len(items), "items": items}


@app.delete("/api/devices/{device_id}")
async def revoke_device(device_id: str) -> dict:
    """吊销某台设备（不需要知道它的 key）。"""
    ok = await asyncio.to_thread(get_service().storage.revoke_device, device_id)
    if not ok:
        raise HTTPException(status_code=404, detail="设备不存在")
    return {"device_id": device_id, "revoked": True}


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
