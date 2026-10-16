"""本机管理命令（不需要 API Key）。

能跑这些命令的人本来就能改 Redis / 证书目录，所以它们不做额外鉴权。

    python -m tonicuisc_server devices            列出设备和账户
    python -m tonicuisc_server revoke <device_id> 吊销某台设备
    python -m tonicuisc_server set-password <user> 重置密码（不需要旧密码）
    python -m tonicuisc_server recovery-code <user> 生成新的恢复码
    python -m tonicuisc_server cleanup            清理无账户 / 已吊销的设备记录
    python -m tonicuisc_server redis              看一眼 Redis 状态
    python -m tonicuisc_server clear-cache        清掉搜索结果缓存
    python -m tonicuisc_server cert <域名或IP>      生成自签证书（内网 HTTPS 用）
"""

from __future__ import annotations

import getpass
import sys
import time
from pathlib import Path

from ..auth import AuthManager
from ..core.config import SETTINGS
from ..core.tls import cert_paths, generate_self_signed
from ..storage import Storage, redis_runtime

#: 能直接从命令行派发的命令
MANAGEMENT_COMMANDS = (
    "devices",
    "list",
    "revoke",
    "set-password",
    "recovery-code",
    "cleanup",
    "redis",
    "clear-cache",
    "cert",
    "acme",
    "env",
    "caddy",
    "ddns",
)

#: 需要参数的命令 → 用法串
_USAGE = {
    "revoke": "revoke <device_id>",
    "set-password": "set-password <username>",
    "recovery-code": "recovery-code <username>",
    "cert": "cert <域名或IP>",
    "acme": "acme [--status|--issue|--force|--staging]",
    "env": "env [uisc|tuisc]        看 / 改当前环境",
    "caddy": "caddy [--download|--generate|--validate|--start|--status|--log]",
    "ddns": "ddns [set-key|check|run|install|status|remove]",
}

#: ``--help`` 用的命令清单：``(命令, 一句话说明)``。
#: **加新命令时一定要往这里补一行**——以前只在 ``__main__.py`` 的文档字符串里列，
#: 结果 env / caddy / ddns / acme 四个命令压根没出现过，用户不知道有这些功能。
COMMAND_HELP: tuple[tuple[str, str], ...] = (
    ("devices", "列出设备和账户（简写 list）"),
    ("revoke <设备ID>", "吊销某台设备（ID 可只输前几位，唯一就行）"),
    ("set-password <用户>", "重置密码（不需要旧密码，交互输两次新的）"),
    ("recovery-code <用户>", "生成恢复码（旧的作废，明文只显示一次）"),
    ("cleanup", "清理无主 / 已吊销的设备记录"),
    ("redis", "看 Redis 状态（会按需拉起）"),
    ("clear-cache", "清掉搜索结果缓存"),
    ("env [uisc|tuisc]", "看 / 改当前环境（决定域名、横幅、DDNS 目标）"),
    ("caddy [选项]", "Caddy：--status 看状态、--download 下载、--generate 生成配置、"),
    ("", "        --validate 校验、--start 启动、--log 看日志"),
    ("acme [选项]", "Let's Encrypt 证书（DNS-01，不需要入站端口）："),
    ("", "        --status 看状态、--issue 签发、--force 强制重签"),
    ("", "        --staging 用测试环境（不占生产限额）"),
    ("ddns [子命令]", "阿里云解析：set-key 填 AK、check 只读验证、run 立刻更新、"),
    ("", "        install 装定时任务（--only v6 只发 AAAA）、status、remove"),
    ("cert <域名或IP>", "生成**自签**证书（内网 HTTPS，浏览器会提示不受信任）"),
)


def print_commands(stream=None) -> None:
    """打印命令总览（``--help`` / ``-h`` 用）。"""
    out = stream or sys.stdout
    print("用法：python -m tonicuisc_server [命令] [参数]", file=out)
    print("不带命令 = 启动服务（会自动拉起 Redis，Caddy 看 TONICUISC_CADDY_AUTOSTART）", file=out)
    print(file=out)
    print("命令：", file=out)
    for name, help_text in COMMAND_HELP:
        print(f"  {name:<22}{help_text}", file=out)
    print(file=out)
    print("服务起来后，控制台底部有个 > 提示符：h 看命令、q 停服务。", file=out)


#: 当成「请求帮助」的参数
HELP_FLAGS = ("--help", "-h", "help", "?")


def format_time(value: float | None) -> str:
    if not value:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value))


def devices_command(store) -> int:
    devices = store.list_devices(own_only=False)  # 本机管理命令：看全库，不按账户过滤
    if not devices:
        print("还没有已登录设备")
    else:
        print(f"{'设备 ID':<34}{'名称':<16}{'账户':<14}{'状态':<8}最后使用")
        for device in devices:
            state = "已吊销" if device.get("revoked") else "正常"
            name = str(device.get("name") or "-")[:14]
            user = str(device.get("username") or "-")[:12]
            print(f"{device['id']:<34}{name:<16}{user:<14}{state:<8}{format_time(device.get('last_seen'))}")

    users = store.list_users()
    if users:
        print("\n账户：")
        for user in users:
            print(
                f"  {user['username']:<16}创建于 {format_time(user.get('created_at'))}"
                f"  最后登录 {format_time(user.get('last_login_at'))}"
            )
    return 0


def revoke_command(store, device_id: str) -> int:
    """吊销设备；允许输唯一前缀——设备 ID 很长，控制台上敲全太费劲。"""
    devices = store.list_devices(own_only=False)
    hits = [d for d in devices if str(d.get("id")) == device_id]
    if not hits:
        hits = [d for d in devices if str(d.get("id")).startswith(device_id)]
    if not hits:
        print("没有这个设备")
        return 1
    if len(hits) > 1:
        print(f"前缀 {device_id} 匹配到 {len(hits)} 台设备，输长一点：")
        for device in hits:
            print(f"  {device['id']}")
        return 1
    store.revoke_device(hits[0]["id"])
    print("已吊销该设备")
    return 0


def account_command(store, mode: str, username: str) -> int:
    """``set-password`` / ``recovery-code``：忘密码时的兜底入口。"""
    auth = AuthManager(store)
    if mode == "recovery-code":
        code = auth.issue_recovery_code(username)
        if code is None:
            print(f"没有这个账户: {username}")
            return 1
        print(f"账户 {username} 的新恢复码（只显示这一次，抄下来；旧的已作废）：\n\n  {code}\n")
        print("以后忘了密码，就在 /login 页面点「忘记密码」用它重置。")
        return 0

    if store.find_user(username) is None:
        print(f"没有这个账户: {username}")
        return 1
    new = getpass.getpass("新密码（至少 6 位）: ")
    if len(new) < 6:
        print("密码太短，至少 6 位")
        return 1
    if new != getpass.getpass("再输一次: "):
        print("两次输入不一致，没有改")
        return 1
    if auth.set_password(username, new) is None:
        print("重置失败了")
        return 1
    print(f"已重置 {username} 的密码（恢复码没动，已登录的设备不用重新登录）")
    return 0


def cleanup_command(store) -> int:
    # 只删「没有账户」和「已吊销」的记录，正常设备不动
    orphans = store.delete_devices(without_user=True)
    revoked = store.delete_devices(revoked=True)
    expired = store.drop_expired_device_requests()
    print(f"已删除无账户设备 {orphans} 条、已吊销设备 {revoked} 条、过期登录请求 {expired} 条")
    return 0


def clear_cache_command(store) -> int:
    removed = store.drop_search_cache()
    print(f"已清掉 {removed} 条搜索结果缓存")
    return 0


def redis_command() -> int:
    client = redis_runtime.ensure_redis_client()
    info = client.info() if hasattr(client, "info") else {}
    version = info.get("redis_version") if isinstance(info, dict) else None
    print(f"Redis 地址：{SETTINGS.redis_config()}")
    print(f"版本：{version or '未知'}")
    print(f"是否由本进程拉起：{'是（本进程退出时会自动关闭）' if redis_runtime.RUNTIME.started_by_us else '否（用现成的）'}")
    if redis_runtime.RUNTIME.server_path:
        print(f"redis-server：{redis_runtime.RUNTIME.server_path}")
    print(f"数据目录：{SETTINGS.redis_data_dir}")
    return 0


def cert_command(name: str, out_dir: Path | None = None) -> int:
    """生成自签证书，并把要设的环境变量直接打出来。"""
    name = (name or "").strip()
    if not name:
        print(f"用法: python -m tonicuisc_server {_USAGE['cert']}")
        return 2
    out_dir = out_dir or (SETTINGS.cache_dir / "tls")
    cert_path, key_path = cert_paths(out_dir, name)
    try:
        tool = generate_self_signed(name, cert_path, key_path)
    except Exception as exc:  # 缺依赖 / openssl 报错，都别甩栈给用户
        print(f"生成证书失败：{type(exc).__name__}: {exc}")
        print("装一个 cryptography 再试：pip install cryptography")
        return 1
    print(f"已用 {tool} 生成自签证书（{DEFAULT_DAYS_HINT} 天）：")
    print(f"  证书：{cert_path}")
    print(f"  私钥：{key_path}")
    print("\n在 server/.env 里加两行就能开 HTTPS：")
    print(f"  TONICUISC_TLS_CERT={cert_path}")
    print(f"  TONICUISC_TLS_KEY={key_path}")
    print("\n手机 / 浏览器会提示「不受信任」：把上面这张证书装到设备上并手动信任，")
    print("或者干脆用公网域名 + Caddy 自动签（见 deploy/Caddyfile）。")
    return 0


#: 自签有效期，和 core.tls.DEFAULT_DAYS 一致（这里只用于提示文案）
DEFAULT_DAYS_HINT = 825


def acme_command(args: list[str]) -> int:
    """用 Let's Encrypt 签证书（DNS-01 / 阿里云解析，**不需要入站 80/443**）。

    Caddy 自带的 ACME 只会 HTTP-01 / TLS-ALPN-01，都要 Let's Encrypt 能从公网连进来；
    大陆家用宽带常封 80/443，所以这里改用 DNS-01：只要能往解析里写 TXT 记录就行。
    """
    from ..core import acme
    from ..core.config import ENVIRONMENT

    action = (args[0] if args else "--status").strip().lower()
    domain = ENVIRONMENT.domain

    # 参数解析：**选项可能出现在任意位置**，所以两边都扫。
    # 踩过两次：`--issue --force` 里 --force 在 args[1]，只看 args[0] 会漏；
    # 而把所有 - 开头的都当选项筛掉又会把 --issue 本身也筛没。
    flags = {arg.strip().lower() for arg in args}
    force = "--force" in flags
    staging = "--staging" in flags

    if "--status" in flags:
        action = "status"
    elif "--issue" in flags or "--force" in flags:
        action = "issue"
    else:
        # 允许不带横线的写法：acme issue / acme force
        words = [arg.strip().lower() for arg in args if not arg.startswith("-")]
        action = words[0] if words else "status"
        force = force or action == "force"
        staging = staging or "staging" in words
        if action == "force":
            action = "issue"

    if action in {"status"} and not force:
        crt, key = acme.cert_paths(domain)
        print(f"环境    ：{ENVIRONMENT.key}（{ENVIRONMENT.label}）")
        print(f"域名    ：{domain}")
        print(f"证书    ：{crt}（{'存在' if crt.exists() else '还没有'}）")
        if crt.exists():
            remaining = acme.cert_expires_in_days(crt)
            print(f"剩余    ：{remaining} 天" if remaining is not None else "剩余    ：读不出来")
            print(f"要续期  ：{'是' if acme.needs_renewal(domain) else '否'}")
        path = acme.find_lego(auto_download=False)
        print(f"lego    ：{path or '没找到（--issue 时会自动下载）'}")
        if path:
            print(f"版本    ：{acme.lego_version(path) or '（拿不到）'}")
        print(f"AK      ：{'已配置' if acme._aliyun_env() else '没配（先跑 ddns set-key）'}")
        return 0

    if action in {"issue"}:
        ca = acme.LE_STAGING if staging else ""
        if staging:
            print("注意：用的是 Let's Encrypt **测试环境**（浏览器会不信任，只用来验证流程）")
        try:
            crt, key = acme.obtain(domain, force=force, ca=ca)
        except Exception as exc:
            print(f"签证书失败：{exc}")
            return 1
        print(f"证书：{crt}")
        print(f"私钥：{key}")
        print("\nCaddy 会自动用上它（重新生成配置即可）：")
        print("  python -m tonicuisc_server caddy --generate")
        return 0

    print(_USAGE["acme"])
    print("  --status   看证书 / lego / AK 的状态（默认）")
    print("  --issue    签发（已有效则跳过；DNS-01，不需要入站端口）")
    print("  --force    强制重签（可和 --staging 一起用来先试流程）")
    print("  --staging  用 Let's Encrypt 测试环境（不占生产限额）")
    return 2


def env_command(args: list[str]) -> int:
    """看 / 改当前环境（uisc 正式、tuisc 测试）。

    环境决定了对外域名、启动横幅、DDNS 要更新的记录。**选过就不再问**——
    启动时突然弹问题很烦，所以重选走这个命令（或 ``env --ask`` 现场问一遍）。
    """
    from ..core.environment import ENVIRONMENTS, ENV_PATH, apply_environment, get_environment

    if not args or args[0] in {"--show", "show"}:
        current = get_environment()
        print(f"当前环境：{current.key}  ——  {current.label}")
        print(f"对外地址：{current.public_url}")
        print(f"配置文件：{ENV_PATH}")
        print()
        print("可选环境：")
        for key, env in ENVIRONMENTS.items():
            mark = " ← 当前" if key == current.key else ""
            print(f"  {key:<6} {env.label:<16} {env.public_url}{mark}")
        print()
        print("切换：python -m tonicuisc_server env tuisc     （写进 .env，重启生效）")
        print("现场问一遍：python -m tonicuisc_server env --ask")
        return 0

    # --ask：把启动时那个询问单独拿出来，方便随时重选
    if args[0] in {"--ask", "ask"}:
        from .launch import choose_environment
        from ..core.environment import ENV_KEY

        # 清掉当前值，否则 choose_environment 会认为「已经选过」直接返回
        import os

        os.environ.pop(ENV_KEY, None)
        if not choose_environment(interactive=True):
            print("没有做出选择，环境保持不变。")
            return 0
        print("重启服务后生效。")
        return 0

    wanted = args[0].strip().lower()
    if wanted not in ENVIRONMENTS:
        print(f"不认识的环境：{wanted}（可选：{', '.join(ENVIRONMENTS)}）")
        return 2

    environment = ENVIRONMENTS[wanted]
    path = apply_environment(environment, persist=True)
    print(f"已切换到：{environment.key}  ——  {environment.label}")
    print(f"对外地址：{environment.public_url}")
    if path:
        print(f"已写入：{path}")
    print("\n重启服务后生效。")
    return 0


def caddy_command(args: list[str]) -> int:
    """Caddy 的定位 / 下载 / 校验 / 启动。"""
    from ..core import caddy as caddy_runtime

    action = (args[0] if args else "--status").strip().lower()

    if action in {"--status", "status"}:
        from ..core.config import ENVIRONMENT

        path = caddy_runtime.find_caddy(auto_download=False)
        print(f"环境     ：{ENVIRONMENT.key}（{ENVIRONMENT.label}）")
        if path is None:
            print("caddy    ：没找到。可以：")
            print(f"  · python -m tonicuisc_server caddy --download   （自动下到 {caddy_runtime.bin_dir()}）")
            print(f"  · 手动下载：{caddy_runtime.DOWNLOAD_PAGE}")
            print(f"  · 或把路径写进 .env：TONICUISC_CADDY_SERVER=<完整路径>")
            return 1
        version = caddy_runtime.caddy_version(path)
        print(f"caddy    ：{path}")
        print(f"版本     ：{version or '（跑 --version 没拿到）'}")
        print(f"配置     ：{SETTINGS.caddy_config}（{'存在' if SETTINGS.caddy_config.exists() else '还没生成'}）")
        print(f"自动启动 ：{'开' if SETTINGS.caddy_autostart else '关'}（TONICUISC_CADDY_AUTOSTART）")
        return 0

    if action in {"--download", "download"}:
        try:
            path = caddy_runtime.find_caddy(auto_download=True)
        except RuntimeError as exc:
            print(exc)
            return 1
        if path is None:
            print("下载被关掉了（TONICUISC_CADDY_DOWNLOAD=0），而且本地也没有。")
            return 1
        print(f"已就绪：{path}")
        return 0

    if action in {"--generate", "generate"}:
        try:
            config = caddy_runtime.generate_config(force=True)
        except RuntimeError as exc:
            print(exc)
            return 1
        print(f"已生成：{config}")
        return 0

    if action in {"--validate", "validate"}:
        path = caddy_runtime.find_caddy(auto_download=False)
        if path is None:
            print("没找到 caddy，先跑 caddy --download")
            return 1
        try:
            config = caddy_runtime.generate_config()
        except RuntimeError as exc:
            print(exc)
            return 1
        ok, detail = caddy_runtime.validate_config(path, config)
        print(("配置有效" if ok else "配置有问题") + f"：{detail}（{config}）")
        return 0 if ok else 1

    if action in {"--log", "log"}:
        path = caddy_runtime.log_path()
        if not path.exists():
            print(f"还没有日志：{path}")
            return 1
        print(f"=== {path}（最后 40 行）===")
        print(caddy_runtime.tail_log(40))
        return 0

    if action in {"--start", "start"}:
        # 管理端点是全机唯一的：已经有 Caddy 在跑就别再起一个（会 bind 2019 失败）
        if caddy_runtime.admin_alive():
            print("已经有一个 Caddy 在跑了（管理端点 127.0.0.1:2019 有响应）。")
            print("要用新配置的话：先停掉那个（Ctrl+C / taskkill /IM caddy.exe），再重新启动。")
            return 1
        # `--start` 是**前台**启动，人就在盯着这个窗口，所以默认把日志显示出来；
        # 想让这个窗口也安静就加 --quiet（日志照样会写文件）。
        quiet = "--quiet" in {a.strip().lower() for a in args}
        path = caddy_runtime.find_caddy(auto_download=True)
        if path is None:
            print("没找到 caddy，先跑 caddy --download")
            return 1
        try:
            config = caddy_runtime.generate_config()
            ok, detail = caddy_runtime.validate_config(path, config)
            if not ok:
                print(f"Caddyfile 有问题，先修一下：{detail}")
                return 1
            process = caddy_runtime.start_caddy(path, config, console=not quiet)
        except RuntimeError as exc:
            print(exc)
            return 1
        print(f"Caddy 已在后台运行（pid {process.pid}）。停止：Ctrl+C 或结束该进程。")
        try:
            process.wait()
        except KeyboardInterrupt:
            print("\n正在停止 Caddy…")
            process.terminate()
        return 0

    print(_USAGE["caddy"])
    print("  --status    看环境、caddy 路径、版本、配置（默认）")
    print("  --download  下载（Windows zip / Linux tar.gz，走 gh-proxy 镜像）")
    print("  --generate  按当前环境生成 Caddyfile")
    print("  --validate  生成 + 校验 Caddyfile 语法")
    print("  --start     生成 + 校验 + 前台启动 Caddy（日志直接显示；加 --quiet 只写文件）")
    print("  --log       看 Caddy 运行时日志的最后几行")
    return 2


def ddns_command(args: list[str]) -> int:
    """DDNS：set-key / check / run / install / status / remove（跨平台）。"""
    from ..core import ddns

    action = (args[0] if args else "status").strip().lower()
    rest = args[1:]

    if action in {"set-key", "setkey", "key"}:
        return ddns.set_key(rest)
    if action == "check":
        return ddns.check(rest)
    if action == "run":
        return ddns.run_once(rest)
    if action == "install":
        return ddns.install(rest)
    if action == "remove":
        return ddns.remove()
    if action == "status":
        return ddns.status()
    print(_USAGE["ddns"])
    print("  set-key      填阿里云 RAM 的 AccessKey（交互输入，写完当场验证）")
    print("  check        只读：看这个域名现有记录（验证 AK 与权限）")
    print("  run [--only v4|v6]   立刻更新一次（默认刷新 A / AAAA）")
    print("  install [--every 10m] [--record x.cn] [--only v6]   装定时任务")
    print("  status       看装了没 / 上次结果")
    print("  remove       卸掉定时任务")
    return 2


def run_management_command(name: str, args: list[str]) -> int:
    """跑一条管理命令并返回退出码（自己管 Storage 与托管 Redis 的生命周期）。"""
    if name not in MANAGEMENT_COMMANDS:
        print(f"未知命令: {name}")
        return 2
    # 只有**必须**带参数的命令才在这里拦；env / caddy / ddns 不带参数时各有默认动作
    if name in {"revoke", "set-password", "recovery-code", "cert"} and not args:
        print(f"用法: python -m tonicuisc_server {_USAGE[name]}")
        return 2
    if name == "cert":
        return cert_command(args[0])
    # 注意：args 里已经**不含**命令名本身了，别再切一刀
    if name == "acme":
        return acme_command(args)
    if name == "env":
        return env_command(args)
    if name == "caddy":
        return caddy_command(args)
    if name == "ddns":
        return ddns_command(args)

    store = Storage()
    try:
        if name in {"devices", "list"}:
            return devices_command(store)
        if name == "revoke":
            return revoke_command(store, args[0])
        if name == "redis":
            return redis_command()
        if name == "clear-cache":
            return clear_cache_command(store)
        if name == "cleanup":
            return cleanup_command(store)
        if name in {"set-password", "recovery-code"}:
            return account_command(store, name, args[0])
    finally:
        store.close()
        # 管理命令跑完就把我们拉起来的 Redis 收掉
        redis_runtime.shutdown_redis()
    return 2
