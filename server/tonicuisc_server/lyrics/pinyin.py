"""中文歌词的拼音（作为附加行，不替代原文）。

KRC 自带的 ``type 0`` 只有部分歌曲有，中文歌经常是空的；这里用 ``pypinyin``
统一补一档。没安装 ``pypinyin`` 时安静跳过，不影响逐字歌词本身。

**加拼音的门槛比「含汉字」严得多**，否则满屏噪音：

* **开头的制作名单整块跳过**（``credit_count``，由 :func:`~.clean.credit_prefix_length`
  按结构算出来）：那些行不是唱出来的，注音纯属噪音；
* 单行的「标签：内容」（``词：周杰伦`` / ``承制：某某``）也跳过，兜住没被整块圈进来的；
* 一行至少 2 个汉字（``啊`` / ``哦`` 这种单字行不加）；
* 汉字要占这行「有效字符」（字母 / 汉字 / 假名，不算空格标点）的 40% 以上——
  英语歌里夹一句 ``I love you 我爱你`` 不该整行注音；
* 日文行（含假名）不加：``pypinyin`` 给汉字读的是**中文**读音，日语歌注出来全是错的，
  那种歌该用 KRC 自带的 romaji。
"""

from __future__ import annotations

import re
from dataclasses import replace

from .clean import is_credit_line
from .models import LyricsLine

try:  # 可选依赖：requirements.txt 里有，但不是播放歌词的硬依赖
    from pypinyin import Style, lazy_pinyin
except ImportError:  # pragma: no cover - 环境没装时走这里
    Style = None  # type: ignore[assignment]
    lazy_pinyin = None  # type: ignore[assignment]

#: 汉字（含扩展 A 区）
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
#: 假名：出现就说明这是日文行，拼音只会注错
_KANA_RE = re.compile(r"[\u3040-\u30ff]")
#: 「有效字符」：字母 / 汉字 / 假名 / 韩文（空格、标点、数字不算）
_VISIBLE_RE = re.compile(r"[0-9A-Za-z\u3040-\u30ff\uac00-\ud7af\u3400-\u4dbf\u4e00-\u9fff]")

#: 一行至少几个汉字才值得注音
MIN_CJK = 2
#: 汉字占有效字符的比例下限（0.4：``Baby 我爱你`` 注，``I love you 我爱你`` 不注）
MIN_RATIO = 0.4


def should_pinyin(text: str) -> bool:
    """这一行该不该加拼音（导出给测试 / 排查用）。"""
    text = (text or "").strip()
    if not text or is_credit_line(text):
        return False
    if _KANA_RE.search(text):
        return False
    cjk = len(_CJK_RE.findall(text))
    if cjk < MIN_CJK:
        return False
    visible = len(_VISIBLE_RE.findall(text))
    return visible > 0 and cjk / visible >= MIN_RATIO


def fill_pinyin(lines: tuple[LyricsLine, ...], credit_count: int = 0) -> tuple[LyricsLine, ...]:
    """给「确实是歌词的中文行」补拼音；已有 roman（如日语 romaji）的行不动。

    ``credit_count`` 是开头有多少行是制作名单（:func:`~.clean.credit_prefix_length`），
    这些行**保留原文，但不注音**。
    """
    out: list[LyricsLine] = []
    for index, line in enumerate(lines):
        if index < credit_count or is_credit_line(line.text):
            out.append(_without_roman(line))
            continue
        if lazy_pinyin is None or line.roman or not should_pinyin(line.text):
            out.append(line)
            continue
        words = []
        for word in line.words:
            # 逐字只注汉字；夹在中间的英文单词保持原样（不注成字母读音）
            words.append(replace(word, roman=_pinyin(word.text)) if _CJK_RE.search(word.text) else word)
        out.append(replace(line, words=tuple(words), roman=_pinyin(line.text)))
    return tuple(out)


def _without_roman(line: LyricsLine) -> LyricsLine:
    """去掉这一行（含逐字）的 roman；本来就没有就原样返回，别制造新对象。"""
    if not line.roman and not any(word.roman for word in line.words):
        return line
    return replace(line, roman="", words=tuple(replace(word, roman="") for word in line.words))


def _pinyin(text: str) -> str:
    if lazy_pinyin is None:
        return ""
    return " ".join(lazy_pinyin(text, style=Style.TONE)).strip()
