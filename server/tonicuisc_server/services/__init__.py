"""业务层：音源、搜索、下载、封面，统一由 :class:`MusicService` 组装。

对外只暴露这一个门面类（外加几个领域错误 / 工具），``api`` 层不许直接碰 mixin。
"""

from __future__ import annotations

from ..core.constants import (  # noqa: F401  —— 历史 import 路径的兼容出口
    AUDIO_SUFFIXES,
    ITEM_TTL_SECONDS,
    SEARCH_RESIDUE_MAX_AGE,
    USER_AGENT,
)
from ..core.errors import DownloadFailed, SongNotFound  # noqa: F401
from ..core.text import clean  # noqa: F401
from .service import MusicService
from .song_item import song_to_item

__all__ = [
    "MusicService",
    "SongNotFound",
    "DownloadFailed",
    "song_to_item",
    "clean",
    "ITEM_TTL_SECONDS",
    "SEARCH_RESIDUE_MAX_AGE",
    "AUDIO_SUFFIXES",
    "USER_AGENT",
]
