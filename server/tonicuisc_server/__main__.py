"""``python -m tonicuisc_server`` 启动服务。

顺带两个不用 API Key 的管理命令（在服务器本机跑）：

    python -m tonicuisc_server devices            列出已配对设备
    python -m tonicuisc_server revoke <device_id> 吊销某台设备
"""

from __future__ import annotations

import sys
import time

import uvicorn

from .config import SETTINGS


def _format_time(value: float | None) -> str:
    if not value:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value))


def _devices_command(store) -> int:
    devices = store.list_devices()
    if not devices:
        print("还没有配对设备")
        return 0
    print(f"{'设备 ID':<34}{'名称':<18}{'状态':<8}最后使用")
    for device in devices:
        state = "已吊销" if device.get("revoked") else "正常"
        print(f"{device['id']:<34}{str(device.get('name'))[:16]:<18}{state:<8}{_format_time(device.get('last_seen'))}")
    return 0


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] in {"devices", "list", "revoke"}:
        from .storage import Storage

        store = Storage()
        try:
            if args[0] in {"devices", "list"}:
                raise SystemExit(_devices_command(store))
            if len(args) < 2:
                print("用法: python -m tonicuisc_server revoke <device_id>")
                raise SystemExit(2)
            ok = store.revoke_device(args[1])
            print("已吊销该设备" if ok else "没有这个设备")
            raise SystemExit(0 if ok else 1)
        finally:
            store.close()

    uvicorn.run(
        "tonicuisc_server.main:app",
        host=SETTINGS.host,
        port=SETTINGS.port,
        reload=SETTINGS.reload,
    )


if __name__ == "__main__":
    main()
