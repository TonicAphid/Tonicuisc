"""运行环境（uisc 正式 / tuisc 测试）与 ``.env`` 加载。

两件事放在一起，是因为它们互相依赖：**先读 ``server/.env``，再决定环境**。

* :func:`load_env_file` —— 把 ``server/.env`` 灌进 ``os.environ``（**不覆盖已有的**，
  真正的环境变量优先级更高，方便临时覆盖 / systemd 里配）；
* :class:`Environment` —— 一个部署环境（正式 / 测试），决定对外域名、启动横幅、DDNS 目标。

以前 ``.env`` 只是被 start 脚本「检查了一下在不在」，里面的值从来没生效过——
所有配置都只认真正的环境变量。这个模块把那个坑补上。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: ``server/`` 目录（本文件在 ``server/tonicuisc_server/core/``）
SERVER_DIR = Path(__file__).resolve().parent.parent.parent

#: ``.env`` 的位置
ENV_PATH = SERVER_DIR / ".env"

#: 环境变量名：当前是哪套环境
ENV_KEY = "TONICUISC_ENV"


@dataclass(frozen=True)
class Environment:
    """一套部署环境。

    ``uisc`` 是正式的（App 默认连它），``tuisc`` 是这台测试机自己用的。
    两者的区别**只有域名**——端口、Redis、鉴权都一样，测试机靠改 PORT 错开。
    """

    #: 短名，写进 ``TONICUISC_ENV``
    key: str
    #: 中文说明，给启动提示用
    label: str
    #: 对外域名（不带协议）
    domain: str

    @property
    def public_url(self) -> str:
        return f"https://{self.domain}"

    @property
    def is_production(self) -> bool:
        return self.key == "uisc"


#: 支持的环境。要加第三套（比如内网 IP）在这里加一条就行。
ENVIRONMENTS: dict[str, Environment] = {
    "uisc": Environment(key="uisc", label="正式（部署机）", domain="uisc.tonicaphid.cn"),
    "tuisc": Environment(key="tuisc", label="测试（这台机器）", domain="tuisc.tonicaphid.cn"),
}

#: 没选过环境时的默认（正式的）
DEFAULT_ENV_KEY = "uisc"


def get_environment(key: str | None = None) -> Environment:
    """按名字取环境；认不出来就回退到默认，并说明一下。"""
    name = (key or os.getenv(ENV_KEY) or "").strip().lower()
    if name in ENVIRONMENTS:
        return ENVIRONMENTS[name]
    return ENVIRONMENTS[DEFAULT_ENV_KEY]


def parse_env_file(text: str) -> dict[str, str]:
    """解析 ``.env``：``KEY=VALUE``，支持 ``#`` 注释、引号、``export`` 前缀。

    用 ``utf-8-sig`` 那套思路处理 BOM：Windows 记事本 / PowerShell 存 UTF-8 会带 BOM，
    不剥掉的话第一行的 key 会变成 ``\\ufeffTONICUISC_HOST``，白读不到。
    """
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip().lstrip("\ufeff")
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        # 去成对引号（值里有空格时用得上）
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def load_env_file(path: Path | None = None, *, override: bool = False) -> dict[str, str]:
    """把 ``.env`` 读进 ``os.environ``，返回读到的键值对（方便调用方打印摘要）。

    ``override=False`` 时**不覆盖已存在的环境变量**：这样 ``TONICUISC_PORT=9000 python -m ...``
    这种临时覆盖仍然管用，systemd / 计划任务里配的也优先。
    """
    target = path or ENV_PATH
    if not target.exists():
        return {}
    try:
        text = target.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return {}
    values = parse_env_file(text)
    for key, value in values.items():
        if override or key not in os.environ:
            os.environ[key] = value
    return values


def write_env_value(key: str, value: str, path: Path | None = None) -> Path:
    """把 ``KEY=VALUE`` 写进 ``.env``：有就替换那一行，没有就追加。

    只动这一行，别把用户手写的其它配置冲掉。
    """
    target = path or ENV_PATH
    lines: list[str] = []
    if target.exists():
        lines = target.read_text(encoding="utf-8-sig", errors="replace").splitlines()

    replaced = False
    for index, raw in enumerate(lines):
        stripped = raw.strip().lstrip("\ufeff")
        if stripped.startswith("#") or "=" not in stripped:
            continue
        name = stripped.partition("=")[0].strip()
        if name == key:
            lines[index] = f"{key}={value}"
            replaced = True
            break

    if not replaced:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"{key}={value}")

    target.parent.mkdir(parents=True, exist_ok=True)
    # 统一 LF：这个文件要给 Linux 那台一起用
    target.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return target


def apply_environment(environment: Environment, *, persist: bool = True) -> Path | None:
    """把选定的环境写进 ``os.environ``（并按需落盘到 ``.env``）。

    两件事：

    * ``TONICUISC_ENV`` —— 记住选了哪套；
    * ``TONICUISC_PUBLIC_URL`` —— 横幅 / 登录链接用它，**跟着环境走**。

    ``PUBLIC_URL`` 这里踩过坑：早先写成「没设过才写」，于是从 tuisc 切回 uisc 时
    旧域名残留在 ``.env`` 里，横幅和登录链接指向错的域名。现在切环境一定同步改它
    （内存 + 文件都改），保证「选了哪套环境，对外地址就是哪套」。

    想**临时**指到别的地址（比如内网 IP）就别走这个函数——直接设真环境变量、
    并别去动 ``.env``，例如 ``$env:TONICUISC_PUBLIC_URL='http://192.168.5.37:8000'``。
    """
    os.environ[ENV_KEY] = environment.key
    os.environ["TONICUISC_PUBLIC_URL"] = environment.public_url

    written: Path | None = None
    if persist:
        written = write_env_value(ENV_KEY, environment.key)
        write_env_value("TONICUISC_PUBLIC_URL", environment.public_url)
    return written
