"""歌词文本清理（不涉及网络/存储）。"""

from __future__ import annotations

from .matcher import normalize
from .models import LyricsLine


def strip_header(lines: tuple[LyricsLine, ...], title: str, artists: str) -> tuple[LyricsLine, ...]:
    """去掉开头「歌名 - 歌手」这类头部行。

    KRC 的第一行经常是 ``[0,7340]晴天 - 周杰伦 (Jay Chou)``，它不是歌词。
    只处理最前面 3 行，且要求这一行同时像歌名/歌手、长度也短，避免误删正文。
    """
    keep = list(lines)
    want_title, want_artist = normalize(title), normalize(artists)
    if not want_title and not want_artist:
        return lines
    for _ in range(3):
        if not keep:
            break
        text = normalize(keep[0].text)
        if not text or len(text) > 60:
            break
        hit_title = bool(want_title) and want_title in text
        hit_artist = bool(want_artist) and want_artist in text
        if not (hit_title or hit_artist):
            break
        keep.pop(0)
    return tuple(keep)
