"""端到端冒烟测试：搜索 -> 边下边播 -> Range 播放 -> 下载附件。

用法（在 server/ 目录下）：
    python scripts/smoke_api.py [关键词]
"""

from __future__ import annotations

import sys
import time

from fastapi.testclient import TestClient

from tonicuisc_server.config import SETTINGS
from tonicuisc_server.main import app

KEYWORD = sys.argv[1] if len(sys.argv) > 1 else "周杰伦"


def main() -> int:
    with TestClient(app) as client:
        print("health:", client.get("/api/health").json())

        # 只勾选一个音源时，不应该再去请求另一个音源
        only_migu = client.get(
            "/api/search", params={"keyword": KEYWORD, "sources": "migu", "limit": 2, "refresh": True}
        )
        assert only_migu.status_code == 200, only_migu.text
        migu_payload = only_migu.json()
        migu_items = migu_payload["items"]
        print("[sources=migu] elapsed:", migu_payload.get("elapsed"), "items:", [(i["id"], i["source"]) for i in migu_items])
        assert all(item["source"] == "MiguMusicClient" for item in migu_items), "只选咪咕却返回了别的音源"

        search = client.get("/api/search", params={"keyword": KEYWORD, "limit": 3, "refresh": True})
        print("search status:", search.status_code, "elapsed:", search.json().get("elapsed"))
        if search.status_code != 200:
            print(search.text[:500])
            return 1
        items = search.json()["items"]
        print("results:", [(i["id"], i["name"], i["source_label"], i["file_size"]) for i in items])
        if not items:
            return 1

        song_id = items[0]["id"]

        direct = client.get(f"/api/url/{song_id}")
        print("direct url status:", direct.status_code, "host:", direct.json().get("url", "")[:60])
        assert direct.status_code == 200

        # 1) 第一次播放：未缓存，必须很快开始出字节（边下边播），且中断后不留半截文件
        started = time.time()
        with client.stream("GET", f"/api/stream/{song_id}") as stream:
            assert stream.status_code == 200, stream.read()
            first_chunk = next(stream.iter_bytes(), b"")
            first_byte = time.time() - started
            print("first byte after:", round(first_byte, 2), "s, accept-ranges:", stream.headers.get("accept-ranges"))
            assert first_chunk, "没有收到音频数据"
            assert first_byte < 8, "第一次点歌不应该等整首下完"
        leftovers = [p.name for p in SETTINGS.cache_dir.rglob("*.part")]
        assert not leftovers, f"中断后不应残留 .part: {leftovers}"

        # 2) 完整播一次：边下边播的同时把文件写进缓存
        full = client.get(f"/api/stream/{song_id}")
        print("full stream status:", full.status_code, "len:", len(full.content))
        assert full.status_code == 200 and len(full.content) > 1024
        cached = [p.name for p in (SETTINGS.cache_dir / "files").iterdir()]
        print("cache files:", sorted(cached))
        assert cached, "完整播放后应该已缓存"

        # 3) 再播一次：走本地缓存，支持 Range
        ranged = client.get(f"/api/stream/{song_id}", headers={"Range": "bytes=0-1023"})
        print("range status:", ranged.status_code, "len:", len(ranged.content), "range:", ranged.headers.get("content-range"))
        assert ranged.status_code == 206 and len(ranged.content) == 1024

        # 4) 附件下载
        resp = client.get(f"/api/download/{song_id}")
        print("download status:", resp.status_code, "len:", len(resp.content), "name:", resp.headers.get("content-disposition"))
        assert resp.status_code == 200 and len(resp.content) > 1024

        print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
