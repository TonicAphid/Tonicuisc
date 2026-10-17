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

from ..core.config import ENVIRONMENT, SETTINGS, display_host
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
    # 反代目标：**不能直接用 SETTINGS.host** —— 它是监听地址，0.0.0.0 / :: 表示
    # 「所有网卡」，拿去连是错的。display_host 会把它换成 127.0.0.1（和横幅同一套逻辑，
    # 避免两处各写一份、日后漂移）。
    upstream = f"{display_host(SETTINGS.host)}:{SETTINGS.port}"

    # 站点地址：默认就是裸域名（Caddy 按 443 处理）。住宅宽带 80/443 常被运营商
    # 封掉，被封时设 TONICUISC_HTTPS_PORT=8443 —— 站点地址就变成
    # uisc.tonicaphid.cn:8443，客户端也要用 https://uisc.tonicaphid.cn:8443。
    # 证书跟端口无关（lego 走 DNS-01），所以换端口不用重新签。
    https_port = SETTINGS.https_port
    site = ENVIRONMENT.domain if https_port == 443 else f"{ENVIRONMENT.domain}:{https_port}"

    # 有 lego 签好的 Let's Encrypt 证书就用它：Caddy 只终止 TLS，不再自己跑 ACME。
    # 好处：证书走 DNS-01 签（不需要入站 80/443），而且 Caddy 那串
    # 「challenge failed + 一大坨 Go 栈」的刷屏日志会**彻底消失**。
    from . import acme

    crt, key = acme.cert_paths(ENVIRONMENT.domain)
    if crt.exists() and key.exists():
        tls_block = f"\ttls {crt.as_posix()} {key.as_posix()}\n"
        tls_note = f"证书 {crt.name}（lego 签的，Caddy 不再自己申请）"
    else:
        # 还没有证书：留空，Caddy 用自己的 ACME（此时需要入站 80/443）
        tls_block = ""
        tls_note = "Caddy 自己申请证书（需要入站 80/443）"

    rendered = (
        template.replace("{DOMAIN}", ENVIRONMENT.domain)
        .replace("{SITE}", site)
        .replace("{UPSTREAM}", upstream)
        .replace("{LOG}", "caddy.log")
        .replace("{TLS}", tls_block)
        # 证书 / ACME 账号落在项目里，不碰 %AppData%（受限令牌下建不了目录，
        # Caddy 会直接放弃申请证书）
        .replace("{STORAGE}", str(data_dir()).replace("\\", "/"))
    )

    if target.exists() and not force:
        try:
            if target.read_text(encoding="utf-8") == rendered:
                return target
        except OSError:
            pass

    target.write_text(rendered, encoding="utf-8", newline="\n")
    _log(f"已生成配置：{target}（站点 {site} → {upstream}；{tls_note}）")
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


def state_dir() -> Path:
    """Caddy 的状态根目录（证书、ACME 账号、autosave.json 都在这下面）。

    **刻意放在项目里**，不用 ``%AppData%\\Caddy`` / ``~/.local/share/caddy``：

    * 自包含：整个部署就是一个目录，备份 / 搬机器 / 删干净都简单；
    * 不受用户配置目录权限影响——实测在受限令牌下 ``mkdir %AppData%\\Roaming\\Caddy``
      会 **Access denied**，Caddy 直接放弃申请证书（443 上握不了手，
      浏览器报 ``ERR_SSL_PROTOCOL_ERROR``，看起来像「证书不行」，其实压根没申请）；
    * 证书文件在项目里，排查时一眼能看到。
    """
    return SETTINGS.caddy_dir / ".caddy"


def data_dir() -> Path:
    """证书 / ACME 账号的存放处（Caddyfile 里 ``storage file_system`` 指向它）。"""
    return state_dir() / "data"


def config_home() -> Path:
    """Caddy 存 autosave.json 等配置的位置（靠 APPDATA / XDG_CONFIG_HOME 指过来）。"""
    return state_dir() / "config"


def caddy_env() -> dict[str, str]:
    """给 Caddy 子进程的环境变量：把它的家目录**按到项目里**。

    Windows 看 ``APPDATA``（``os.UserConfigDir()`` 的实现），
    Linux / macOS 看 ``XDG_*``。
    """
    env = dict(os.environ)
    home = str(config_home())
    env["APPDATA"] = home
    env["XDG_CONFIG_HOME"] = home
    env["XDG_DATA_HOME"] = str(data_dir())
    for path in (data_dir(), config_home()):
        path.mkdir(parents=True, exist_ok=True)
    return env


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
            env=caddy_env(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if result.returncode == 0:
        return True, "配置有效"
    detail = (result.stderr or result.stdout or "").strip().splitlines()
    return False, detail[-1] if detail else f"退出码 {result.returncode}"


def log_path() -> Path:
    """Caddy 的**运行时**日志（进程 stdout/stderr）。

    注意和 Caddyfile 里 ``output file caddy.log`` 那个**访问日志**不是一回事：
    那个是每来一个请求写一行，这个是启动/报错信息。分开命名免得混淆。
    """
    return SETTINGS.caddy_dir / ".caddy" / "runtime.log"


def tail_log(lines: int = 20, path: Path | None = None) -> str:
    """读日志文件最后几行（起不来时打给用户看）。"""
    target = path or log_path()
    if not target.exists():
        return ""
    try:
        content = target.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


def start_caddy(
    path: Path, config: Path, *, wait_seconds: float = 2.0, console: bool | None = None
) -> subprocess.Popen:
    """后台起 Caddy。

    **日志默认写文件**（``deploy/.caddy/runtime.log``），不刷控制台：
    Caddy 启动会吐十几行 INFO，最前面还有两行 Go 运行时信息
    （``maxprocs`` / ``GOMEMLIMIT``）——那是运行时直接写 stderr 的，
    **不经过 Caddy 的日志系统**，所以只调 Caddyfile 的日志级别掐不掉，必须重定向子进程。

    ``console=True`` 让日志直接刷当前终端（前台手动起 Caddy 时更直观）；
    默认（``None``）看配置项 ``TONICUISC_CADDY_LOG``。
    """
    config = config.resolve()
    if not config.exists():
        raise RuntimeError(f"找不到配置文件：{config}")

    console = SETTINGS.caddy_log_console if console is None else console
    _log(f"启动 Caddy：{path.name} run --config {config.name}")

    sink = None
    log_target = log_path()
    if console:
        stdout, stderr = None, None
    else:
        # 追加模式：上次的日志留着，排查历史问题时有用
        log_target.parent.mkdir(parents=True, exist_ok=True)
        sink = log_target.open("a", encoding="utf-8", errors="replace")
        stdout, stderr = sink, sink

    try:
        process = subprocess.Popen(
            [str(path), "run", "--config", str(config)],
            cwd=str(config.parent),
            env=caddy_env(),
            stdout=stdout,
            stderr=stderr,
        )
    finally:
        # 父进程这边不用留着这个句柄，子进程已经继承了
        if sink is not None:
            sink.close()

    # 给它一点时间；起不来（比如 443 被占）会立刻退出，这里就能发现
    time.sleep(wait_seconds)
    if process.poll() is not None:
        # 用**实际写过的那个文件**，别再查一遍全局 SETTINGS（那样测试里会读到别处）
        detail = tail_log(path=log_target)
        hint = f"\n最后几行日志（{log_target}）：\n{detail}" if detail and not console else ""
        raise RuntimeError(f"Caddy 启动后立刻退出了（退出码 {process.returncode}）{hint}")
    if not console:
        _log(f"日志在 {log_target}（实时刷屏：TONICUISC_CADDY_LOG=console）")
    return process
    return process


def admin_alive(*, timeout: float = 1.0) -> bool:
    """Caddy 的管理端点（默认 ``127.0.0.1:2019``）有没有人在听。

    **管理端点是全机唯一的**：已经有一个 Caddy 在跑时，再起一个会直接失败：
    ``starting caddy administration endpoint: listen tcp 127.0.0.1:2019: bind: ...``。
    实测踩过——手动开的那个还在，自动启动就永远起不来，还以为是端口/权限问题。
    """
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen("http://127.0.0.1:2019/config/", timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def ensure_running(*, auto_download: bool | None = None) -> subprocess.Popen | None:
    """把 Caddy 拉起来（起服务时顺手调用）。**失败只报错，不抛异常。**

    服务端不该因为「Caddy 起不来」就整个起不来——多半是端口被占 / 没权限 /
    入站没放行，这些都是可以边跑边修的。所以这里把原因讲清楚，然后返回 ``None``。
    """
    # 已经有一个在跑（比如你手动开的）：**不再起第二个**，管理端口会打架。
    # 返回 None 表示「不是我们起的」，退出时也就不会去停它。
    if admin_alive():
        _log("已经有一个 Caddy 在跑了（管理端点 127.0.0.1:2019 有响应），不重复启动")
        _log("如果你想让它用新配置：先停掉那个，再重启本服务（或用 caddy reload）")
        return None

    try:
        path = find_caddy(auto_download=SETTINGS.caddy_download if auto_download is None else auto_download)
    except (RuntimeError, OSError, urllib.error.URLError) as exc:
        # 只接「下载/解压失败」这类预期错误。**别用裸 Exception**：
        # 那样连 AttributeError 这种真 bug 都会被报成「没找到 Caddy」，
        # 排查时会往完全错的方向找（实测踩过）。
        _log(f"没找到 Caddy，也没下下来：{type(exc).__name__}: {exc}")
        _log("手动放一个到 deploy/win/caddy.exe（或 deploy/linux/caddy）再重启")
        return None
    if path is None:
        _log("没找到 Caddy（deploy/<win|linux>/ 和 PATH 里都没有）")
        return None

    # 先弄证书：有 lego 签的 Let's Encrypt 证书（DNS-01，不需要入站 80/443），
    # Caddy 就只终止 TLS、不再自己跑 ACME（那串 Go 栈日志也就没了）。
    # 签不下来不算致命：Caddy 还能退回自己的 ACME（此时才需要入站端口）。
    if SETTINGS.acme_dns01:
        from . import acme

        if acme.needs_renewal(ENVIRONMENT.domain):
            acme.ensure(ENVIRONMENT.domain, email=SETTINGS.acme_email, ca=SETTINGS.acme_ca)

    try:
        config = generate_config()
    except Exception as exc:
        _log(f"生成 Caddyfile 失败：{exc}")
        return None

    ok, detail = validate_config(path, config)
    if not ok:
        _log(f"Caddyfile 校验没过：{detail}")
        return None

    try:
        process = start_caddy(path, config)
    except Exception as exc:
        _log(f"Caddy 没起来：{exc}")
        if admin_alive():
            _log("（管理端口有响应——可能是刚才那一刻另一个 Caddy 正好起来了）")
        else:
            _log("443 / 80 被占用？还是入站没放行（管理员跑 deploy/open-firewall.ps1）？")
        return None

    _log(f"Caddy 已启动（pid {process.pid}）：{ENVIRONMENT.domain} → {display_host(SETTINGS.host)}:{SETTINGS.port}")
    return process


def stop_caddy(process: subprocess.Popen | None, *, timeout: float = 5.0) -> None:
    """停掉我们拉起来的 Caddy（服务端退出时调用，别留孤儿进程占着 443）。"""
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=timeout)
        _log("Caddy 已停止")
    except Exception:
        try:
            process.kill()
            _log("Caddy 没乖乖退出，已强杀")
        except Exception:
            pass
