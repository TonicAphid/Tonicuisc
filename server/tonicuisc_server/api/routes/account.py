"""账户与设备：设备码登录的两个端点 + 「我的」页用的账户 / 设备信息。"""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..deps import client_key, get_auth, get_service, request_user_id
from ..limiters import device_start_limiter

router = APIRouter()


class DeviceStartRequest(BaseModel):
    user_code: str = Field(..., min_length=8, max_length=12, description="App 生成的设备码，如 A1B1-C1D1")
    poll_secret: str = Field(..., min_length=8, description="只有 App 知道的轮询密钥，别显示给用户")
    name: str = Field("", max_length=60, description="设备名")
    device_id: str = Field(
        "",
        max_length=64,
        description="本机已有的设备 ID：重新配对时带上，服务端就地复用旧记录，不再新开一条",
    )


class DeviceStatusRequest(BaseModel):
    """App 轮询登录状态用的请求体（``poll_secret`` 走 body，不进访问日志）。"""

    user_code: str = Field(..., min_length=8, max_length=12)
    poll_secret: str = Field(..., min_length=8)


@router.post("/api/device/start")
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
        get_auth().start_device_request,
        payload.user_code,
        payload.poll_secret,
        payload.name,
        payload.device_id,
    )
    if result is None:
        raise HTTPException(status_code=409, detail="设备码格式不对或已被占用，请重新生成")
    return {
        "user_code": result["user_code"],
        "expires_in": max(int(result["expires_at"] - time.time()), 0),
        "login_path": "/login",
    }


@router.post("/api/device/status")
async def device_status(payload: DeviceStatusRequest) -> dict:
    """App 轮询：pending / approved（带 api_key，只给一次） / denied / expired / claimed。

    ``poll_secret`` 走请求体而不是 query：query 会原样进 uvicorn / Caddy 的访问日志，
    而这个秘密的全部意义就是「只有 App 知道」。
    """
    return await asyncio.to_thread(get_auth().request_status, payload.user_code, payload.poll_secret)


@router.get("/api/me")
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


@router.get("/api/devices")
async def devices(request: Request) -> dict:
    """本账户的设备；没关联账户的孤儿设备也留着（旧版配对流程的残留要能清理）。"""
    user_id = request_user_id(request)
    items = await asyncio.to_thread(get_service().storage.list_devices, user_id)
    current = getattr(request.state, "device", {}) or {}
    for item in items:
        item["revoked"] = bool(item.get("revoked"))
        item["current"] = item["id"] == current.get("id")
        item["blocked"] = bool(item.get("revoked")) or not item.get("user_id")
    return {"total": len(items), "items": items}


@router.delete("/api/devices/{device_id}")
async def revoke_device(device_id: str, request: Request) -> dict:
    """吊销设备 = 直接从库里删掉，它的 key 立刻失效，列表里也不再出现。

    只能删本账户的设备（外加没有账户的孤儿设备），否则任何一台配对成功的设备
    都能把别人的设备踢下线。
    """
    user_id = request_user_id(request)
    ok = await asyncio.to_thread(get_service().storage.delete_device, device_id, user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="设备不存在")
    print(f"[device] 已吊销设备 {device_id[:8]}…", flush=True)
    return {"device_id": device_id, "deleted": True}
