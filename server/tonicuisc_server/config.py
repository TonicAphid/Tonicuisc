"""Runtime settings for the Tonicuisc server.

Everything is read from environment variables so the same image can be reused
for local development and for a container deployment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

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
    """Translate short aliases (``migu``) into musicdl class names."""
    resolved: list[str] = []
    for alias in aliases:
        key = alias.strip().lower()
        if key in SOURCE_ALIASES:
            resolved.append(SOURCE_ALIASES[key])
        elif alias in SOURCE_ALIASES.values():
            resolved.append(alias)
    unknown = [a for a in aliases if a.strip().lower() not in SOURCE_ALIASES]
    if unknown:
        raise ValueError(f"unsupported source(s): {', '.join(unknown)}; supported: {', '.join(SOURCE_ALIASES)}")
    return resolved


@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("TONICUISC_HOST", "0.0.0.0")
    port: int = int(os.getenv("TONICUISC_PORT", "8000"))
    reload: bool = os.getenv("TONICUISC_RELOAD", "0") in {"1", "true", "yes"}
    sources: list[str] = field(default_factory=lambda: _env_list("TONICUISC_SOURCES", ["migu", "kuwo"]))
    #: 每个音源要多少首
    search_size: int = int(os.getenv("TONICUISC_SEARCH_SIZE", "10"))
    #: 每个请求取多少首；设成 1 就是「10 首拆成 10 个请求并行拿」
    search_size_per_page: int = int(os.getenv("TONICUISC_SEARCH_SIZE_PER_PAGE", "1"))
    #: 每个音源同时并发多少个请求
    search_threads: int = int(os.getenv("TONICUISC_SEARCH_THREADS", "10"))
    cache_dir: Path = field(
        default_factory=lambda: Path(os.getenv("TONICUISC_CACHE_DIR", SERVER_DIR / ".cache")).resolve()
    )

    @property
    def work_dir(self) -> Path:
        """Where musicdl stores downloaded audio files."""
        return self.cache_dir / "music"

    @property
    def db_path(self) -> Path:
        """SQLite 数据库位置（存搜索结果 / 历史，不含音频本体）。"""
        return Path(os.getenv("TONICUISC_DB", self.cache_dir / "tonicuisc.db")).resolve()


SETTINGS = Settings()
