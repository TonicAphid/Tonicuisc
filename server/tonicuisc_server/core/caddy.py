"""Caddy 的定位 / 自动下载 / 启动（跨平台，Windows 和 Linux 都认）。

家里部署用 Caddy 终止 TLS（自动向 Let's Encrypt 申请并续期），这里只管
「找到 caddy、找不到就下一个」——和 :mod:`tonicuisc_server.storage.redis_runtime` 一个套路。

查找顺序（第一个能用就用）：

1. ``TONICUISC_CADDY_SERVER`` 指定的可执行文件；
2. ``deploy/bin/<win|linux>/`` 下我们下载的那个；
3. ``PATH`` 里的 ``caddy``。

下载源是 Caddy 官方 GitHub Releases：
Windows 用 zip，Linux 用 tar.gz（tar.gz 标准库就能解，比 Redis 那个 deb 简单）。
**GitHub 不一定连得上**（这台机器实测 HTTPS 出网会被拦），所以失败时会把
「手动放哪、去哪下」讲清楚，而不是甩个栈。
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from ..core.config import ENVIRONMENT, SETTINGS
from ..core.logging import timing_log

#: 下载超时（秒）：Caddy 单个平台包约 15 MB
DOWNLOAD_TIMEOUT = 180.0
#: 下载缓冲块
DOWNLOAD_CHUNK = 256 * 1024

#: 官方 release 地址模板（版本固定，和 Redis 那边一样避免「今天能下明天不能」）
CADDY_VERSION = "2.10.0"
_RELEASE = f"https://github.com/caddyserver/caddy/releases/download/v{CADDY_VERSION}"
WINDOWS_ZIP_URL = f"{_RELEASE}/caddy_{CADDY_VERSION}_windows_amd64.zip"
LINUX_TAR_URL = f"{_RELEASE}/caddy_{CADDY_VERSION}_linux_amd64.tar.gz"

#: GitHub 加速镜像（前缀式：把原始 URL 整个接在后面）。
#: 家里 / 国内机器直连 GitHub Releases 经常很慢或直接失败，所以**优先走代理**。
#: 想换别的（或直连）就用 ``TONICUISC_GH_PROXY`` 覆盖，设成空串 = 只直连。
DEFAULT_GH_PROXIES = (
    "https://gh-proxy.com/",
    "https://ghfast.top/",
    "https://ghproxy.net/",
)

#: 手动下载时告诉用户去哪拿
DOWNLOAD_PAGE = "https://caddyserver.com/download"


def _log(message: str) -> None:
    """``[caddy]`` 前缀日志，直接 stdout（跟 Redis 那边保持一致）。"""
    try:
        print(f"[caddy] {message}", flush=True)
    except UnicodeEncodeError:
        timing_log(f"caddy: {message.encode('ascii', 'replace').decode('ascii')}")


def is_windows() -> bool:
    return os.name == "nt"


def binary_name() -> str:
    return "caddy.exe" if is_windows() else "caddy"


def bin_dir() -> Path:
    """二进制存放目录：``deploy/<win|linux>/``。"""
    return SETTINGS.caddy_dir / ("win" if is_windows() else "linux")


def download_candidates(url: str) -> list[str]:
    """给一个 GitHub 原始 URL，返回按优先级排好的下载地址列表。

    ``TONICUISC_GH_PROXY`` 可以覆盖：

    * 没设 —— 用内置的几个镜像 + 最后直连兜底；
    * 设成 ``https://xxx/`` —— 只用这个镜像 + 直连兜底；
    * 设成空串 —— 只直连（机器本身能上 GitHub 时最快）。
    """
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


def _download_file(url: str, dest: Path) -> Path:
    """下载到临时文件、成功后原子改名；失败不留半个文件。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "tonicuisc-caddy-fetch/1.0"})
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
    _log(f"下载完成 {dest.name}（{written / 1048576:.1f} MB）")
    return dest


def _extract_windows(archive: Path, target: Path) -> None:
    """Windows：zip 里的 caddy.exe。"""
    with zipfile.ZipFile(archive) as bundle:
        member = next((name for name in bundle.namelist() if name.endswith("caddy.exe")), None)
        if member is None:
            raise RuntimeError(f"压缩包 {archive.name} 里没有 caddy.exe")
        with bundle.open(member) as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst)


def _extract_linux(archive: Path, target: Path) -> None:
    """Linux：tar.gz 里的 caddy（顺便把 LICENSE / README 忽略掉）。"""
    with tarfile.open(archive, "r:gz") as bundle:
        member = next(
            (item for item in bundle.getmembers() if item.isfile() and Path(item.name).name == "caddy"),
            None,
        )
        if member is None:
            raise RuntimeError(f"压缩包 {archive.name} 里没有 caddy")
        extracted = bundle.extractfile(member)
        if extracted is None:
            raise RuntimeError(f"压缩包 {archive.name} 里的 caddy 读不出来")
        with extracted, target.open("wb") as dst:
            shutil.copyfileobj(extracted, dst)
    target.chmod(0o755)


def download_caddy() -> Path:
    """下载并解出 caddy；返回可执行文件路径。

    按 :func:`download_candidates` 的顺序逐个试（镜像 → 直连），
    哪个先成功用哪个；**全失败才报错**，并把「手动放哪、去哪下」讲清楚。
    """
    target = bin_dir() / binary_name()
    target.parent.mkdir(parents=True, exist_ok=True)
    source = WINDOWS_ZIP_URL if is_windows() else LINUX_TAR_URL
    candidates = download_candidates(source)
    suffix = ".zip" if is_windows() else ".tar.gz"
    archive = target.parent / f"caddy{suffix}"

    _log(f"本地没有 caddy，开始下载 v{CADDY_VERSION}（{len(candidates)} 个源，逐个试）")
    errors: list[str] = []
    for index, url in enumerate(candidates, start=1):
        host = url.split("/")[2] if "//" in url else url
        _log(f"[{index}/{len(candidates)}] {host}")
        try:
            _download_file(url, archive)
            if is_windows():
                _extract_windows(archive, target)
            else:
                _extract_linux(archive, target)
        except (urllib.error.URLError, OSError, RuntimeError, zipfile.BadZipFile, tarfile.TarError) as exc:
            errors.append(f"{host}: {type(exc).__name__}: {exc}")
            _log(f"  这个源不行（{type(exc).__name__}），换下一个")
            try:
                archive.unlink(missing_ok=True)
            except OSError:
                pass
            continue
        try:
            archive.unlink(missing_ok=True)
        except OSError:
            pass
        if not target.exists():
            errors.append(f"{host}: 解压后没看到 {target}")
            continue
        _log(f"已就绪：{target}（来自 {host}）")
        return target

    detail = "\n".join(f"  · {line}" for line in errors) or "  · （没有可用的下载源）"
    raise RuntimeError(
        f"下载 / 解压 caddy 失败，试过的源都不行：\n{detail}\n"
        f"  · 换个镜像：设 TONICUISC_GH_PROXY=https://你的镜像/ （留空 = 只直连 GitHub）\n"
        f"  · 手动下载：{DOWNLOAD_PAGE}（选 windows/linux amd64）\n"
        f"  · 解出 {binary_name()} 放到：{bin_dir()}\n"
        f"  · 或者把已有的 caddy 路径写进 .env：TONICUISC_CADDY_SERVER=<完整路径>"
    )


def generate_config(*, force: bool = False) -> Path:
    """按当前环境生成实际要跑的 Caddyfile。

    正式（uisc）和测试（tuisc）域名不同，Caddy 必须按自己的域名去签证书——
    所以配置不能写死，得从模板生成。已存在且内容相同就不重写（避免 Caddy 被
    无谓地 reload）；``force=True`` 强制重写。
    """
    template_path = SETTINGS.caddy_dir / "Caddyfile.template"
    target = SETTINGS.caddy_config
    if not template_path.exists():
        # 没有模板（比如用户手写了一份 Caddyfile）：直接用它，别覆盖
        if target.exists():
            return target
        raise RuntimeError(f"既没有 {template_path} 也没有 {target}")

    template = template_path.read_text(encoding="utf-8")
    # 反代目标：**不能直接用 SETTINGS.host** —— 它是监听地址，
    # 默认 0.0.0.0（表示「所有网卡」），拿去连是错的。这里映射成可连接的地址：
    #   · 0.0.0.0 / :: / 空 → 127.0.0.1（本机回环）
    #   · 其余原样（用户真写了具体地址就听他的）
    host = (SETTINGS.host or "").strip()
    if host in {"0.0.0.0", "::", "[::]", "*", ""}:
        host = "127.0.0.1"
    upstream = f"{host}:{SETTINGS.port}"
    rendered = (
        template.replace("{DOMAIN}", ENVIRONMENT.domain)
        .replace("{UPSTREAM}", upstream)
        .replace("{LOG}", "caddy.log")
    )

    if target.exists() and not force:
        try:
            if target.read_text(encoding="utf-8") == rendered:
                return target
        except OSError:
            pass

    target.write_text(rendered, encoding="utf-8", newline="\n")
    _log(f"已生成配置：{target}（域名 {ENVIRONMENT.domain} → {upstream}）")
    return target


def find_caddy(*, auto_download: bool = True) -> Path | None:
    """按顺序找 caddy；``auto_download`` 为真且都没有时下载一个。"""
    configured = (SETTINGS.caddy_server or "").strip()
    if configured:
        path = Path(configured).expanduser()
        if path.exists():
            return path
        _log(f"TONICUISC_CADDY_SERVER 指向的 {path} 不存在，继续按顺序找")

    local = bin_dir() / binary_name()
    if local.exists():
        return local

    on_path = shutil.which("caddy")
    if on_path:
        return Path(on_path)

    if not auto_download or not SETTINGS.caddy_download:
        return None
    return download_caddy()


def caddy_version(path: Path) -> str:
    """跑一下 ``caddy version`` 拿版本号（拿不到就返回空）。"""
    try:
        result = subprocess.run(
            [str(path), "version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (result.stdout or "").strip().splitlines()[0] if result.stdout else ""


def validate_config(path: Path, config: Path) -> tuple[bool, str]:
    """``caddy validate --config``：改完配置先验一遍，别等启动才发现语法错。"""
    try:
        result = subprocess.run(
            [str(path), "validate", "--config", str(config)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if result.returncode == 0:
        return True, "配置有效"
    detail = (result.stderr or result.stdout or "").strip().splitlines()
    return False, detail[-1] if detail else f"退出码 {result.returncode}"


def start_caddy(path: Path, config: Path, *, wait_seconds: float = 2.0) -> subprocess.Popen:
    """后台起 Caddy（日志直接进当前控制台，和 uvicorn 的输出混在一起更好排查）。"""
    config = config.resolve()
    if not config.exists():
        raise RuntimeError(f"找不到配置文件：{config}")
    _log(f"启动 Caddy：{path} run --config {config}")
    process = subprocess.Popen(
        [str(path), "run", "--config", str(config)],
        cwd=str(config.parent),
    )
    # 给它一点时间；起不来（比如 443 被占）会立刻退出，这里就能发现
    time.sleep(wait_seconds)
    if process.poll() is not None:
        raise RuntimeError(f"Caddy 启动后立刻退出了（退出码 {process.returncode}），看一下上面的日志")
    return process
