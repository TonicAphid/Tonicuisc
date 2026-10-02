"""SQLite 持久化：搜索结果、歌曲元信息、搜索历史。

只存元信息 + 音源返回的直链（带一个保守的过期时间）。
直链是带签名的临时 token，过期后必须重新搜索，所以这里不假装它是长期有效的。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .config import SETTINGS

#: 音源直链的保守有效期（秒）：超过就重新搜一次刷新
URL_TTL_SECONDS = 30 * 60

#: 搜索历史最多保留多少条
MAX_SEARCH_HISTORY = 500

SCHEMA = """
CREATE TABLE IF NOT EXISTS songs (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    source_label  TEXT,
    root_source   TEXT,
    name          TEXT,
    singers       TEXT,
    album         TEXT,
    ext           TEXT,
    duration      TEXT,
    duration_s    INTEGER,
    file_size     TEXT,
    cover_url     TEXT,
    has_lyric     INTEGER DEFAULT 0,
    payload       TEXT NOT NULL,
    url_expire_at REAL DEFAULT 0,
    first_seen    REAL NOT NULL,
    last_seen     REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_songs_last_seen ON songs(last_seen DESC);

CREATE TABLE IF NOT EXISTS devices (
    id         TEXT PRIMARY KEY,
    name       TEXT,
    key_hash   TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    last_seen  REAL,
    revoked    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS searches (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword      TEXT NOT NULL,
    sources      TEXT NOT NULL,
    result_count INTEGER NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS search_results (
    search_id INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
    song_id   TEXT NOT NULL,
    position  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (search_id, song_id)
);
"""


def song_payload(song: Any) -> dict[str, Any]:
    """把 musicdl 的 SongInfo 变成可 JSON 序列化的 dict。"""
    if hasattr(song, "todict"):
        data = dict(song.todict())
    else:
        data = dict(getattr(song, "__dict__", {}))
    data.pop("episodes", None)  # FM 电台的子集，不入库
    data.pop("downloaded_contents", None)  # 可能是 bytes
    return json.loads(json.dumps(data, ensure_ascii=False, default=str))


class Storage:
    """单文件 SQLite；用一把锁串行化写入，够自建服务用。"""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path or SETTINGS.db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ songs
    def upsert_song(self, item: dict[str, Any], song: Any, url_ttl: float = URL_TTL_SECONDS) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO songs (id, source, source_label, root_source, name, singers, album, ext,
                                   duration, duration_s, file_size, cover_url, has_lyric, payload,
                                   url_expire_at, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source = excluded.source,
                    source_label = excluded.source_label,
                    root_source = excluded.root_source,
                    name = excluded.name,
                    singers = excluded.singers,
                    album = excluded.album,
                    ext = excluded.ext,
                    duration = excluded.duration,
                    duration_s = excluded.duration_s,
                    file_size = excluded.file_size,
                    cover_url = excluded.cover_url,
                    has_lyric = excluded.has_lyric,
                    payload = excluded.payload,
                    url_expire_at = excluded.url_expire_at,
                    last_seen = excluded.last_seen
                """,
                (
                    item["id"],
                    item["source"],
                    item.get("source_label"),
                    item.get("root_source"),
                    item.get("name"),
                    item.get("singers"),
                    item.get("album"),
                    item.get("ext"),
                    item.get("duration"),
                    item.get("duration_s"),
                    item.get("file_size"),
                    item.get("cover_url"),
                    1 if item.get("has_lyric") else 0,
                    json.dumps(song_payload(song), ensure_ascii=False),
                    now + url_ttl,
                    now,
                    now,
                ),
            )
            self._conn.commit()

    def load_song(self, item_id: str) -> dict[str, Any] | None:
        """返回重建播放对象所需的 payload（不含 musicdl 依赖）。"""
        row = self._row(item_id)
        if row is None:
            return None
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        payload.setdefault("identifier", item_id.split(":", 1)[-1])
        payload.setdefault("source", row["source"])
        return payload

    def song_meta(self, item_id: str) -> dict[str, Any] | None:
        row = self._row(item_id)
        return dict(row) if row is not None else None

    def url_expired(self, item_id: str) -> bool:
        row = self._row(item_id)
        return row is None or float(row["url_expire_at"] or 0) < time.time()

    def _row(self, item_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute("SELECT * FROM songs WHERE id = ?", (item_id,)).fetchone()

    def count_songs(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0])

    def prune_songs(self, keep: int = 5000) -> int:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM songs WHERE id NOT IN (SELECT id FROM songs ORDER BY last_seen DESC LIMIT ?)",
                (keep,),
            )
            self._conn.commit()
            return cursor.rowcount

    # ---------------------------------------------------------------- history
    def record_search(self, keyword: str, sources: list[str], song_ids: list[str]) -> None:
        now = time.time()
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO searches (keyword, sources, result_count, created_at) VALUES (?, ?, ?, ?)",
                (keyword, ",".join(sources), len(song_ids), now),
            )
            search_id = cursor.lastrowid
            if song_ids:
                self._conn.executemany(
                    "INSERT OR IGNORE INTO search_results (search_id, song_id, position) VALUES (?, ?, ?)",
                    [(search_id, song_id, index) for index, song_id in enumerate(song_ids)],
                )
            self._conn.execute(
                "DELETE FROM searches WHERE id NOT IN (SELECT id FROM searches ORDER BY id DESC LIMIT ?)",
                (MAX_SEARCH_HISTORY,),
            )
            self._conn.commit()
        # 歌曲表也跟着封顶，避免无限增长
        self.prune_songs()

    def recent_searches(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT keyword, MAX(sources) AS sources, COUNT(*) AS times,
                       MAX(created_at) AS last_searched_at, MAX(result_count) AS result_count
                FROM searches
                GROUP BY keyword
                ORDER BY MAX(id) DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def search_songs(self, search_id: int) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT song_id FROM search_results WHERE search_id = ? ORDER BY position",
                (search_id,),
            ).fetchall()
        return [row["song_id"] for row in rows]

    def recent_song_ids(self, limit: int = 20) -> list[str]:
        """最近搜索到的歌曲 id（最近的排在前面）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT song_id FROM search_results ORDER BY search_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [row["song_id"] for row in rows]

    # ---------------------------------------------------------------- devices
    def create_device(self, name: str, key_hash: str, device_id: str | None = None) -> dict[str, Any]:
        """只存 key 的哈希，明文 key 永远不落库。"""
        device_id = device_id or uuid.uuid4().hex
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO devices (id, name, key_hash, created_at, last_seen, revoked) VALUES (?, ?, ?, ?, ?, 0)",
                (device_id, name, key_hash, now, now),
            )
            self._conn.commit()
        return {"id": device_id, "name": name, "created_at": now, "last_seen": now, "revoked": 0}

    def find_device_by_hash(self, key_hash: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM devices WHERE key_hash = ?", (key_hash,)).fetchone()
        return dict(row) if row is not None else None

    def touch_device(self, device_id: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE devices SET last_seen = ? WHERE id = ?", (time.time(), device_id))
            self._conn.commit()

    def list_devices(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM devices ORDER BY created_at").fetchall()
        return [{k: v for k, v in dict(row).items() if k != "key_hash"} for row in rows]

    def revoke_device(self, device_id: str) -> bool:
        with self._lock:
            cursor = self._conn.execute("UPDATE devices SET revoked = 1 WHERE id = ?", (device_id,))
            self._conn.commit()
            return cursor.rowcount > 0
