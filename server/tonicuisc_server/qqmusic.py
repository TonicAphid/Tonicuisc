"""用 QQ 音乐补封面。

咪咕 / 酷我给的封面经常是小图或者糊的，QQ 音乐的专辑封面更"正统"。
这里只查封面：用「歌名 + 歌手」搜一下 QQ 音乐，拿 albummid 拼出 500x500 的图。

查不到就返回 None，绝不抛异常影响正常搜索。
"""

from __future__ import annotations

import json
import re
from typing import Any

import requests

SEARCH_URL = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
COVER_TEMPLATE = "https://y.gtimg.cn/music/photo_new/T002R500x500M000{}.jpg"

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
    return want_singers in got_singers or got_singers in want_singers


def search_cover(title: str, artists: str = "", timeout: float = 6.0) -> str | None:
    """按歌名 + 歌手找 QQ 音乐的专辑封面，找不到或对不上返回 None。"""
    title, artists = (title or "").strip(), (artists or "").strip()
    keyword = " ".join(part for part in (title, artists) if part)
    if not keyword:
        return None
    try:
        resp = requests.get(
            SEARCH_URL,
            params={"w": keyword, "format": "json", "n": 3, "p": 1, "cr": 1, "t": 0, "aggr": 1, "catZhida": 1},
            headers=HEADERS,
            timeout=timeout,
        )
        resp.raise_for_status()
        payload = _parse_json(resp.text)
    except Exception:
        return None

    songs = (((payload.get("data") or {}).get("song") or {}).get("list")) or []
    for song in songs[:3]:
        if not isinstance(song, dict) or not _is_same_song(song, title, artists):
            continue
        album_mid = str(song.get("albummid") or "").strip()
        if album_mid:
            return COVER_TEMPLATE.format(album_mid)
    return None
