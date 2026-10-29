"""启动服务：环境选择 → uvicorn 装配 + 原生 TLS + 控制台提示符。"""

from __future__ import annotations

import os
import subprocess
import sys

import uvicorn

from ..core.config import ENVIRONMENT, EXTERNAL_ENV_KEYS, SETTINGS
from ..core.environment import ENV_PATH, ENVIRONMENTS, apply_environment, parse_env_file
from ..core.tls import ssl_kwargs
from .console import attach, detach

#: 控制台 ``restart`` 让子进程用这个退出码退出，外面的守护进程看到它就再拉一次。
#: 选 75（EX_TEMPFAIL）只是习惯，和 0 / 1 / 2 区分开即可。
RESTART_EXIT_CODE = 75

#: 守护进程给子进程打的标记，避免子进程也去当守护进程（套娃）。
SUPERVISED_ENV = "TONICUISC_SUPERVISED"


def console_available() -> bool:
    """stdin 是不是一个真终端——只有这时才有 ``>``，也才需要守护进程。"""
    return sys.stdin is not None and sys.stdin.isatty()


def should_supervise() -> bool:
    """当前进程该不该只当守护进程（真正的服务放到子进程里跑）。

    只在**交互式终端**里这么做：普通 ``python -m tonicuisc_server`` 由 shell 直接
    等一个 PID，守护进程也等同一个 PID，所以 ``restart`` 换掉子进程后 shell 不会
    提前把提示符还回来；systemd / 计划任务 / 管道这些非交互场景照旧原地跑，不引入
    额外进程。

    ``TONICUISC_RELOAD=1``（uvicorn 自己管 reloader）也不套：那层的子进程不是我们
    的，硬套会让 reloader 找不到门。
    """
    if os.environ.get(SUPERVISED_ENV) == "1":
        return False
    if os.getenv("TONICUISC_RELOAD", "").strip().lower() in {"1", "true", "yes"}:
        return False
    return console_available()


def restart_argv() -> list[str]:
    """重新拉起自己时要敲的命令（保留原参数 / 解释器开关）。

    ``sys.orig_argv``（3.10+）连 ``-u``、``-X utf8`` 这些都保留；只有在确认它确实是
    ``-m tonicuisc_server`` 时才用，避免从测试 / 别的入口进来时把 pytest 的命令抄走。
    """
    original = list(getattr(sys, "orig_argv", []) or [])
    if "tonicuisc_server" in original:
        original[0] = sys.executable
        return original
    return [sys.executable, "-m", "tonicuisc_server", *sys.argv[1:]]


def child_env() -> dict[str, str]:
    """给服务子进程的环境变量：保留真正的 shell / systemd 覆盖，摘掉「只来自 .env」的键。

    这一步是改 ``server/.env`` 后 ``restart`` 能生效的关键：父进程启动时已经把旧
    ``.env`` 灌进了自己的 ``os.environ``，如果原样传给子进程，子进程的
    ``load_env_file()`` 会因为「真环境变量优先」而拒绝覆盖——改了文件也白改。
    摘掉之后再让子进程重新读文件，同时不碰用户显式设的外部变量。
    """
    env = dict(os.environ)
    try:
        values = parse_env_file(ENV_PATH.read_text(encoding="utf-8-sig", errors="replace"))
    except OSError:
        values = {}
    for key in values:
        if key not in EXTERNAL_ENV_KEYS:
            env.pop(key, None)
    return env


def supervise() -> None:
    """守护循环：拉起一个服务子进程并等它；子进程要求重启（退出码 75）时再拉一次。

    为什么不在原进程里 ``os.execv``：Windows 上 ``execv`` 是「另起一个进程 + 原进程
    退出」，shell 会立刻拿回提示符，而新服务还在同一个控制台里抢 stdin。这里让原
    进程一直等着，shell 就始终卡在 ``python -m tonicuisc_server`` 这一条命令上；
    也避免了反复重启时 exec 失败 / 端口被抢的竞态。
    """
    argv = restart_argv()

    while True:
        env = child_env()
        env[SUPERVISED_ENV] = "1"  # 每轮都重读 .env，改完文件 restart 才有效
        process = subprocess.Popen(argv, env=env)  # stdin/stdout/stderr 全继承
        try:
            code = process.wait()
        except KeyboardInterrupt:
            # Ctrl+C 是发给整个控制台进程组的，子进程也收到了；别自己先跑，
            # 等它把 uvicorn / Redis / Caddy 收干净。
            while process.poll() is None:
                try:
                    process.wait()
                except KeyboardInterrupt:
                    pass
            code = process.returncode

        if code == RESTART_EXIT_CODE:
            print("[restart] 正在重新启动…", flush=True)
            continue
        raise SystemExit(code)


def choose_environment(*, interactive: bool | None = None) -> bool:
    """启动时问一次「这是哪套环境」，选完写进 ``.env``。

    规则：
    * **已经选过就不再问**（``.env`` / 环境变量里有 ``TONICUISC_ENV``）——
      启动后突然弹一次问题很烦人，而且会打断自动化；
    * 想重选：``python -m tonicuisc_server env tuisc``（确认后写盘，重启生效），
      或者临时覆盖 ``TONICUISC_ENV=tuisc python -m tonicuisc_server``；
    * 非交互场景（systemd / 计划任务 / 管道）自动跳过询问，用上面两种方式指定。

    返回是否真的问了。
    """
    import os

    if os.getenv("TONICUISC_ENV"):
        return False  # 已经选过了
    if interactive is None:
        interactive = sys.stdin is not None and sys.stdin.isatty()
    if not interactive:
        return False

    print()
    print("=" * 56)
    print("  第一次启动：这是哪套环境？")
    for index, (key, env) in enumerate(ENVIRONMENTS.items(), start=1):
        print(f"    {index}) {key:<6} {env.label}   {env.public_url}")
    print("=" * 56)

    keys = list(ENVIRONMENTS)
    default = keys[0]
    while True:
        try:
            answer = input(f"选一个 [1-{len(keys)}，回车={default}]: ").strip().lower()
        except KeyboardInterrupt:
            # Ctrl+C：不选，保持未设置
            print("\n  已取消，这次按默认环境跑；要设就 python -m tonicuisc_server env <uisc|tuisc>")
            return False
        except EOFError:
            # 没有可读的输入（被重定向 / 后台跑 / 无人应答）：
            # 这**不等于**用户按了回车，所以不要替他做决定、更不要写 .env
            print()
            print("  读不到输入（不是交互终端），这次按默认环境跑。")
            print(f"  要设就：python -m tonicuisc_server env <{'|'.join(keys)}>")
            return False
        if not answer:
            chosen = default
            break
        if answer in ENVIRONMENTS:
            chosen = answer
            break
        if answer.isdigit() and 1 <= int(answer) <= len(keys):
            chosen = keys[int(answer) - 1]
            break
        print(f"  认不出来，输 1-{len(keys)} 或者 {', '.join(keys)}")

    environment = ENVIRONMENTS[chosen]
    try:
        path = apply_environment(environment, persist=True)
    except OSError as exc:
        # 写不进 .env（只读目录 / 权限不够）：至少让这次能跑起来
        print(f"  （写入 .env 失败：{exc}，只在本次运行里记住）")
        path = None
    print(f"  已记住：{environment.key}（{environment.public_url}）")
    if path:
        print(f"  写进了 {path}，以后不再问；要改就编辑那个文件")
    print()
    return True


def run_server(*, interactive: bool | None = None) -> None:
    """起服务；stdin 是控制台的话，顺手挂一个 ``>`` 命令提示符。"""
    choose_environment(interactive=interactive)

    # 交互式终端：当前进程只当守护，真正的服务放到子进程里跑。这样控制台的
    # restart 只要让子进程以 75 退出，shell 一直等的那个 PID 不会变，也不会出现
    # 「shell 提示符回来了、服务还在同一个控制台里抢输入」。
    if should_supervise():
        supervise()
        return

    # 环境可能是刚选的。Settings 的默认值是「类定义时」读环境变量的，选完再设 os.environ
    # 不会自动生效，所以这里显式重建一次（否则横幅 / 登录链接会退回 host:port）。
    # api.app 是 uvicorn 启动时才 import 的，能拿到重建后的对象。
    from ..core import config as config_module

    settings = config_module.apply_runtime_env()
    environment = config_module.ENVIRONMENT
    print(f"[env] {environment.key} —— {environment.label} / {environment.public_url}", flush=True)
    print(f"[env] 监听 {settings.host}:{settings.port}", flush=True)
    if settings.host in {"0.0.0.0", "::", "*", ""}:
        # 这种配置下外网能绕过 Caddy 直连服务端（TLS 和鉴权链都跳过了），值得喊一声
        print(
            f"[env] 注意：监听 {settings.host} = 所有网卡，外网可绕过 Caddy 直连 {settings.port}。"
            "只给反代用的话，在 server/.env 里设 TONICUISC_HOST=127.0.0.1",
            flush=True,
        )

    tls = ssl_kwargs(settings)
    if settings.reload:
        # 开发模式：reloader 由 uvicorn 自己另开进程管，控制台的 stop 停不动它，就不挂了
        uvicorn.run("tonicuisc_server.api:app", host=settings.host, port=settings.port, reload=True, **tls)
        return

    from ..core import caddy as caddy_runtime

    # 顺手把 Caddy 也拉起来（TONICUISC_CADDY_AUTOSTART=0 可关）：
    # 家里部署本来就要开两个进程，能少开一个窗口是一个。
    # 起不来**不影响服务端**——多半是 443 被占 / 入站没放行，那些都能边跑边修。
    caddy_process = caddy_runtime.ensure_running() if settings.caddy_autostart else None

    server = uvicorn.Server(
        uvicorn.Config("tonicuisc_server.api:app", host=settings.host, port=settings.port, **tls)
    )
    prompt = attach(server)
    try:
        server.run()
    finally:
        detach(prompt)
        # 一起收摊：不然 Caddy 会留着占 443，下次启动报「端口被占」
        caddy_runtime.stop_caddy(caddy_process)

    # 控制台的 restart：uvicorn / Redis / Caddy 都收干净后再用退出码告诉守护进程
    # 「再拉一次」。非交互场景没有控制台，这个分支走不到。
    if getattr(server, "restart_requested", False):
        raise SystemExit(RESTART_EXIT_CODE)


__all__ = [
    "RESTART_EXIT_CODE",
    "SUPERVISED_ENV",
    "console_available",
    "should_supervise",
    "child_env",
    "supervise",
    "restart_argv",
    "run_server",
    "choose_environment",
    "ENVIRONMENT",
    "SETTINGS",
]
