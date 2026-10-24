"""酷狗音乐：搜索 → 找 KRC 歌词 → 下载 → 解密解析。

用到的公开接口：

* ``songsearch.kugou.com/song_search_v2`` 搜歌（返回 FileHash / 时长）
* ``krcs.kugou.com/search`` 用 FileHash 找 KRC 歌词候选
* ``lyrics.kugou.com/download`` 下载 KRC（base64）

KRC 只是 XOR + zlib，见 :mod:`tonicuisc_server.lyrics.krc`。
"""

from __future__ import annotations

import base64
from typing import Any

import requests

from ...core.logging import timing_log
from ..clean import strip_header
from ..krc import decode_krc, parse_krc
from ..matcher import clean_text, duration_seconds, score
from ..models import EnhancedLyrics
from ..pinyin import fill_pinyin

SEARCH_URL = "https://songsearch.kugou.com/song_search_v2"
KRC_SEARCH_URL = "https://krcs.kugou.com/search"
DOWNLOAD_URL = "https://lyrics.kugou.com/download"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.kugou.com/",
}


class KugouProvider:
    name = "kugou"

    def __init__(self, *, timeout: float = 6.0, min_score: float = 0.6) -> None:
        self.timeout = timeout
        self.min_score = min_score

    # ------------------------------------------------------------------ 对外
    def fetch(self, song: Any) -> EnhancedLyrics | None:
        title = clean_text(str(getattr(song, "song_name", "") or getattr(song, "name", "") or ""))
        artists = clean_text(str(getattr(song, "singers", "") or ""))
        duration = duration_seconds(getattr(song, "duration_s", None))
        if not title:
            return None

        found = self._search_song(title, artists, duration)
        if found is None:
            return None
        file_hash, matched_title, matched_artists, matched_duration = found

        candidate = self._find_krc(file_hash, matched_title, matched_artists, matched_duration)
        if candidate is None:
            return None

        raw = self._download(candidate["id"], candidate["accesskey"])
        if raw is None:
            return None
        text = decode_krc(raw)
        lines = fill_pinyin(strip_header(parse_krc(text), title, artists))
        if not lines:
            return None
        timing_log(f"[lyric] 酷狗命中「{matched_title} - {matched_artists}」{len(lines)} 行")
        return EnhancedLyrics(provider=self.name, lines=lines)

    # ------------------------------------------------------------------ 搜索
    def _search_song(
        self, title: str, artists: str, duration: float | None
    ) -> tuple[str, str, str, float | None] | None:
        payload = self._get_json(
            SEARCH_URL,
            {
                "keyword": f"{title} {artists}".strip(),
                "page": 1,
                "pagesize": 10,
                "platform": "WebFilter",
                "tag": "em",
                "filter": 2,
                "iscorrection": 1,
                "privilege_filter": 0,
            },
        )
        songs = (payload.get("data") or {}).get("lists") or []
        best: tuple[float, dict[str, Any]] | None = None
        for item in songs:
            if not isinstance(item, dict) or not item.get("FileHash"):
                continue
            cand_title = clean_text(str(item.get("SongName") or ""))
            cand_artists = clean_text(str(item.get("SingerName") or ""))
            cand_duration = duration_seconds(item.get("Duration"))
            value = score(
                title=title,
                artists=artists,
                duration_s=duration,
                cand_title=cand_title,
                cand_artists=cand_artists,
                cand_duration_s=cand_duration,
            )
            if best is None or value > best[0]:
                best = (value, item)
        if best is None or best[0] < self.min_score:
            return None
        item = best[1]
        cand_duration = duration_seconds(item.get("Duration"))
        if duration and cand_duration and abs(duration - cand_duration) > 8:
            return None
        return (
            str(item["FileHash"]),
            clean_text(str(item.get("SongName") or "")),
            clean_text(str(item.get("SingerName") or "")),
            cand_duration,
        )

    def _find_krc(
        self, file_hash: str, title: str, artists: str, duration: float | None
    ) -> dict[str, Any] | None:
        payload = self._get_json(
            KRC_SEARCH_URL,
            {
                "ver": 1,
                "man": "yes",
                "client": "mobi",
                "keyword": "",
                "duration": int(duration or 0),
                "hash": file_hash,
            },
        )
        candidates = payload.get("candidates") or []
        best: tuple[float, dict[str, Any]] | None = None
        for item in candidates:
            if not isinstance(item, dict) or not item.get("id") or not item.get("accesskey"):
                continue
            cand_title = clean_text(str(item.get("song") or ""))
            cand_artists = clean_text(str(item.get("singer") or ""))
            value = score(
                title=title,
                artists=artists,
                duration_s=duration,
                cand_title=cand_title,
                cand_artists=cand_artists,
                cand_duration_s=duration_seconds(item.get("duration")),
            )
            if best is None or value > best[0]:
                best = (value, item)
        if best is None:
            return None
        return best[1]

    def _download(self, lyric_id: str, access_key: str) -> bytes | None:
        payload = self._get_json(
            DOWNLOAD_URL,
            {"ver": 1, "client": "pc", "id": lyric_id, "accesskey": access_key, "fmt": "krc", "charset": "utf8"},
        )
        content = payload.get("content")
        if not content:
            return None
        try:
            return base64.b64decode(content)
        except (ValueError, TypeError):
            return None

    # ------------------------------------------------------------------ HTTP
    def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        response = requests.get(url, params=params, headers=HEADERS, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}
