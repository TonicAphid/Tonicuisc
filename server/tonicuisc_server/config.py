"""Runtime settings for the Tonicuisc server.

Everything is read from environment variables so the same image can be reused
for local development and for a container deployment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SERVER_DIR = Path(__file__).resolve().parent.parent

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
    host: str = os.getenv("TONICUISC_HOST", "0.0.0.0")
    port: int = int(os.getenv("TONICUISC_PORT", "8000"))
    #: 对外地址，用来在启动横幅里打印登录链接，例如 https://106-35-196-104.nip.io
    public_url: str = os.getenv("TONICUISC_PUBLIC_URL", "").strip()
    reload: bool = os.getenv("TONICUISC_RELOAD", "0") in {"1", "true", "yes"}
    sources: list[str] = field(default_factory=lambda: _env_list("TONICUISC_SOURCES", ["migu", "kuwo"]))
    #: 每个音源的抓取下限（真正抓多少按「这次要几条 ÷ 音源数」分摊）
    search_size: int = int(os.getenv("TONICUISC_SEARCH_SIZE", "5"))
    #: 每个请求取几首（下限）：实际每页条数 = max(这个值, ceil(要几条 / 并发数))
    search_size_per_page: int = int(os.getenv("TONICUISC_SEARCH_SIZE_PER_PAGE", "1"))
    #: 每个音源同时并发多少个请求（musicdl 每个请求内部串行解析，所以并发数=并行度）
    search_threads: int = int(os.getenv("TONICUISC_SEARCH_THREADS", "5"))
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
    #: /api/covers 最多等正在跑的封面查询多久（秒）；等不到就先给音源原图
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


SETTINGS = Settings()
