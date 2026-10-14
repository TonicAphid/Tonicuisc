"""``python -m tonicuisc_server`` 入口。

服务端的数据都在 Redis 里，启动时会自动把本地 Redis 拉起来（找不到就自动下载，
见 ``storage/redis_runtime.py``），退出时再把它关掉。

管理命令（不需要 API Key，在服务器本机跑）：

    python -m tonicuisc_server devices            列出设备和账户
    python -m tonicuisc_server revoke <device_id> 吊销某台设备
    python -m tonicuisc_server set-password <user> 重置某个账户的密码（不需要旧密码）
    python -m tonicuisc_server recovery-code <user> 生成新的恢复码（忘密码时用它重置）
    python -m tonicuisc_server cleanup            清理无账户 / 已吊销的设备记录
    python -m tonicuisc_server redis              看一眼 Redis 状态（会按需拉起）
    python -m tonicuisc_server clear-cache        清掉搜索结果缓存
    python -m tonicuisc_server cert <域名或IP>      生成自签证书（内网 HTTPS）

服务启动后，如果 stdin 是控制台，底部会一直留一个 ``>`` 提示符（像 MC 服务端那样），
``h`` 看帮助、``q`` 停服务。实现都在 ``tonicuisc_server.cli`` 里。
"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    main()
