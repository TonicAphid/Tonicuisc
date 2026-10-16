"""``python -m tonicuisc_server`` 入口。

服务端的数据都在 Redis 里，启动时会自动把本地 Redis 拉起来（找不到就自动下载，
见 ``storage/redis_runtime.py``），退出时再把它关掉。
默认还会顺手把 Caddy 也拉起来（``TONICUISC_CADDY_AUTOSTART=0`` 可关）。

**不带参数 = 启动服务**；``--help`` 看命令总览。管理命令都在本机跑、不需要 API Key：

    设备 / 账户
      devices                列出设备和账户（简写 list）
      revoke <device_id>     吊销某台设备
      set-password <user>    重置某个账户的密码（不需要旧密码）
      recovery-code <user>   生成新的恢复码（忘密码时用它重置）
      cleanup                清理无账户 / 已吊销的设备记录

    运行时
      redis                  看一眼 Redis 状态（会按需拉起）
      clear-cache            清掉搜索结果缓存

    部署 / HTTPS（详见 README）
      env [uisc|tuisc]       看 / 改当前环境（决定域名、横幅、DDNS 目标）
      caddy [选项]           Caddy：--status / --download / --generate / --validate
                             / --start / --log
      acme [选项]            Let's Encrypt 证书（**DNS-01，不需要入站端口**）：
                             --status / --issue / --force / --staging
      ddns [子命令]          阿里云解析：set-key / check / run / install / status / remove
      cert <域名或IP>        生成**自签**证书（内网 HTTPS，浏览器会提示不受信任）

命令清单的**唯一出处**是 ``cli/commands.py`` 里的 ``COMMAND_HELP``——加新命令时改那里，
``--help`` 会跟着更新（以前只在本文件里手写列表，结果 env / caddy / ddns / acme 四个
命令压根没出现过）。

服务启动后，如果 stdin 是控制台，底部会一直留一个 ``>`` 提示符（像 MC 服务端那样），
``h`` 看帮助、``q`` 停服务。实现都在 ``tonicuisc_server.cli`` 里。
"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    main()
