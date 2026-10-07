"""把旧版 SQLite 里的数据一次性搬到 Redis。

用法（在 ``server/`` 目录下）：

    python scripts/import_sqlite.py                 # 默认搬 .cache/tonicuisc.db
    python scripts/import_sqlite.py --from D:\\old\\tonicuisc.db
    python scripts/import_sqlite.py --dry-run       # 只统计，不写 Redis
    python scripts/import_sqlite.py --force       # 已经导入过也继续（默认跳过非空库）

搬到 Redis 的内容：歌曲元信息、账户、设备、搜索历史、收藏/喜欢/播放历史。
设备码登录的中间状态（device_requests）只有 10 分钟寿命，不搬。

导入是**幂等**的：同一个 id/用户名再导一次只是覆盖，不会产生重复数据。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tonicuisc_server.config import SETTINGS  # noqa: E402
from tonicuisc_server.storage import MAX_SEARCH_HISTORY, Storage  # noqa: E402


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row["name"] for row in rows}


def _import_songs(conn: sqlite3.Connection, store: Storage, dry: bool) -> int:
    if "songs" not in _tables(conn):
        return 0
    rows = conn.execute("SELECT * FROM songs").fetchall()
    if dry:
        return len(rows)
    pipe = store.client.pipeline(transaction=False)
    for row in rows:
        item_id = str(row["id"])
        mapping: dict[str, Any] = {
            "id": item_id,
            "source": str(row["source"] or ""),
            "source_label": str(row["source_label"] or ""),
            "root_source": str(row["root_source"] or ""),
            "name": str(row["name"] or ""),
            "singers": str(row["singers"] or ""),
            "album": str(row["album"] or ""),
            "ext": str(row["ext"] or ""),
            "duration": str(row["duration"] or ""),
            "duration_s": "" if row["duration_s"] is None else str(row["duration_s"]),
            "file_size": str(row["file_size"] or ""),
            "cover_url": str(row["cover_url"] or ""),
            "has_lyric": "1" if row["has_lyric"] else "0",
            "payload": str(row["payload"]),
            "url_expire_at": repr(float(row["url_expire_at"] or 0)),
            "first_seen": repr(float(row["first_seen"] or 0)),
            "last_seen": repr(float(row["last_seen"] or 0)),
        }
        pipe.hset(store.key("song", item_id), mapping=mapping)
        pipe.zadd(store.key("songs"), {item_id: float(row["last_seen"] or 0)})
        # qq_cover：NULL = 还没查过（不写 key），'' = 查过但没有
        if row["qq_cover"] is not None:
            pipe.set(store.key("qcover", item_id), str(row["qq_cover"]))
    pipe.execute()
    return len(rows)


def _import_users(conn: sqlite3.Connection, store: Storage, dry: bool) -> int:
    if "users" not in _tables(conn):
        return 0
    rows = conn.execute("SELECT * FROM users").fetchall()
    if dry:
        return len(rows)
    pipe = store.client.pipeline(transaction=False)
    for row in rows:
        user_id = str(row["id"])
        username = str(row["username"])
        pipe.hset(
            store.key("user", user_id),
            mapping={
                "id": user_id,
                "username": username,
                "password_hash": str(row["password_hash"]),
                "created_at": repr(float(row["created_at"] or 0)),
                "last_login_at": "" if row["last_login_at"] is None else repr(float(row["last_login_at"])),
            },
        )
        pipe.set(store.key("username", username.lower()), user_id)
        pipe.zadd(store.key("users"), {user_id: float(row["created_at"] or 0)})
    pipe.execute()
    return len(rows)


def _import_devices(conn: sqlite3.Connection, store: Storage, dry: bool) -> int:
    if "devices" not in _tables(conn):
        return 0
    rows = conn.execute("SELECT * FROM devices").fetchall()
    if dry:
        return len(rows)
    pipe = store.client.pipeline(transaction=False)
    for row in rows:
        device_id = str(row["id"])
        key_hash = str(row["key_hash"])
        pipe.hset(
            store.key("device", device_id),
            mapping={
                "id": device_id,
                "name": str(row["name"] or ""),
                "key_hash": key_hash,
                "created_at": repr(float(row["created_at"] or 0)),
                "last_seen": "" if row["last_seen"] is None else repr(float(row["last_seen"])),
                "revoked": "1" if row["revoked"] else "0",
                "user_id": str(row["user_id"] or ""),
            },
        )
        pipe.set(store.key("devicehash", key_hash), device_id)
        pipe.zadd(store.key("devices"), {device_id: float(row["created_at"] or 0)})
    pipe.execute()
    return len(rows)


def _import_library(conn: sqlite3.Connection, store: Storage, dry: bool) -> int:
    if "library" not in _tables(conn):
        return 0
    rows = conn.execute("SELECT * FROM library").fetchall()
    if dry:
        return len(rows)
    pipe = store.client.pipeline(transaction=False)
    for row in rows:
        user_id, kind, song_id = str(row["user_id"]), str(row["kind"]), str(row["song_id"])
        updated = float(row["updated_at"] or 0)
        pipe.zadd(store.key("lib", user_id, kind), {song_id: updated})
        pipe.hset(
            store.key("libmeta", user_id, kind),
            song_id,
            json.dumps(
                {
                    "created_at": float(row["created_at"] or 0),
                    "play_count": int(row["play_count"] or 1),
                }
            ),
        )
    pipe.execute()
    return len(rows)


def _import_searches(conn: sqlite3.Connection, store: Storage, dry: bool) -> int:
    """搜索历史：直接沿用原来的 search id 当序号，顺序和次数都不变。"""
    tables = _tables(conn)
    if "searches" not in tables:
        return 0
    rows = conn.execute("SELECT * FROM searches ORDER BY id").fetchall()
    if dry:
        return len(rows)
    songs: dict[int, list[str]] = {}
    if "search_results" in tables:
        for row in conn.execute("SELECT search_id, song_id FROM search_results ORDER BY search_id, position"):
            songs.setdefault(int(row["search_id"]), []).append(str(row["song_id"]))

    pipe = store.client.pipeline(transaction=False)
    # 重建「最近搜索」列表和每条记录的结果列表，重复导入才不会把次数和歌单叠加
    pipe.delete(store.key("searchrecent"))
    newest_first: list[str] = []
    for row in rows:
        seq = int(row["id"])
        search_key = store.key("search", seq)
        song_ids = songs.get(seq) or []
        pipe.delete(f"{search_key}:songs")
        pipe.hset(
            search_key,
            mapping={
                "keyword": str(row["keyword"]),
                "sources": str(row["sources"] or ""),
                "result_count": str(int(row["result_count"] or 0)),
                "created_at": repr(float(row["created_at"] or 0)),
            },
        )
        if song_ids:
            pipe.rpush(f"{search_key}:songs", *song_ids)
        newest_first.append(str(seq))

    # 只保留最近 MAX_SEARCH_HISTORY 条
    for seq in newest_first[-MAX_SEARCH_HISTORY:]:
        pipe.lpush(store.key("searchrecent"), seq)
    pipe.set(store.key("searchseq"), newest_first[-1] if newest_first else "0")
    pipe.execute()
    return len(rows)


def _import_search_cache(conn: sqlite3.Connection, store: Storage, dry: bool) -> int:
    """把老库里的「搜索结果」直接写成搜索缓存，这样 App 再搜这些词是秒回的。

    老版本没有落盘的搜索缓存（只在内存里 10 分钟），能还原的只有 ``search_results``
    里记着的那份结果列表。同一个关键词只保留**最新**一次（按 id 从小到大写，后写的覆盖），
    和线上 ``search`` 的缓存 key 保持一致：``(keyword.lower(), tuple(sorted(sources)))``。
    """
    tables = _tables(conn)
    if "searches" not in tables or "search_results" not in tables:
        return 0
    rows = conn.execute("SELECT * FROM searches ORDER BY id").fetchall()
    if not rows:
        return 0
    songs: dict[int, list[str]] = {}
    for row in conn.execute("SELECT search_id, song_id FROM search_results ORDER BY search_id, position"):
        songs.setdefault(int(row["search_id"]), []).append(str(row["song_id"]))

    written = 0
    for row in rows:
        seq, keyword = int(row["id"]), str(row["keyword"])
        sources = [part for part in str(row["sources"] or "").split(",") if part]
        song_ids = songs.get(seq) or []
        if not keyword or not song_ids:
            continue
        if dry:
            written += 1
            continue
        items = []
        for song_id in song_ids:
            item = _item_from_redis(store, song_id)
            if item is not None:
                items.append(item)
        if not items:
            continue
        store.save_search_cache((keyword.lower(), tuple(sorted(sources))), items)
        written += 1
    return written


def _item_from_redis(store: Storage, song_id: str) -> dict[str, Any] | None:
    """用 Redis 里的歌曲元信息拼回 ``/api/search`` 的 item（搜索缓存的格式）。"""
    data = store.client.hgetall(store.key("song", song_id))
    if not data:
        return None
    cover = store.client.get(store.key("qcover", song_id)) or data.get("cover_url") or ""
    duration_s = data.get("duration_s")
    return {
        "id": song_id,
        "source": data.get("source") or song_id.split(":", 1)[0],
        "source_label": data.get("source_label") or "",
        "root_source": data.get("root_source") or "",
        "name": data.get("name") or "未知歌曲",
        "singers": data.get("singers") or "未知歌手",
        "album": data.get("album") or "",
        "ext": data.get("ext") or "",
        "duration": data.get("duration") or "",
        "duration_s": int(duration_s) if str(duration_s or "").isdigit() else None,
        "file_size": data.get("file_size") or "",
        "cover_url": cover,
        "has_lyric": str(data.get("has_lyric") or "0") == "1",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="旧 SQLite → Redis 一次性迁移")
    parser.add_argument("--from", dest="source", default=str(SETTINGS.db_path), help="旧 SQLite 文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只统计条数，不写 Redis")
    parser.add_argument("--force", action="store_true", help="Redis 里已经有数据也照搬（默认会问一下）")
    args = parser.parse_args()

    path = Path(args.source).expanduser().resolve()
    if not path.is_file():
        print(f"找不到 SQLite 文件：{path}")
        return 1
    print(f"[import] SQLite: {path}")

    store = Storage()
    print(f"[import] Redis : {store.describe()}")

    existing = store.count_songs() + len(store.list_users())
    if existing and not args.dry_run and not args.force:
        answer = input(f"Redis 里已经有数据（{existing} 条歌曲/账户），还要继续导入吗？[y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("已取消")
            return 1

    started = time.perf_counter()
    conn = _connect(path)
    try:
        counts = {
            "songs": _import_songs(conn, store, args.dry_run),
            "users": _import_users(conn, store, args.dry_run),
            "devices": _import_devices(conn, store, args.dry_run),
            "library": _import_library(conn, store, args.dry_run),
            "searches": _import_searches(conn, store, args.dry_run),
            "cache": _import_search_cache(conn, store, args.dry_run),
        }
    finally:
        conn.close()

    label = "将导入" if args.dry_run else "已导入"
    print(
        f"[import] {label} 歌曲 {counts['songs']} 首、账户 {counts['users']} 个、"
        f"设备 {counts['devices']} 台、收藏/历史 {counts['library']} 条、搜索记录 {counts['searches']} 条"
        f"（{time.perf_counter() - started:.2f}s）"
    )
    print(f"[import] 顺带还原了 {counts['cache']} 个关键词的搜索缓存（App 再搜这些词直接命中，不联网）")
    if not args.dry_run:
        print("[import] Redis 里的条数：", store.count_songs(), "首歌 /", store.recent_searches(5))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
