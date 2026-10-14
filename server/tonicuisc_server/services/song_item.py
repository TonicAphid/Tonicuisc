"""musicdl 的 ``SongInfo`` → 接口 item。"""

from __future__ import annotations

from typing import Any

from ..core.config import source_label
from ..core.text import clean


def song_to_item(song: Any) -> dict[str, Any]:
    """把 musicdl 的 SongInfo 变成接口返回的 item。"""
    return {
        "id": f"{song.source}:{song.identifier}",
        "source": song.source,
        "source_label": source_label(str(song.source)),
        "root_source": clean(song.root_source),
        "name": clean(song.song_name, "未知歌曲"),
        "singers": clean(song.singers, "未知歌手"),
        "album": clean(song.album),
        "ext": clean(song.ext).lstrip("."),
        "duration": clean(song.duration),
        "duration_s": song.duration_s,
        "file_size": clean(song.file_size),
        "cover_url": clean(song.cover_url),
        "has_lyric": bool(clean(song.lyric)),
    }
