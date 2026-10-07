"""用 QQ 音乐补封面。

咪咕 / 酷我给的封面经常是小图或者糊的，QQ 音乐的专辑封面更"正统"。
这里只查封面：用「歌名 + 歌手」搜一下 QQ 音乐，拿 albummid 拼出 500x500 的图。

两层提速：

* **LRU 记忆**：``(歌名, 歌手)`` → albummid。同一首歌、同一批歌重复查直接命中，不再联网；
* **命中 albummid 就直接拼 URL**，一次搜索搞定，不用再发第二次请求。

网络失败**不写缓存**（下次还会重试），只有「确实没有」才会被记住：

* 查不到 / 对不上 → 返回 ``None``，调用方据此写 ``''``（查过、没有）；
* 网络失败 / 接口报错 → 抛 :class:`CoverLookupError`，调用方**不写缓存**，
  下次还会重试。要是把失败也记成「查过没有」，这首歌就永远只剩糊的原图了。
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

import requests

from .config import SETTINGS

SEARCH_URL = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
COVER_TEMPLATE = "https://y.gtimg.cn/music/photo_new/T002R500x500M000{}.jpg"


class CoverLookupError(RuntimeError):
    """查 QQ 封面时网络/接口出错（区别于「确实没有这首歌的封面」）。"""

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://y.qq.com/",
    "Accept": "application/json, text/plain, */*",
}

_NORMALIZE_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")


def _normalize(text: str) -> str:
    """只留字母数字和汉字，方便做「像不像同一首」的判断。"""
    return _NORMALIZE_RE.sub("", (text or "").lower())


def cover_url_for_album(album_mid: str | None) -> str | None:
    """有了 albummid 就直接拼封面地址，不需要再请求一次。"""
    mid = str(album_mid or "").strip()
    return COVER_TEMPLATE.format(mid) if mid else None


def _parse_json(text: str) -> dict[str, Any]:
    """QQ 的接口有时候返回 JSONP，包一层处理。"""
    body = (text or "").strip()
    if not body.startswith("{"):
        start, end = body.find("("), body.rfind(")")
        if start != -1 and end > start:
            body = body[start + 1 : end]
    try:
        data = json.loads(body)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _is_same_song(song: dict[str, Any], title: str, artists: str) -> bool:
    """QQ 搜不到时会返回「最接近」的结果，得自己判断是不是同一首。

    歌名要**相等**，或者短的那个（至少 4 个字）被长的包含；
    另外歌手也得对得上——不然搜「不存在的歌名xyzabc」会被 XY&Z 这种短名字骗到。
    """
    got_title = _normalize(str(song.get("songname") or ""))
    got_singers = _normalize(
        "".join(str(singer.get("name") or "") for singer in (song.get("singer") or []) if isinstance(singer, dict))
    )
    want_title, want_singers = _normalize(title), _normalize(artists)

    if not want_title or not got_title:
        return False
    if want_title == got_title:
        title_ok = True
    else:
        shorter, longer = sorted((want_title, got_title), key=len)
        title_ok = len(shorter) >= 4 and shorter in longer
    if not title_ok:
        return False

    if not want_singers:
        return True  # 没给歌手，只能靠歌名
    if not got_singers:
        return False  # 我给了歌手、QQ 结果却没歌手字段：对不上，不能放行
    return want_singers in got_singers or got_singers in want_singers


@lru_cache(maxsize=SETTINGS.qq_cover_cache_size)
def _album_mid(title: str, artists: str, timeout: float = 6.0) -> str | None:
    """查一次 QQ 拿 albummid。网络异常直接往外抛，让调用方决定不缓存。"""
    keyword = " ".join(part for part in (title, artists) if part)
    resp = requests.get(
        SEARCH_URL,
        params={"w": keyword, "format": "json", "n": 3, "p": 1, "cr": 1, "t": 0, "aggr": 1, "catZhida": 1},
        headers=HEADERS,
        timeout=timeout,
    )
    resp.raise_for_status()
    payload = _parse_json(resp.text)

    songs = (((payload.get("data") or {}).get("song") or {}).get("list")) or []
    for song in songs[:3]:
        if not isinstance(song, dict) or not _is_same_song(song, title, artists):
            continue
        mid = str(song.get("albummid") or "").strip()
        if mid:
            return mid
    return None  # 确实没有 → 会被记住


def search_cover(title: str, artists: str = "", timeout: float = 6.0) -> str | None:
    """按歌名 + 歌手找 QQ 音乐的专辑封面。

    找不到或对不上返回 ``None``（可以放心记成「查过、没有」）；
    网络/接口失败抛 :class:`CoverLookupError`（**不许**记成「没有」）。
    """
    title, artists = (title or "").strip(), (artists or "").strip()
    if not title and not artists:
        return None
    try:
        mid = _album_mid(title, artists, timeout)
    except Exception as exc:
        raise CoverLookupError(f"QQ 封面查询失败: {exc}") from exc
    return cover_url_for_album(mid)


def cache_info() -> dict[str, int]:
    """给测速 / 排查用：LRU 命中情况。"""
    info = _album_mid.cache_info()
    return {"hits": info.hits, "misses": info.misses, "size": info.currsize}


def clear_cache() -> None:
    _album_mid.cache_clear()
