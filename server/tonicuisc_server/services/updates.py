"""桌面端更新检查：GitHub Release → 安装包 + gh-proxy 下载地址。

服务端只做「查最新 Release + 选对平台的包 + 拼加速地址」，不代理文件本身。
iOS 明确返回 ``supported: false``（移动端走 App Store / 自签，不做这套）。

GitHub API 匿名只有 60 次/小时，所以结果在进程内缓存 10 分钟；可以用
``TONICUISC_GITHUB_TOKEN`` 提高额度。
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any

import requests

from ..core.config import SETTINGS
from ..core.download import download_candidates
from ..core.logging import timing_log

_HTTP_TIMEOUT = 10.0
_CACHE_TTL = 10 * 60

#: 每个平台优先找的安装包文件名（按顺序回退）
_WINDOWS_ASSETS = ("tonicuisc-windows-x64-setup.exe", "tonicuisc-windows-x64-portable.zip")
_LINUX_ASSETS = ("tonicuisc-linux-x64.tar.gz",)

_VERSION_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)(?:[\s._-]*fix)?", re.IGNORECASE)


def _version_key(label: str | None) -> tuple[int, int, int, int] | None:
    match = _VERSION_RE.search(label or "")
    if not match:
        return None
    fix = 1 if re.search(r"fix", label or "", re.IGNORECASE) else 0
    return int(match.group(1)), int(match.group(2)), int(match.group(3)), fix


def _display_version(tag: str) -> str:
    match = _VERSION_RE.search(tag or "")
    if not match:
        return (tag or "").lstrip("v")
    fix = "fix" if re.search(r"fix", tag or "", re.IGNORECASE) else ""
    return f"{match.group(1)}.{match.group(2)}.{match.group(3)}{fix}"


def _asset_names(platform: str, arch: str | None) -> tuple[str, ...]:
    if platform == "windows":
        return _WINDOWS_ASSETS
    if platform == "linux":
        return _LINUX_ASSETS
    if platform == "macos":
        if (arch or "").lower() in {"x64", "x86_64", "intel"}:
            return ("tonicuisc-macos-x64.dmg",)
        return ("tonicuisc-macos-arm64.dmg",)
    return ()


class UpdateService:
    def __init__(
        self,
        *,
        repo: str | None = None,
        token: str | None = None,
        ttl: int = _CACHE_TTL,
        http_get: Any = None,
    ) -> None:
        self.repo = (repo or SETTINGS.github_repo).strip().strip("/")
        self.token = (token if token is not None else SETTINGS.github_token).strip()
        self.ttl = ttl
        self._http_get = http_get or requests.get
        self._lock = threading.Lock()
        self._cached_at = 0.0
        self._cached: dict[str, Any] | None = None

    # ------------------------------------------------------------------ 对外
    def check(
        self,
        platform: str,
        arch: str | None = None,
        version: str | None = None,
        build: str | None = None,
    ) -> dict[str, Any]:
        platform = (platform or "").strip().lower()
        if platform in {"ios", "android"}:
            return {
                "supported": False,
                "platform": platform,
                "message": "移动端不通过服务端检查更新",
            }
        if platform not in {"windows", "macos", "linux"}:
            raise ValueError(f"不支持的平台：{platform}")

        release = self.latest_release()
        if release is None:
            return {
                "supported": True,
                "platform": platform,
                "arch": arch or "",
                "ready": False,
                "latest": None,
                "has_update": False,
                "message": "还没有发布版本",
            }

        tag = str(release.get("tag_name") or "")
        latest = _display_version(tag)
        current_key = _version_key(version)
        latest_key = _version_key(latest)
        has_update = bool(latest_key and (current_key is None or latest_key > current_key))

        assets = {
            str(asset.get("name")): asset
            for asset in (release.get("assets") or [])
            if isinstance(asset, dict) and asset.get("name")
        }
        chosen = next((assets[name] for name in _asset_names(platform, arch) if name in assets), None)

        payload: dict[str, Any] = {
            "supported": True,
            "platform": platform,
            "arch": arch or "",
            "current": version or "",
            "build": build or "",
            "latest": latest,
            "has_update": has_update,
            "published_at": release.get("published_at") or "",
            "notes": str(release.get("body") or "")[:8000],
            "html_url": release.get("html_url") or "",
            "ready": chosen is not None,
            "asset": None,
        }
        if chosen is None:
            payload["message"] = "新版本已发布，但对应平台的安装包还没上传完，稍后再试"
            return payload

        direct_url = str(chosen.get("browser_download_url") or "")
        payload["asset"] = {
            "name": str(chosen.get("name") or ""),
            "size": int(chosen.get("size") or 0),
            "url": download_candidates(direct_url)[0],
            "direct_url": direct_url,
            "digest": str(chosen.get("digest") or ""),
        }
        return payload

    def latest_release(self) -> dict[str, Any] | None:
        """查最新正式 Release（带 10 分钟进程内缓存）。"""
        now = time.time()
        with self._lock:
            if self._cached is not None and now - self._cached_at < self.ttl:
                return self._cached
            data = self._fetch_latest()
            self._cached = data
            self._cached_at = time.time()
            return data

    def clear_cache(self) -> None:
        with self._lock:
            self._cached = None
            self._cached_at = 0.0

    # ------------------------------------------------------------------ 内部
    def _fetch_latest(self) -> dict[str, Any] | None:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "tonicuisc-update/1.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        url = f"https://api.github.com/repos/{self.repo}/releases/latest"
        started = time.perf_counter()
        response = self._http_get(url, headers=headers, timeout=_HTTP_TIMEOUT)
        try:
            response.raise_for_status()
        except requests.HTTPError:
            if getattr(response, "status_code", None) == 404:
                return None
            raise
        data = response.json()
        timing_log(f"[update] GitHub latest {self.repo}: {data.get('tag_name')} ({time.perf_counter() - started:.2f}s)")
        return data if isinstance(data, dict) else None


_service: UpdateService | None = None
_service_lock = threading.Lock()


def get_update_service() -> UpdateService:
    global _service
    with _service_lock:
        if _service is None:
            _service = UpdateService()
        return _service
