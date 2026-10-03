"""验证「重启后还能播」：数据全部来自 Redis，不依赖内存缓存。

用法（在 server/ 目录下，先跑过 smoke_api.py 生成数据）：
    python scripts/smoke_db.py
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tonicuisc_server.main import app, get_service
from tonicuisc_server.storage import Storage


def main() -> int:
    store = Storage()
    print("redis:", store.describe())
    print("songs in redis:", store.count_songs())
    print("history:", [(row["keyword"], row["times"]) for row in store.recent_searches(5)])

    song_ids = store.recent_song_ids(3)
    print("recent song ids:", song_ids)
    if not song_ids:
        print("Redis 是空的，先跑一次 scripts/smoke_api.py")
        return 1

    song_id = song_ids[0]
    service = get_service()
    assert not service._songs, "内存缓存应该是空的（模拟刚重启）"
    for candidate in song_ids:  # 优先挑已经下载过、本地有文件的
        if service.cached_file(candidate) is not None:
            song_id = candidate
            break
    song = service.get_song(song_id)  # 只会走 Redis
    print("restored from redis:", song_id, "->", getattr(song, "song_name", None), getattr(song, "ext", None))

    with TestClient(app) as client:
        lyric = client.get(f"/api/lyric/{song_id}")
        print("lyric status:", lyric.status_code, "head:", lyric.text[:30].replace("\n", " "))
        stream = client.get(f"/api/stream/{song_id}", headers={"Range": "bytes=0-1023"})
        print("stream status:", stream.status_code, "len:", len(stream.content), "range:", stream.headers.get("content-range"))
        assert stream.status_code == 206 and len(stream.content) == 1024
        direct = client.get(f"/api/url/{song_id}")
        print("direct url status:", direct.status_code)

    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
