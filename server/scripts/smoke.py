"""手动冒烟测试：真实调用音源，确认 咪咕 / 酷我 可用。

用法（在 server/ 目录下）：
    python scripts/smoke.py 关键词 [--download]
"""

from __future__ import annotations

import json
import sys

from tonicuisc_server.service import MusicService


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    keyword = args[0] if args else "周杰伦"
    svc = MusicService()
    items = svc.search(keyword, limit=5)
    print(json.dumps(items, ensure_ascii=False, indent=2))
    if not items:
        print("没有搜索结果")
        return 1
    if "--download" in sys.argv:
        path = svc.ensure_file(items[0]["id"])
        print("下载完成:", path, path.stat().st_size, "bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
