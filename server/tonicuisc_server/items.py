"""通用小工具：清洗字段、拼歌曲条目、计时日志。

不做任何网络或存储操作，纯函数，谁都能 import。
"""

from __future__ import annotations

import re
from typing import Any

from .config import source_label

#: 搜索结果在内存里保持可用的时间（/api/stream/{id} 靠它）
ITEM_TTL_SECONDS = 3 * 60 * 60

#: 搜索残留目录（只有 *.pkl）至少闲置这么久才清理，避免打断正在进行的下载
SEARCH_RESIDUE_MAX_AGE = 600

#: 这些后缀算音频，清理目录时一律不动
AUDIO_SUFFIXES = {".mp3", ".flac", ".m4a", ".aac", ".wav", ".ogg", ".ape", ".wma", ".wv", ".tta"}

#: 兜底请求用的浏览器 UA
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

ARTIST_SPLIT_RE = re.compile(r"[/,，、&；;|]+")


class SongNotFound(KeyError):
    """歌曲 id 未知或已过期。"""


class DownloadFailed(RuntimeError):
    """musicdl 没能产出可用的音频文件。"""


def clean(value: Any, default: str = "") -> str:
    """把 None / 'NULL' 之类的脏值洗成空串。"""
    text = "" if value is None else str(value).strip()
    return default if text.upper() in {"NULL", "NONE"} else text


def split_artists(text: str) -> list[str]:
    """把「蒋雪儿/王力宏」这种人名串拆成单个歌手（都转小写）。"""
    return [part.strip().lower() for part in ARTIST_SPLIT_RE.split(text or "") if part.strip()]


def timing_log(message: str) -> None:
    """`[timing]` 计时日志。Windows 控制台编码扛不住中文时退化成 ASCII，别把服务搞崩。"""
    for candidate in (message, message.encode("ascii", "replace").decode("ascii")):
        try:
            print(f"[timing] {candidate}", flush=True)
            return
        except UnicodeEncodeError:
            continue


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
