"""歌词候选匹配：歌名 / 歌手 / 时长的启发式打分。

拿不到音乐平台的内部 ID 时只能靠元信息匹配；宁可返回 None，也不要配错歌。
"""

from __future__ import annotations

import html
import re
from difflib import SequenceMatcher

_TAG_RE = re.compile(r"<[^>]+>")
_PUNCT_RE = re.compile(r"[\s\-_–—·:：()（）\[\]【】,，.。!！?？'\"“”‘’/&+]+")


def clean_text(value: str) -> str:
    """去掉平台返回的高亮标签（``<em>``）和 HTML 实体。"""
    return html.unescape(_TAG_RE.sub("", value or "")).strip()


def normalize(value: str) -> str:
    return _PUNCT_RE.sub("", clean_text(value).lower())


def similarity(left: str, right: str) -> float:
    a, b = normalize(left), normalize(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def artist_match(want: str, got: str) -> float:
    """歌手匹配：完全相等 / 互相包含得满分，否则退化成字符串相似度。"""
    a, b = normalize(want), normalize(got)
    if not a or not b:
        return 0.0
    if a == b or a in b or b in a:
        return 1.0
    return similarity(a, b)


def score(
    *,
    title: str,
    artists: str,
    duration_s: float | None,
    cand_title: str,
    cand_artists: str,
    cand_duration_s: float | None,
) -> float:
    """0~1 的匹配分；低于调用方的阈值就当没匹配上。"""
    title_score = similarity(title, cand_title)
    artist_score = artist_match(artists, cand_artists)
    total = title_score * 0.6 + artist_score * 0.3
    if duration_s and cand_duration_s:
        diff = abs(float(duration_s) - float(cand_duration_s))
        # 差 0 秒加 0.1，差 10 秒及以上不加分
        total += max(0.0, 0.1 * (1 - diff / 10))
    return round(min(total, 1.0), 4)


def duration_seconds(value: object) -> float | None:
    """平台字段有秒也有毫秒，统一成秒。"""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number / 1000 if number > 10_000 else number
