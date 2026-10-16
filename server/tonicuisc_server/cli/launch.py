"""启动服务：环境选择 → uvicorn 装配 + 原生 TLS + 控制台提示符。"""

from __future__ import annotations

import sys

import uvicorn

from ..core.config import ENVIRONMENT, SETTINGS
from ..core.environment import ENVIRONMENTS, apply_environment
from ..core.tls import ssl_kwargs
from .console import attach, detach


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


__all__ = ["run_server", "choose_environment", "ENVIRONMENT", "SETTINGS"]
