"""Runtime settings for the Tonicuisc server.

Everything is read from environment variables so the same image can be reused
for local development and for a container deployment.

**先加载 ``server/.env`` 再读配置**：真正的环境变量优先级更高（``.env`` 不覆盖已有的），
这样临时覆盖（``TONICUISC_PORT=9000 python -m ...``）和 systemd 里配的都管用。
"""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .constants import DEFAULT_THREAD_POOL_SIZE
from .environment import ENV_KEY, ENVIRONMENTS, Environment, SERVER_DIR, get_environment, load_env_file

#: 加载 ``.env`` **之前**就存在的真实环境变量（shell / systemd / 临时覆盖）。
#: 重启时子进程要保留这些、同时把「只来自 .env」的键摘掉，让它重新读文件——
#: 否则改了 server/.env 再 restart，子进程拿到的还是父进程里的旧值。
EXTERNAL_ENV_KEYS = frozenset(os.environ)

#: 先把 .env 灌进 os.environ，下面的 os.getenv 才读得到它
ENV_FILE_VALUES = load_env_file()

#: 当前环境（uisc 正式 / tuisc 测试）；影响对外域名、横幅、DDNS 目标
ENVIRONMENT: Environment = get_environment()

#: short alias -> musicdl client class name.  Only Migu and Kuwo are enabled
#: by design; add more entries here if you ever want extra sources.
SOURCE_ALIASES: dict[str, str] = {
    "migu": "MiguMusicClient",
    "kuwo": "KuwoMusicClient",
}

SOURCE_LABELS: dict[str, str] = {
    "migu": "咪咕",
    "kuwo": "酷我",
}


def source_alias(client_class_name: str) -> str:
    """``MiguMusicClient`` -> ``migu``（未知音源原样返回）。"""
    for alias, class_name in SOURCE_ALIASES.items():
        if client_class_name == class_name:
            return alias
    return client_class_name


def source_label(client_class_name: str) -> str:
    alias = source_alias(client_class_name)
    return SOURCE_LABELS.get(alias, client_class_name)


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if not raw:
        return default
    return [part.strip().lower() for part in raw.replace(";", ",").split(",") if part.strip()]


def resolve_sources(aliases: list[str]) -> list[str]:
    """Translate short aliases (``migu``) into musicdl class names.

    别名（大小写不敏感）和类名（``MiguMusicClient``）都认——以前只在前半段认类名，
    紧接着又用「小写别名表」判 unknown，于是传类名会被接受后再抛一次 ValueError。
    """
    resolved: list[str] = []
    unknown: list[str] = []
    class_names = set(SOURCE_ALIASES.values())
    for alias in aliases:
        key = alias.strip().lower()
        if key in SOURCE_ALIASES:
            resolved.append(SOURCE_ALIASES[key])
        elif alias.strip() in class_names:
            resolved.append(alias.strip())
        else:
            unknown.append(alias)
    if unknown:
        raise ValueError(f"unsupported source(s): {', '.join(unknown)}; supported: {', '.join(SOURCE_ALIASES)}")
    return resolved


@dataclass(frozen=True)
class Settings:
    #: 监听地址。默认 **127.0.0.1**：家里部署时 Caddy 反代到回环，
    #: 服务端不该直接对外（0.0.0.0 会让外网绕过 Caddy 的 TLS 和鉴权链）。
    #: 想让局域网 / 外网直连（不装 Caddy）时再显式改成 0.0.0.0 或 ::。
    host: str = os.getenv("TONICUISC_HOST", "127.0.0.1")
    port: int = int(os.getenv("TONICUISC_PORT", "8000"))
    #: 网页（落地页 + 播放器）由 **Python 自己托管**：`/`、`/music`、`/music/api/session`
    #: 都在同一个进程里，`python -m tonicuisc_server` 一条命令就全跑起来。
    #: 设 0 回到老拓扑：Caddy 反代到下面的 ``web_port``（Node 的 webapp/server.js），
    #: Node 再把 `/api` 转给本进程。
    web_builtin: bool = os.getenv("TONICUISC_WEB_BUILTIN", "1").lower() not in {"0", "false", "no", "off"}
    #: Node 网页入口的端口。**只在 `web_builtin=0` 时有意义**（Caddy 反代到这里）；
    #: 不设就退回 Python 自己的端口。以前这里默认 8080，单跑 Python 时会把 Caddy
    #: 指到一个没人监听的端口，所以默认值改成了 0。
    web_port: int = int(os.getenv("TONICUISC_WEB_PORT", "0") or 0)
    #: 内置网页的静态目录（落地页 / 播放器的 HTML/CSS/JS）。默认仓库里的 webapp/public，
    #: 单独打包部署时可以用这个变量指过去。
    web_public_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("TONICUISC_WEB_PUBLIC", SERVER_DIR.parent / "webapp" / "public")
        ).resolve()
    )
    #: 网页会话（HttpOnly Cookie）有效期（秒），默认 7 天；进程重启会清空内存会话
    web_session_ttl: int = int(os.getenv("TONICUISC_WEB_SESSION_TTL", str(7 * 24 * 3600)))
    #: 「记住这台浏览器」：浏览器带一枚长期指纹 Cookie（``tuisc_web_id``），服务端把它绑到
    #: 一台设备记录上。进程重启后内存会话没了，靠这条绑定**自动重新签发 key**，
    #: 所以网页播放器不用每次重启都重新配对；同一个浏览器也只会占设备列表里的一条。
    web_remember: bool = os.getenv("TONICUISC_WEB_REMEMBER", "1").lower() not in {"0", "false", "no", "off"}
    #: 上面那条绑定的有效期（秒），默认 30 天；每次成功恢复登录都会续期。
    #: 设备被吊销 / 从设备列表删掉时绑定立刻失效。
    web_remember_ttl: int = int(os.getenv("TONICUISC_WEB_REMEMBER_TTL", str(30 * 24 * 3600)))
    #: 对外地址，用来在启动横幅里打印登录链接，例如 https://106-35-196-104.nip.io
    public_url: str = os.getenv("TONICUISC_PUBLIC_URL", "").strip()
    #: Caddy 对外监听的 HTTPS 端口。住宅宽带常被运营商封掉 80/443 —— 被封时改成
    #: 非标端口（比如 8443），Caddy 站点地址会变成 ``域名:8443``，客户端地址也要
    #: 跟着带上端口。证书跟端口无关（lego 走 DNS-01），换端口不用重新签。
    https_port: int = int(os.getenv("TONICUISC_HTTPS_PORT", "443"))
    reload: bool = os.getenv("TONICUISC_RELOAD", "0") in {"1", "true", "yes"}
    sources: list[str] = field(default_factory=lambda: _env_list("TONICUISC_SOURCES", ["kuwo"]))
    #: 桌面端检查更新用的 GitHub 仓库（owner/repo）与可选 token（提高 API 额度）
    github_repo: str = os.getenv("TONICUISC_GITHUB_REPO", "TonicAphid/Tonicuisc").strip().strip("/")
    github_token: str = os.getenv("TONICUISC_GITHUB_TOKEN", "").strip()
    #: 每个音源的抓取下限（真正抓多少按「这次要几条 ÷ 音源数」分摊）
    search_size: int = int(os.getenv("TONICUISC_SEARCH_SIZE", "5"))
    #: 每个请求取几首（下限）：实际每页条数 = max(这个值, ceil(要几条 / 并发数))
    search_size_per_page: int = int(os.getenv("TONICUISC_SEARCH_SIZE_PER_PAGE", "1"))
    #: 每个音源同时并发多少个请求（musicdl 每个请求内部串行解析，所以并发数=并行度）。
    #: 实测酷我 15 条：5 路 3.6s、15 路 2.0s；上游抖动时（单首 4s）5 路要 12.7s、15 路 ~5s。
    #: 拧大是拿上游并发换稳定时延，怕被限流就设回 5。
    search_threads: int = int(os.getenv("TONICUISC_SEARCH_THREADS", "10"))
    #: 客户端一页多少条（滑到底再要下一页）
    search_page_size: int = int(os.getenv("TONICUISC_SEARCH_PAGE_SIZE", "15"))
    #: 一次搜索最多抓多少条（分页上限，别让请求数炸）
    search_max: int = int(os.getenv("TONICUISC_SEARCH_MAX", "60"))
    #: 设备级 API Key 校验（关闭后任何人都能调用接口）
    auth_enabled: bool = os.getenv("TONICUISC_AUTH", "1").lower() not in {"0", "false", "no", "off"}
    #: 设备码（登录请求）有效期（秒）
    device_code_ttl: int = int(os.getenv("TONICUISC_DEVICE_CODE_TTL", "600"))
    #: 是否信任反向代理写的 X-Forwarded-For（限速按真实来源 IP 算）
    trust_proxy: bool = os.getenv("TONICUISC_TRUST_PROXY", "1").lower() not in {"0", "false", "no", "off"}
    #: 用 QQ 音乐补封面（咪咕/酷我的封面经常糊）
    qq_cover: bool = os.getenv("TONICUISC_QQ_COVER", "1").lower() not in {"0", "false", "no", "off"}
    #: 补封面时的并发数（QQ 单次要 3~4 秒，实测 8 路比 4 路快一倍）
    qq_cover_threads: int = int(os.getenv("TONICUISC_QQ_COVER_THREADS", "8"))
    #: /api/covers 最多等正在跑的封面查询多久（秒）；到点还没结果就**先不返回**这
    #: 个 id（「还没定论」），客户端过几秒再问——把它记成原图/空串就等于结案了
    qq_cover_wait: float = float(os.getenv("TONICUISC_QQ_COVER_WAIT", "25"))
    #: QQ 封面查询的记忆条数（同一首歌/同一批歌重复查时秒回）
    qq_cover_cache_size: int = int(os.getenv("TONICUISC_QQ_COVER_CACHE", "2048"))
    # ---------------------------------------------------------------- 搜索缓存
    #: 搜索结果在 Redis 里的保存时间（秒）；重启后依然命中，不用重新联网搜
    search_ttl: int = int(os.getenv("TONICUISC_SEARCH_TTL", str(24 * 3600)))
    #: 「软刷新」窗口（秒）：客户端带 refresh=true 进来时，缓存比这新就直接返回，
    #: 同时在后台重新搜一遍更新缓存。App 每次搜索都带 refresh=true，靠它才不会每次重搜。
    search_soft_ttl: int = int(os.getenv("TONICUISC_SEARCH_SOFT_TTL", "600"))
    # ------------------------------------------------------------------- Redis
    #: 完整 Redis 地址（``redis://[:密码@]主机:端口/库号``）。给了就用它，
    #: 这时服务端只**连接**、不负责启动/关闭 Redis。
    redis_url: str = os.getenv("TONICUISC_REDIS_URL", "").strip()
    #: 没给 redis_url 时，用下面这组参数连本地 Redis（也是托管模式启动时的监听参数）
    redis_host: str = os.getenv("TONICUISC_REDIS_HOST", "127.0.0.1").strip() or "127.0.0.1"
    redis_port: int = int(os.getenv("TONICUISC_REDIS_PORT", "6390"))
    redis_db: int = int(os.getenv("TONICUISC_REDIS_DB", "0"))
    #: 指定 redis-server 可执行文件；不给就按 redis/<系统>/ → PATH → 自动下载 找
    redis_server: str = os.getenv("TONICUISC_REDIS_SERVER", "").strip()
    #: 托管 Redis 的目录：二进制放 <redis_dir>/<win|linux>/，数据放 <cache_dir>/redis/
    redis_dir: Path = field(
        default_factory=lambda: Path(os.getenv("TONICUISC_REDIS_DIR", SERVER_DIR.parent / "redis")).resolve()
    )
    #: 启动服务时自动拉起 Redis；0 = 只连已运行的（连不上就报错）
    redis_autostart: bool = os.getenv("TONICUISC_REDIS_AUTOSTART", "1").lower() not in {"0", "false", "no", "off"}
    #: 首次启动没找到 redis-server 时自动下载（0 = 直接报错，让人手动装）
    redis_download: bool = os.getenv("TONICUISC_REDIS_DOWNLOAD", "1").lower() not in {"0", "false", "no", "off"}
    #: 托管实例开不开 AOF 持久化（数据全在 Redis 里，默认开，退出重启不丢）
    redis_appendonly: bool = os.getenv("TONICUISC_REDIS_APPENDONLY", "1").lower() not in {"0", "false", "no", "off"}
    cache_dir: Path = field(
        default_factory=lambda: Path(os.getenv("TONICUISC_CACHE_DIR", SERVER_DIR / ".cache")).resolve()
    )
    # --------------------------------------------------------------------- TLS
    #: 证书链 PEM 路径（和 ``tls_key`` 一起给才开原生 HTTPS）
    tls_cert: str = os.getenv("TONICUISC_TLS_CERT", "").strip()
    #: 私钥 PEM 路径
    tls_key: str = os.getenv("TONICUISC_TLS_KEY", "").strip()
    #: ``asyncio.to_thread`` 的线程池大小（搜索 / 下载 / Redis 调用都走它）
    thread_pool_size: int = int(os.getenv("TONICUISC_THREADS", str(DEFAULT_THREAD_POOL_SIZE)))
    # ------------------------------------------------------------------- Caddy
    #: 指定 caddy 可执行文件；不给就按 deploy/bin/<系统>/ → PATH → 自动下载 找
    caddy_server: str = os.getenv("TONICUISC_CADDY_SERVER", "").strip()
    #: 找不到 caddy 时自动下载（0 = 只找不下载）
    caddy_download: bool = os.getenv("TONICUISC_CADDY_DOWNLOAD", "1").lower() not in {"0", "false", "no", "off"}
    #: 启动服务时顺手把 Caddy 也拉起来（**默认开**：家里部署本来就两个进程，
    #: 少开一个窗口；设 0 关掉，比如只想在局域网直连、不挂 HTTPS 的时候）
    caddy_autostart: bool = os.getenv("TONICUISC_CADDY_AUTOSTART", "1").lower() not in {"0", "false", "no", "off"}
    #: Caddy 的运行时日志往哪写。
    #: 默认 ``file``（写进 deploy/.caddy/caddy.log）——Caddy 启动会吐十几行
    #: INFO + Go 运行时信息，混在服务端横幅里很乱；出问题时我们会自动把文件尾部打出来。
    #: 想看实时刷屏就设 ``console``（或跑 caddy --start --verbose）。
    caddy_log_console: bool = os.getenv("TONICUISC_CADDY_LOG", "file").strip().lower() in {
        "console",
        "1",
        "true",
        "yes",
        "on",
    }
    #: Caddy 二进制 / 配置的目录（配置就是仓库里的 deploy/）
    caddy_dir: Path = field(
        default_factory=lambda: Path(os.getenv("TONICUISC_CADDY_DIR", SERVER_DIR.parent / "deploy")).resolve()
    )
    # -------------------------------------------------------------------- ACME
    #: 用 lego + **DNS-01**（阿里云解析）向 Let's Encrypt 签证书：不需要入站 80/443。
    #: 默认开——大陆家用宽带常封 80/443，Caddy 自带的 HTTP-01 / TLS-ALPN-01 根本签不下来。
    acme_dns01: bool = os.getenv("TONICUISC_ACME_DNS01", "1").lower() not in {"0", "false", "no", "off"}
    #: 证书联系邮箱（Let's Encrypt 用它发到期提醒，可留空）
    acme_email: str = os.getenv("TONICUISC_ACME_EMAIL", "").strip()
    #: ACME 服务器；调试时填 staging 可避免撞生产环境的签发限额
    acme_ca: str = os.getenv("TONICUISC_ACME_CA", "").strip()

    @property
    def caddy_config(self) -> Path:
        """Caddyfile 的位置。"""
        return self.caddy_dir / "Caddyfile"

    @property
    def tls_enabled(self) -> bool:
        """两个路径都给了才算开了 HTTPS —— 只给一个就当作没配，别猜。"""
        return bool(self.tls_cert and self.tls_key)

    @property
    def scheme(self) -> str:
        """对外协议：开了 TLS 就是 https（登录链接、探活提示都用它）。"""
        return "https" if self.tls_enabled else "http"

    @property
    def work_dir(self) -> Path:
        """Where musicdl stores downloaded audio files."""
        return self.cache_dir / "music"

    @property
    def db_path(self) -> Path:
        """旧 SQLite 库的位置（只给 ``scripts/import_sqlite.py`` 迁移老数据用）。"""
        return Path(os.getenv("TONICUISC_DB", self.cache_dir / "tonicuisc.db")).resolve()

    @property
    def redis_data_dir(self) -> Path:
        """托管 Redis 的 AOF/RDB 落盘目录。"""
        return self.cache_dir / "redis"

    @property
    def redis_bin_dir(self) -> Path:
        """当前系统的 redis-server 存放目录：``redis/win`` 或 ``redis/linux``。"""
        return self.redis_dir / ("win" if os.name == "nt" else "linux")

    def redis_config(self) -> dict[str, Any]:
        """redis-py 的连接参数（有 redis_url 就整条用它）。"""
        if self.redis_url:
            return {"url": self.redis_url}
        return {"host": self.redis_host, "port": self.redis_port, "db": self.redis_db}


def display_host(host: str) -> str:
    """把**监听地址**换成**能点的地址**。

    ``0.0.0.0`` / ``::`` 是「所有网卡」的意思，拿去当 URL 主机名是没用的
    （横幅会打出 ``http://0.0.0.0:8000/login`` 这种点不开的链接）。
    """
    value = (host or "").strip()
    if value in {"0.0.0.0", "::", "[::]", "*", ""}:
        return "127.0.0.1"
    return value


SETTINGS = Settings()


def apply_runtime_env() -> Settings:
    """把「刚选好的环境」反映到 :data:`SETTINGS` / :data:`ENVIRONMENT` 上（同进程内）。

    为什么需要它：``Settings`` 的默认值是**类定义时**读环境变量的（``= os.getenv(...)``
    只在类体执行那一次求值），所以启动时选完环境再往 ``os.environ`` 里写值，
    ``SETTINGS.public_url`` 还是空的——横幅、登录页链接就会退回 ``http://127.0.0.1:8000``。

    这里显式重建受影响的那几个字段。只影响 ``public_url``（环境选择唯一会改的东西）；
    ``api.app`` 是在 uvicorn 启动时才 import 的，所以它能拿到重置后的对象。
    """
    global SETTINGS, ENVIRONMENT

    ENVIRONMENT = get_environment()
    SETTINGS = dataclasses.replace(SETTINGS, public_url=os.getenv("TONICUISC_PUBLIC_URL", "").strip())
    return SETTINGS
