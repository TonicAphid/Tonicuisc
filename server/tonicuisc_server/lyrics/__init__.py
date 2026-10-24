"""逐字（增强型）歌词。

按职责拆开，不塞进一个 ``lyric.py``：

* :mod:`.models`    —— 数据模型与序列化
* :mod:`.matcher`   —— 歌名 / 歌手 / 时长匹配
* :mod:`.krc`       —— 酷狗 KRC 解密与解析
* :mod:`.providers` —— 各歌词源适配器（目前酷狗；QQ 后续加）
* :mod:`.service`   —— 缓存、后台抓取、降级编排
"""

from __future__ import annotations

from .models import EnhancedLyrics, LyricsLine, LyricsWord
from .service import LyricService

__all__ = ["EnhancedLyrics", "LyricsLine", "LyricsWord", "LyricService"]
