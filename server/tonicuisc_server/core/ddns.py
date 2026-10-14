"""DDNS：把本机当前的公网地址定时刷进阿里云解析（Windows / Linux 都支持）。

家里部署的地址会变（IPv6 前缀重播就换、IPv4 也可能变），所以要定期更新 A / AAAA。
调度用各系统自带的东西，不引入任何依赖：

* **Windows** —— 计划任务（``schtasks``），每 N 分钟跑一次；
* **Linux** —— 优先 **systemd timer**（用户级，不需要 root）；没有 systemd 就退回 **cron**。

真正的探测 + 改记录在 ``deploy/update_dns.py`` 里（那个脚本可以独立运行，
也方便你手动跑）。这里只负责「把它挂成定时任务」。

为什么不做成常驻线程：地址变化是低频事件（几小时到几天一次），
常驻一个线程不如交给系统调度器省心——服务重启、机器重启都不用管。
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from ..core.config import ENVIRONMENT, SETTINGS, SERVER_DIR
from ..core.environment import ENV_PATH, parse_env_file

#: 定时任务名（Windows 计划任务 / systemd unit 都用它）
TASK_NAME = "tonicuisc-ddns"
#: 默认间隔
DEFAULT_EVERY = "10m"

#: 上次运行结果（status 里显示）
STATE_FILE = SERVER_DIR / ".cache" / "ddns-state.txt"


def script_path() -> Path:
    """``deploy/update_dns.py`` 的位置。"""
    return SETTINGS.caddy_dir / "update_dns.py"


def python_path() -> str:
    """用当前解释器（venv / 系统 Python 都能对上）。"""
    return sys.executable or "python"


def _log_path() -> Path:
    return SERVER_DIR / ".cache" / "ddns.log"


def record_for_environment() -> str:
    """当前环境该更新哪个域名。"""
    return ENVIRONMENT.domain


def _load_aliyun_env() -> dict[str, str]:
    """从 ``deploy/.aliyun.env`` 读 AK（给计划任务用——它不会继承你的 shell 环境）。"""
    env_file = SETTINGS.caddy_dir / ".aliyun.env"
    if not env_file.exists():
        return {}
    values: dict[str, str] = {}
    for raw in env_file.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.strip().lstrip("\ufeff")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _every_to_minutes(every: str) -> int:
    """``10m`` / ``2h`` / ``30`` → 分钟数。"""
    text = (every or DEFAULT_EVERY).strip().lower()
    try:
        if text.endswith("m"):
            minutes = int(text[:-1])
        elif text.endswith("h"):
            minutes = int(text[:-1]) * 60
        else:
            minutes = int(text)
    except ValueError:
        raise SystemExit(f"间隔认不出来：{every}（写 10m / 2h / 30 这种）")
    if minutes < 1:
        raise SystemExit("间隔至少 1 分钟")
    return minutes


# ------------------------------------------------------------------ Windows
def _install_windows(minutes: int, record: str) -> int:
    script = script_path()
    if not script.exists():
        print(f"找不到 {script}")
        return 1
    # schtasks 的 /TR 里带引号很麻烦，所以写一个 .cmd 包一层
    cmd_path = SERVER_DIR / ".cache" / f"{TASK_NAME}.cmd"
    cmd_path.parent.mkdir(parents=True, exist_ok=True)
    aliyun = _load_aliyun_env()
    lines = ["@echo off", f'cd /d "{SETTINGS.caddy_dir}"']
    for key, value in aliyun.items():
        lines.append(f'set "{key}={value}"')
    lines.append(f'"{python_path()}" "{script}" --record {record} --update >> "{_log_path()}" 2>&1')
    cmd_path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")

    result = subprocess.run(
        [
            "schtasks", "/Create", "/TN", TASK_NAME,
            "/SC", "MINUTE", "/MO", str(minutes),
            "/TR", str(cmd_path),
            "/F",  # 已存在就覆盖
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        print(f"创建计划任务失败：{(result.stderr or result.stdout or '').strip()}")
        print("（试试用管理员 PowerShell 跑这条命令）")
        return 1
    print(f"已装计划任务「{TASK_NAME}」：每 {minutes} 分钟更新 {record} 的 A/AAAA")
    print(f"  包装脚本：{cmd_path}")
    print(f"  日志    ：{_log_path()}")
    print("  立即跑一次：python -m tonicuisc_server ddns run")
    return 0


def _remove_windows() -> int:
    result = subprocess.run(
        ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        print(f"没删掉（可能本来就没装）：{(result.stderr or result.stdout or '').strip()}")
        return 1
    print(f"已删除计划任务「{TASK_NAME}」")
    return 0


def _status_windows() -> int:
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        print(f"没装定时任务（{TASK_NAME}）。装：python -m tonicuisc_server ddns install")
        return 1
    for line in (result.stdout or "").splitlines():
        if ":" in line:
            print("  " + line.strip())
    return 0


# -------------------------------------------------------------------- Linux
def _systemd_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def _install_linux(minutes: int, record: str) -> int:
    script = script_path()
    if not script.exists():
        print(f"找不到 {script}")
        return 1
    aliyun = _load_aliyun_env()
    env_lines = "\n".join(f'Environment="{key}={value}"' for key, value in aliyun.items())
    log = _log_path()
    log.parent.mkdir(parents=True, exist_ok=True)

    service = f"""[Unit]
Description=Tonicuisc DDNS update ({record})

[Service]
Type=oneshot
{env_lines}
ExecStart={python_path()} {script} --record {record} --update
StandardOutput=append:{log}
StandardError=append:{log}
"""
    timer = f"""[Unit]
Description=Tonicuisc DDNS timer (every {minutes} min)

[Timer]
OnBootSec=2min
OnUnitActiveSec={minutes}min
Persistent=true

[Install]
WantedBy=timers.target
"""

    if shutil.which("systemctl"):
        target = _systemd_dir()
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{TASK_NAME}.service").write_text(service, encoding="utf-8")
        (target / f"{TASK_NAME}.timer").write_text(timer, encoding="utf-8")
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        result = subprocess.run(
            ["systemctl", "--user", "enable", "--now", f"{TASK_NAME}.timer"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"启用 timer 失败：{(result.stderr or '').strip()}")
            print("（有些系统需要先开 user 级 lingering：sudo loginctl enable-linger $USER）")
            return 1
        print(f"已装 systemd user timer「{TASK_NAME}」：每 {minutes} 分钟更新 {record}")
        print(f"  看状态：systemctl --user status {TASK_NAME}.timer")
        print(f"  看日志：{log}")
        return 0

    # 没有 systemd：退回 cron
    cron_line = (
        f"*/{minutes} * * * * cd {SETTINGS.caddy_dir} && "
        + " ".join(f"{key}={value}" for key, value in aliyun.items())
        + f" {python_path()} {script} --record {record} --update >> {log} 2>&1"
    )
    existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout or ""
    lines = [line for line in existing.splitlines() if TASK_NAME not in line]
    lines.append(f"# {TASK_NAME}")
    lines.append(cron_line)
    result = subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True)
    if result.returncode != 0:
        print("写 crontab 失败")
        return 1
    print(f"已装 cron 任务：每 {minutes} 分钟更新 {record}")
    print(f"  日志：{log}")
    return 0


def _remove_linux() -> int:
    if shutil.which("systemctl") and (_systemd_dir() / f"{TASK_NAME}.timer").exists():
        subprocess.run(["systemctl", "--user", "disable", "--now", f"{TASK_NAME}.timer"], check=False)
        for suffix in (".timer", ".service"):
            path = _systemd_dir() / f"{TASK_NAME}{suffix}"
            path.unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        print(f"已删除 systemd timer「{TASK_NAME}」")
        return 0

    existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout or ""
    if TASK_NAME not in existing:
        print("没装 cron 任务")
        return 1
    lines = [line for line in existing.splitlines() if TASK_NAME not in line]
    subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True)
    print("已删除 cron 任务")
    return 0


def _status_linux() -> int:
    if shutil.which("systemctl") and (_systemd_dir() / f"{TASK_NAME}.timer").exists():
        result = subprocess.run(
            ["systemctl", "--user", "list-timers", f"{TASK_NAME}.timer", "--no-pager"],
            capture_output=True,
            text=True,
        )
        print((result.stdout or "").strip() or "（timer 已装但没查到状态）")
        return 0
    existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout or ""
    if TASK_NAME in existing:
        print("已装 cron 任务：")
        for line in existing.splitlines():
            if TASK_NAME in line or "update_dns" in line:
                print("  " + line)
        return 0
    print(f"没装定时任务（{TASK_NAME}）。装：python -m tonicuisc_server ddns install")
    return 1


# -------------------------------------------------------------------- 公共
def install(args: list[str]) -> int:
    every = DEFAULT_EVERY
    record = record_for_environment()
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--every" and index + 1 < len(args):
            every = args[index + 1]
            index += 2
            continue
        if token == "--record" and index + 1 < len(args):
            record = args[index + 1]
            index += 2
            continue
        print(f"不认识的参数：{token}")
        return 2
    minutes = _every_to_minutes(every)

    if not _load_aliyun_env() and not os.getenv("ALIYUN_ACCESS_KEY_ID"):
        print(f"还没配阿里云 AK：往 {SETTINGS.caddy_dir / '.aliyun.env'} 里填")
        print("（照 .aliyun.env.example 抄两行即可；计划任务不会继承你 shell 里的环境变量，")
        print("  所以放在文件里最稳）")
        return 2

    if platform.system() == "Windows":
        return _install_windows(minutes, record)
    return _install_linux(minutes, record)


def remove() -> int:
    if platform.system() == "Windows":
        return _remove_windows()
    return _remove_linux()


def status() -> int:
    print(f"环境    ：{ENVIRONMENT.key}（{ENVIRONMENT.label}）")
    print(f"目标域名：{record_for_environment()}")
    print(f"更新脚本：{script_path()}")
    print()
    code = _status_windows() if platform.system() == "Windows" else _status_linux()
    if STATE_FILE.exists():
        print()
        print("上次结果：")
        print(STATE_FILE.read_text(encoding="utf-8", errors="replace").strip())
    return code


def run_once() -> int:
    """立刻跑一次 ``update_dns.py``（把当前环境的域名刷一遍）。"""
    script = script_path()
    if not script.exists():
        print(f"找不到 {script}")
        return 1
    aliyun = _load_aliyun_env()
    env = dict(os.environ)
    env.update(aliyun)
    record = record_for_environment()
    print(f"更新 {record} …")
    result = subprocess.run(
        [python_path(), str(script), "--record", record, "--update"],
        cwd=str(SETTINGS.caddy_dir),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    print(output)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    import time

    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    STATE_FILE.write_text(f"{stamp}  退出码 {result.returncode}\n{output}\n", encoding="utf-8")
    return result.returncode
