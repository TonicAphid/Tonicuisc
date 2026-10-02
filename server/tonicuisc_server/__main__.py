"""``python -m tonicuisc_server`` 启动服务。

顺带几个不用 API Key 的管理命令（在服务器本机跑）：

    python -m tonicuisc_server devices            列出设备和账户
    python -m tonicuisc_server revoke <device_id> 吊销某台设备
    python -m tonicuisc_server cleanup            清理无账户/已吊销的设备记录
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
        print("还没有已登录设备")
        return 0
    print(f"{'设备 ID':<34}{'名称':<16}{'账户':<14}{'状态':<8}最后使用")
    for device in devices:
        state = "已吊销" if device.get("revoked") else "正常"
        name = str(device.get("name") or "-")[:14]
        user = str(device.get("username") or "-")[:12]
        print(f"{device['id']:<34}{name:<16}{user:<14}{state:<8}{_format_time(device.get('last_seen'))}")

    users = store.list_users()
    if users:
        print("\n账户：")
        for user in users:
            print(f"  {user['username']:<16}创建于 {_format_time(user.get('created_at'))}"
                  f"  最后登录 {_format_time(user.get('last_login_at'))}")
    return 0


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] in {"devices", "list", "revoke", "cleanup"}:
        from .storage import Storage

        store = Storage()
        try:
            if args[0] in {"devices", "list"}:
                raise SystemExit(_devices_command(store))
            if args[0] == "cleanup":
                # 只删「没有账户」和「已吊销」的记录，正常设备不动
                orphans = store.delete_devices(without_user=True)
                revoked = store.delete_devices(revoked=True)
                expired = store.drop_expired_device_requests()
                print(f"已删除无账户设备 {orphans} 条、已吊销设备 {revoked} 条、过期登录请求 {expired} 条")
                raise SystemExit(0)
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
