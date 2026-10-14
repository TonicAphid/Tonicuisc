"""文本清洗与歌手拆分（纯函数，不碰网络和存储）。"""

from __future__ import annotations

import re
from typing import Any

#: 歌手分隔符：「蒋雪儿/王力宏」「周杰伦、方文山」都认
ARTIST_SPLIT_RE = re.compile(r"[/,，、&；;|]+")


def clean(value: Any, default: str = "") -> str:
    """把 None / 'NULL' 之类的脏值洗成空串。"""
    text = "" if value is None else str(value).strip()
    return default if text.upper() in {"NULL", "NONE"} else text


def split_artists(text: str) -> list[str]:
    """把「蒋雪儿/王力宏」这种人名串拆成单个歌手（都转小写）。"""
    return [part.strip().lower() for part in ARTIST_SPLIT_RE.split(text or "") if part.strip()]
