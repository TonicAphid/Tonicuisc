"""从 GitHub Releases 下载二进制的公共部分（Caddy 和 lego 共用）。

国内直连 GitHub Releases 经常很慢或直接失败，所以**优先走加速镜像**。
镜像逻辑只写这一份：:mod:`tonicuisc_server.core.caddy` 和
:mod:`tonicuisc_server.core.lego` 都从这里拿，避免两处漂移。

``TONICUISC_GH_PROXY`` 可以覆盖镜像：

* 没设 —— 用内置的几个镜像 + 最后直连兜底；
* 设成 ``https://xxx/`` —— 只用这个镜像 + 直连兜底；
* 设成空串 —— 只直连（机器本身能上 GitHub 时最快）。
"""

from __future__ import annotations

import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

#: 单个文件的下载超时（秒）
DOWNLOAD_TIMEOUT = 180.0
#: 下载缓冲块
DOWNLOAD_CHUNK = 256 * 1024

#: GitHub 加速镜像（前缀式：把原始 URL 整个接在后面）
DEFAULT_GH_PROXIES = (
    "https://gh-proxy.com/",
    "https://ghfast.top/",
    "https://ghproxy.net/",
)


def download_candidates(url: str) -> list[str]:
    """给一个 GitHub 原始 URL，返回按优先级排好的下载地址列表（直连永远在最后）。"""
    configured = os.getenv("TONICUISC_GH_PROXY")
    if configured is None:
        proxies = list(DEFAULT_GH_PROXIES)
    else:
        configured = configured.strip()
        proxies = [configured] if configured else []

    candidates: list[str] = []
    for prefix in proxies:
        candidates.append(prefix.rstrip("/") + "/" + url)
    candidates.append(url)  # 直连兜底
    return candidates


def download_file(url: str, dest: Path, *, label: str = "") -> Path:
    """下载到临时文件、成功后原子改名；失败不留半个文件。

    ``label`` 只用于日志前缀（``[caddy]`` / ``[lego]``）。
    """
    prefix = f"[{label}] " if label else ""

    def say(message: str) -> None:
        print(f"{prefix}{message}", flush=True)

    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "tonicuisc-fetch/1.0"})
    fd, temp_name = tempfile.mkstemp(prefix=f"{dest.name}.", suffix=".part", dir=str(dest.parent))
    temp_path = Path(temp_name)
    written = 0
    try:
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
            while True:
                chunk = response.read(DOWNLOAD_CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                written += len(chunk)
        os.replace(temp_path, dest)
    except BaseException:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    say(f"下载完成 {dest.name}（{written / 1048576:.1f} MB）")
    return dest


__all__ = [
    "DEFAULT_GH_PROXIES",
    "DOWNLOAD_CHUNK",
    "DOWNLOAD_TIMEOUT",
    "download_candidates",
    "download_file",
]
