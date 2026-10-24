"""中文歌词的拼音（作为附加行，不替代原文）。

KRC 自带的 ``type 0`` 只有部分歌曲有，中文歌经常是空的；这里用 ``pypinyin``
统一补一档。没安装 ``pypinyin`` 时安静跳过，不影响逐字歌词本身。
"""

from __future__ import annotations

import re
from dataclasses import replace

from .models import LyricsLine

try:  # 可选依赖：requirements.txt 里有，但不是播放歌词的硬依赖
    from pypinyin import Style, lazy_pinyin
except ImportError:  # pragma: no cover - 环境没装时走这里
    Style = None  # type: ignore[assignment]
    lazy_pinyin = None  # type: ignore[assignment]

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def fill_pinyin(lines: tuple[LyricsLine, ...]) -> tuple[LyricsLine, ...]:
    """给没有罗马音的中文行补拼音；已有 roman（如日语 romaji）的行不动。"""
    if lazy_pinyin is None:
        return lines
    out: list[LyricsLine] = []
    for line in lines:
        if line.roman or not _CJK_RE.search(line.text):
            out.append(line)
            continue
        words = []
        for word in line.words:
            if _CJK_RE.search(word.text):
                words.append(replace(word, roman=_pinyin(word.text)))
            else:
                words.append(word)
        out.append(replace(line, words=tuple(words), roman=_pinyin(line.text)))
    return tuple(out)


def _pinyin(text: str) -> str:
    if lazy_pinyin is None:
        return ""
    return " ".join(lazy_pinyin(text, style=Style.TONE)).strip()
