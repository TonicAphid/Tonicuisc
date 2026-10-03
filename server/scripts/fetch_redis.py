"""手动预下载 redis-server（不想让「首次启动」卡在下载上，或者要给离线机器备货）。

用法（在 server/ 目录下，跟其它 scripts 一样）：

    python scripts/fetch_redis.py                  # 给当前平台下好，放进 redis/win 或 redis/linux
    python scripts/fetch_redis.py --force          # 已经有了也重新下一遍
    python scripts/fetch_redis.py --platform win   # 指定平台目录（zip 解压是纯 Python，跨平台可用）
    python scripts/fetch_redis.py --check          # 只检查下载地址通不通，什么都不下

这个脚本**不会**启动 Redis，也不会碰 `.cache/redis` 里的数据；它只负责把可执行文件
放到 `redis/<平台>/`，剩下的交给服务端启动时的 `redis_runtime`。
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
import urllib.request
from pathlib import Path

# 允许从 server/ 目录直接 `python scripts/fetch_redis.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tonicuisc_server.config import SETTINGS  # noqa: E402
from tonicuisc_server.redis_runtime import (  # noqa: E402
    LINUX_DEB_URL,
    LINUX_SOURCE_URL,
    WINDOWS_ZIP_URL,
    RedisRuntime,
)

#: 每个平台的目标文件名（放好了就算「已就绪」）
TARGET_NAMES = {"win": "redis-server.exe", "linux": "redis-server"}
#: 当前系统能直接下的平台
NATIVE_PLATFORM = "win" if sys.platform.startswith("win") else "linux"


class _PlatformRuntime(RedisRuntime):
    """把 ``redis_bin_dir`` 指到指定平台的目录，方便跨平台预下载。"""

    def __init__(self, settings, platform: str) -> None:
        super().__init__(settings)
        self._platform = platform

    @property
    def bin_dir(self) -> Path:
        return self._settings.redis_dir / self._platform

    def _download_server(self) -> Path:
        bin_dir = self.bin_dir
        bin_dir.mkdir(parents=True, exist_ok=True)
        if self._platform == "win":
            return self._download_windows(bin_dir)
        return self._download_linux(bin_dir)


def _head(url: str) -> str:
    """HEAD 一下，返回一行人能看懂的结果（用来验下载地址还有效）。"""
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "tonicuisc-redis-fetch/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            size = response.headers.get("Content-Length")
            human = f"{int(size) / 1048576:.1f} MB" if size and size.isdigit() else "大小未知"
            return f"OK  HTTP {response.status}  {human}"
    except Exception as exc:  # noqa: BLE001 - 这里就是要报告所有失败
        return f"失败  {type(exc).__name__}: {exc}"


def _check() -> int:
    """检查代码里写死的三个下载地址（换过地址以后记得跑一次）。"""
    print("检查 redis-server 下载地址：\n")
    ok = True
    for label, url in (
        ("Windows zip (Redis 5.0.14.1)", WINDOWS_ZIP_URL),
        ("Linux deb   (Redis 7.2.5)", LINUX_DEB_URL),
        ("Linux 源码  (Redis 7.2.5)", LINUX_SOURCE_URL),
    ):
        result = _head(url)
        if not result.startswith("OK"):
            ok = False
        print(f"  {label}\n    {url}\n    -> {result}\n")
    print("结论：" + ("三个地址都可用" if ok else "有地址不可用，请更新 redis_runtime.py 顶部的常量"))
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把 redis-server 预先下到 redis/win 或 redis/linux（不启动 Redis）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--platform", choices=sorted(TARGET_NAMES), default=NATIVE_PLATFORM,
                        help=f"下到哪个平台目录（默认：当前系统 {NATIVE_PLATFORM}）")
    parser.add_argument("--force", action="store_true", help="已经存在也重新下载覆盖")
    parser.add_argument("--check", action="store_true", help="只检查下载地址是否可用，不下载")
    args = parser.parse_args()

    if args.check:
        return _check()

    settings = dataclasses.replace(SETTINGS)
    runtime = _PlatformRuntime(settings, args.platform)
    target = runtime.bin_dir / TARGET_NAMES[args.platform]

    if target.is_file() and not args.force:
        print(f"已经有了，跳过下载：{target}")
        print("（想重新下一遍加 --force）")
        return 0

    if args.platform != NATIVE_PLATFORM and args.platform == "linux":
        print("提示：给 Linux 备货最好直接在 Linux 机器上跑（解 deb / 编译源码都要 Linux 工具）。\n")

    print(f"目标：{target}")
    try:
        path = runtime._download_server()  # noqa: SLF001 - 脚本就是来调这个内部流程的
    except RuntimeError as exc:
        print(f"\n下载失败：\n{exc}", file=sys.stderr)
        return 1

    print(f"\n完成：{path}")
    print(f"大小：{path.stat().st_size / 1048576:.1f} MB")
    print("这些二进制不要提交进仓库，.gitignore 规则见 redis/README.md 第六节。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
