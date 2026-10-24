"""歌词源适配器接口。

每个 provider 只负责「给我一首歌的元信息，还我逐字歌词」，缓存/降级/并发由
:class:`~tonicuisc_server.lyrics.service.LyricService` 统一处理。
"""

from __future__ import annotations

from typing import Any, Protocol

from ..models import EnhancedLyrics


class LyricsProvider(Protocol):
    name: str

    def fetch(self, song: Any) -> EnhancedLyrics | None:
        """匹配并抓取逐字歌词；没有匹配/没有逐字数据时返回 None。"""
        ...


from .kugou import KugouProvider  # noqa: E402  (放最后避免循环导入)

__all__ = ["LyricsProvider", "KugouProvider"]
