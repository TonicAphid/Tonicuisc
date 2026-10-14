"""启动服务：环境选择 → uvicorn 装配 + 原生 TLS + 控制台提示符。"""

from __future__ import annotations

import sys

import uvicorn

from ..core.config import ENVIRONMENT, SETTINGS
from ..core.environment import ENVIRONMENTS, apply_environment
from ..core.tls import ssl_kwargs
from .console import attach, detach


def choose_environment(*, interactive: bool | None = None) -> bool:
    """第一次启动时问一次「这是哪套环境」，选完写进 ``.env``，以后不再问。

    返回是否真的问了。非交互场景（systemd / 计划任务 / 管道）自动跳过，
    用 ``TONICUISC_ENV=tuisc`` 或 ``--env`` 指定即可。
    """
    import os

    if os.getenv("TONICUISC_ENV"):
        return False  # 已经选过了（.env 或环境变量里）
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
        except (EOFError, KeyboardInterrupt):
            print()
            answer = ""
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
    path = apply_environment(environment, persist=True)
    print(f"  已记住：{environment.key}（{environment.public_url}）")
    if path:
        print(f"  写进了 {path}，以后不再问；要改就编辑那个文件")
    print()
    return True


def run_server(*, interactive: bool | None = None) -> None:
    """起服务；stdin 是控制台的话，顺手挂一个 ``>`` 命令提示符。"""
    choose_environment(interactive=interactive)

    # 环境可能是刚选的：重新取一次（SETTINGS 是模块级单例，读的是 import 时的值）
    from ..core import config as config_module

    settings = config_module.SETTINGS
    environment = config_module.ENVIRONMENT
    print(f"[env] {environment.key} —— {environment.label} / {environment.public_url}", flush=True)

    tls = ssl_kwargs(settings)
    if settings.reload:
        # 开发模式：reloader 由 uvicorn 自己另开进程管，控制台的 stop 停不动它，就不挂了
        uvicorn.run("tonicuisc_server.api:app", host=settings.host, port=settings.port, reload=True, **tls)
        return

    server = uvicorn.Server(
        uvicorn.Config("tonicuisc_server.api:app", host=settings.host, port=settings.port, **tls)
    )
    prompt = attach(server)
    try:
        server.run()
    finally:
        detach(prompt)


__all__ = ["run_server", "choose_environment", "ENVIRONMENT", "SETTINGS"]
