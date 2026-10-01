"""端到端冒烟测试：搜索 -> Range 流式播放 -> 下载附件。

用法（在 server/ 目录下）：
    python scripts/smoke_api.py [关键词]
"""

from __future__ import annotations

import sys

from fastapi.testclient import TestClient

from tonicuisc_server.main import app

KEYWORD = sys.argv[1] if len(sys.argv) > 1 else "周杰伦"


def main() -> int:
    with TestClient(app) as client:
        print("health:", client.get("/api/health").json())

        search = client.get("/api/search", params={"keyword": KEYWORD, "limit": 3})
        print("search status:", search.status_code)
        if search.status_code != 200:
            print(search.text[:500])
            return 1
        items = search.json()["items"]
        print("results:", [(i["id"], i["name"], i["source_label"], i["file_size"]) for i in items])
        if not items:
            return 1

        song_id = items[0]["id"]

        stream = client.get(f"/api/stream/{song_id}", headers={"Range": "bytes=0-1023"})
        print("stream status:", stream.status_code, "len:", len(stream.content), "range:", stream.headers.get("content-range"))
        assert stream.status_code == 206 and len(stream.content) == 1024, "Range 流式响应不正确"

        resp = client.get(f"/api/download/{song_id}")
        print("download status:", resp.status_code, "len:", len(resp.content), "name:", resp.headers.get("content-disposition"))
        assert resp.status_code == 200 and len(resp.content) > 1024

        print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
