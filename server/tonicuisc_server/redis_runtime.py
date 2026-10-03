"""托管 Redis：首次启动自动下载 redis-server、拉起子进程、退出时回收。

设计要点（照着这几条读代码就够了）：

* **只连不碰**：``TONICUISC_REDIS_URL``（``SETTINGS.redis_url``）非空表示
  「用户自己提供的 Redis」。这时我们只连接，**绝不**启动、**绝不**关闭它。
* **能用现成的就用现成的**：``ensure()`` 第一件事是 ping 现有配置；能连上就
  直接复用（``started_by_us=False``），所以「上次被强杀留下的 Redis」也会被
  直接接管复用，而不是又拉一个把端口撞了。
* **只关自己拉起来的**：``stop()`` 只在 ``started_by_us`` 为真时动手，
  先把 ``redis.pid`` 记下来，退出时 ``terminate()`` → 等 10 秒 → ``kill()``，
  Windows 上再兜一层 ``taskkill /T /F``。
* **找不到就下**：Windows 下 ``tporadowski/redis`` 免安装 zip（Redis 5.0.14.1），
  Linux 下优先官方 deb（Redis 7.2.5），deb 不行再退回源码 ``make``。
  二进制只落在 ``redis/<win|linux>/``，**不进仓库**。

对外只有这几个名字：``RedisRuntime`` / ``RUNTIME`` / ``ensure_redis_client()`` /
``shutdown_redis()``。其它都是内部实现。
"""

from __future__ import annotations

import atexit
import ctypes
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

import redis

from .config import SETTINGS, Settings
from .items import timing_log

# --------------------------------------------------------------------- 常量

#: Windows 免安装包（tporadowski/redis，Redis 5.0.14.1 x64）。
#: 2026-02 实测可用：HTTP 200，12_617_669 字节。
WINDOWS_ZIP_URL = "https://github.com/tporadowski/redis/releases/download/v5.0.14.1/Redis-x64-5.0.14.1.zip"
#: zip 里我们真正要留的文件（其余 redis-benchmark 之类丢掉），另外所有 *.dll 都会保留
WINDOWS_KEEP_FILES = ("redis-server.exe", "redis-cli.exe")

#: Linux 官方 deb（Ubuntu noble，Redis 7.2.5）。2026-02 实测可用：HTTP 200，84_108 字节起。
#: 注意路径是 ``pool/<发行版代号>/r/re/``，不是 ``pool/main/r/redis/``（那条会 403）。
LINUX_DEB_URL = "https://packages.redis.io/deb/pool/noble/r/re/redis-server_7.2.5-1rl1~noble1_amd64.deb"
#: 兜底：官方源码包（deb 解不出来、或没有 ar 时，机器上有 gcc/make 就现场编译）
LINUX_SOURCE_URL = "https://download.redis.io/releases/redis-7.2.5.tar.gz"
#: 源码包解出来的目录名（跟上面版本保持一致）
LINUX_SOURCE_DIR = "redis-7.2.5"

#: 连接/读写超时（秒）：连不上时别让整个服务卡死
CONNECT_TIMEOUT = 5.0
#: 拉起 Redis 后最多等多久能 PING 通（秒）
START_TIMEOUT = 20.0
#: 让 Redis 自己退出（SHUTDOWN，会把 AOF 刷盘）后最多等多久（秒）
GRACEFUL_TIMEOUT = 10.0
#: 下载超时（秒）
DOWNLOAD_TIMEOUT = 120.0
#: 下载缓冲块
DOWNLOAD_CHUNK = 256 * 1024


def _log(message: str) -> None:
    """``[redis]`` 前缀日志，直接 stdout，不碰 logging 全局配置。"""
    try:
        print(f"[redis] {message}", flush=True)
    except UnicodeEncodeError:
        # Windows 控制台编码扛不住中文时退化成 ASCII，别把服务搞崩
        timing_log(f"redis: {message.encode('ascii', 'replace').decode('ascii')}")


# ------------------------------------------------------------------ 下载工具


def _download_file(url: str, dest: Path) -> Path:
    """下载 ``url`` 到 ``dest``：先写临时文件、成功后原子改名，失败不留半个文件。

    用标准库 urllib（不额外引入依赖），带 ``User-Agent``（GitHub 对空 UA 比较挑）。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "tonicuisc-redis-fetch/1.0"})
    fd, temp_name = tempfile.mkstemp(prefix=f"{dest.name}.", suffix=".part", dir=str(dest.parent))
    temp_path = Path(temp_name)
    written = 0
    try:
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
            while True:
                chunk = response.read(DOWNLOAD_CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                written += len(chunk)
        os.replace(temp_path, dest)
    except BaseException:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    _log(f"下载完成 {dest.name}（{written / 1048576:.1f} MB）")
    return dest


def _run_checked(cmd: list[str], cwd: Path) -> None:
    """跑一条外部命令，失败就把 stderr 一起抛出来（中文报错里能用上）。"""
    result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        tail = detail[-1] if detail else f"退出码 {result.returncode}"
        raise RuntimeError(f"命令 {' '.join(cmd)} 失败：{tail}")


# -------------------------------------------------------------- Windows 作业对象

#: ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``：Job 句柄一关，里面的进程全被杀。
#: 父进程被强杀时句柄随进程消失，Redis 也就跟着死了 —— 这就是我们要的效果。
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9


class _JobBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JobExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _join_job_object(pid: int) -> int | None:
    """把 ``pid`` 放进一个「句柄关闭就全杀」的 Job Object，返回句柄（失败返回 None）。

    纯粹是加分项：父进程被 ``taskkill /F`` 干掉、来不及跑 ``atexit`` 时，
    操作系统会顺手把 Redis 一起带走。任何一步失败都静默放弃，绝不影响启动。
    """
    if os.name != "nt" or pid <= 0:
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        kernel32.SetInformationJobObject.restype = ctypes.c_int
        kernel32.SetInformationJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.AssignProcessToJobObject.restype = ctypes.c_int
        kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _JobExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)
        ):
            return job  # 设置失败也别释放：至少没坏处
        # PROCESS_SET_QUOTA(0x0100) | PROCESS_TERMINATE(0x0001)
        handle = kernel32.OpenProcess(0x0100 | 0x0001, False, pid)
        if not handle:
            return job
        try:
            kernel32.AssignProcessToJobObject(job, handle)
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
        # 句柄必须一直活着：一关就等于立刻杀 Redis，所以交给调用方持有
        return int(job)
    except Exception:
        return None


# ------------------------------------------------------------------- 主运行时


class RedisRuntime:
    """进程级 Redis 运行时：找到 / 下载 / 拉起 / 复用 / 回收 redis-server。"""

    #: 实际用的 redis-server 可执行文件；连接外部 Redis（或复用已有实例）时为 None
    server_path: Path | None
    #: True = 这个 Redis 是我们进程拉起来的，退出时由我们负责关掉
    started_by_us: bool

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or SETTINGS
        self.server_path = None
        self.started_by_us = False
        self._client: redis.Redis | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._job_handle: int | None = None
        self._signal_previous: dict[int, Any] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------ 对外接口

    def ensure(self) -> redis.Redis:
        """幂等：返回一个可用的 redis-py 客户端；需要时负责拉起 Redis。"""
        with self._lock:
            if self._client is not None:
                return self._client

            # ① 现有配置能不能连上？能连上就直接用，绝不启动、绝不关闭
            client = self._build_client()
            if self._ping(client):
                self._client = client
                self.started_by_us = False
                self.server_path = None
                _log(f"已连上现有 Redis（{self._target()}），由外部管理，退出时不会关闭它")
                return client

            # ①b 给了 TONICUISC_REDIS_URL 就只连这一台：连不上也绝不另起一个本地实例
            #     （用户自己提供的 Redis，我们只有连接权，没有启动/关闭权）
            if self._settings.redis_url:
                raise RuntimeError(
                    f"连不上 TONICUISC_REDIS_URL 指定的 Redis（{self._settings.redis_url}）。"
                    "这个地址是用户自己提供的，服务端只负责连接、不负责启动，"
                    "所以不会去拉起本地实例。请确认那台 Redis 在跑，地址 / 密码 / 库号没写错。"
                )

            # ② 不让自动拉起就到此为止
            if not self._settings.redis_autostart:
                raise RuntimeError(
                    f"连不上 Redis（{self._target()}），自动拉起已关闭。"
                    "请用 TONICUISC_REDIS_URL 指定一个可用的 Redis 地址，"
                    "或打开 TONICUISC_REDIS_AUTOSTART=1 让服务自己启动 Redis。"
                )

            # ③④⑤ 找可执行文件 → 必要时下载 → 拉起来 → 等 PING 通
            server = self.find_server()
            if server is None:
                if not self._settings.redis_download:
                    raise RuntimeError(
                        f"连不上 Redis（{self._target()}），本机也找不到 redis-server，"
                        "而自动下载已关闭（TONICUISC_REDIS_DOWNLOAD=0）。请手动安装 Redis"
                        f"（Windows 把 redis-server.exe 放到 {self._settings.redis_bin_dir}，"
                        "Linux 用 apt install redis-server），"
                        "或用 TONICUISC_REDIS_SERVER 指定 redis-server 的完整路径。"
                    )
                _log("首次启动，本机没有 redis-server，正在自动下载 ...")
                server = self._download_server()
            self.server_path = server

            process = self._spawn(server)
            try:
                self._wait_ready(client)
            except BaseException:
                self._kill_process(process)
                self.server_path = None
                raise

            self.started_by_us = True
            self._client = client
            _log(f"Redis 已由本服务启动：{self._target()}（pid {process.pid}，退出时自动关闭）")
            return client

    def stop(self) -> None:
        """幂等：只关「我们自己拉起来的」那个 Redis，外部实例一律不碰。"""
        with self._lock:
            process = self._process
            client = self._client
            self._process = None
            self._client = None
            started_by_us = self.started_by_us
            self.started_by_us = False
            self.server_path = None
            if not started_by_us or process is None:
                # 从没启动过 / 连的是别人的：什么都不做（重复调用也安全）
                return
            self._shutdown_gracefully(client, process)
            self._kill_process(process)
            self._drop_pid_file()
            _log("托管 Redis 已停止")

    def _shutdown_gracefully(self, client: redis.Redis | None, process: subprocess.Popen[bytes]) -> None:
        """先请 Redis 自己退出（会把 AOF 刷盘），刷不掉再交给 ``_kill_process`` 硬杀。

        直接 terminate() 在 Windows 上是 TerminateProcess：Redis 来不及把 AOF buffer
        落地，``appendfsync everysec`` 下最多丢 1 秒的写入 —— 用户「点完喜欢就关服务」
        正好撞上，再启动就少一条。所以先发 SHUTDOWN，等它自己走。
        """
        if client is not None:
            try:
                client.shutdown(nosave=True)
            except Exception:
                # 收到 SHUTDOWN 后 Redis 直接断开连接，redis-py 抛 ConnectionError 属于正常
                pass
        try:
            process.wait(timeout=GRACEFUL_TIMEOUT)
        except Exception:
            pass

    def ping(self) -> bool:
        """能连上就 True，任何异常都吞掉（探活用，不抛）。"""
        client = self._client
        if client is None:
            try:
                client = self._build_client()
            except Exception:
                return False
        return self._ping(client)

    # ------------------------------------------------------------ 查找二进制

    def find_server(self) -> Path | None:
        """按 ``SETTINGS.redis_server`` → ``redis/<win|linux>/`` → PATH 的顺序找。"""
        explicit = (self._settings.redis_server or "").strip()
        if explicit:
            path = Path(explicit).expanduser()
            if path.is_file():
                return path.resolve()
            _log(f"TONICUISC_REDIS_SERVER 指向的文件不存在：{path}，继续自动查找")

        names = ("redis-server.exe", "redis-server") if os.name == "nt" else ("redis-server",)
        bin_dir = self._settings.redis_bin_dir
        for name in names:
            candidate = bin_dir / name
            if candidate.is_file():
                return candidate.resolve()

        found = shutil.which("redis-server")
        if found:
            return Path(found).resolve()
        return None

    # -------------------------------------------------------------- 配置文件

    def build_config(self) -> str:
        """生成托管实例的 redis.conf 内容（兼容 Windows 上的 Redis 5.0.14.1）。

        只用 5.0 就有的指令；路径统一写成带引号的正斜杠形式（``dir "D:/x/y"``），
        免得 Windows 反斜杠被当成转义。
        """
        data_dir = self._settings.redis_data_dir
        posix_dir = data_dir.as_posix()
        lines = [
            "# Tonicuisc 托管 Redis 配置：由 redis_runtime.py 自动生成，手改会被覆盖。",
            "bind 127.0.0.1",
            f"port {self._settings.redis_port}",
            f'dir "{posix_dir}"',
            "daemonize no",
            # 只监听回环地址，protected-mode 开着反而会在没有密码时拒连，这里关掉
            "protected-mode no",
            "loglevel notice",
            f'logfile "{posix_dir}/redis.log"',
            f'pidfile "{posix_dir}/redis.pid"',
        ]
        if self._settings.redis_appendonly:
            # 全量数据都在 Redis 里，靠 AOF 保证重启不丢；RDB 就没必要了
            lines += ['save ""', "appendonly yes", "appendfsync everysec"]
        else:
            lines += ["save 900 1", "appendonly no"]
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------ 连接与探活

    def _target(self) -> str:
        """当前连的是哪儿（只用来打日志）。"""
        if self._settings.redis_url:
            return self._settings.redis_url
        return f"{self._settings.redis_host}:{self._settings.redis_port}/{self._settings.redis_db}"

    def _build_client(self) -> redis.Redis:
        """按 ``SETTINGS.redis_config()`` 造客户端，两种返回形态都能吃。"""
        params = dict(self._settings.redis_config())
        common: dict[str, Any] = {
            "decode_responses": True,
            "socket_connect_timeout": CONNECT_TIMEOUT,
            "socket_timeout": CONNECT_TIMEOUT,
        }
        url = params.pop("url", None)
        if not url:
            params = {key: value for key, value in params.items() if value is not None}

        # 两处坑，都在 redis-py 8.x 上实测过：
        #  1. RESP3 要 Redis 6+ 才认（握手时发 `HELLO 3`）。Windows 免安装包是
        #     Redis 5.0.14.1，收到 HELLO 直接回 `unknown command`，所以固定按 RESP2 连。
        #  2. 默认带回退重试，连一个没开的端口要磨 26 秒（connect timeout 5 秒 × 好几轮）。
        #     我们自己的 `_wait_ready` 已经显式轮询了，这里关掉重试，探测一次就够。
        # 老版本 redis-py 不认这些参数，TypeError 就退到下一组（没有 protocol 的老版本
        # 本来就是 RESP2，行为一致）。
        extras: tuple[dict[str, Any], ...] = (
            {**common, "protocol": 2, "retry_on_error": [], "retry": None},
            {**common, "protocol": 2},
            dict(common),
        )
        last_error: Exception = RuntimeError("无法创建 Redis 客户端")
        for extra in extras:
            try:
                if url:
                    # 注意：redis-py 的 Redis.__init__ 没有 url 参数（8.x 实测 TypeError），
                    # 带 url 必须走 from_url 这个类方法，别写成 redis.Redis(url=...)。
                    return redis.Redis.from_url(url, **extra)
                return redis.Redis(**params, **extra)
            except TypeError as exc:
                last_error = exc
                continue
        raise last_error

    @staticmethod
    def _ping(client: redis.Redis) -> bool:
        try:
            return bool(client.ping())
        except Exception:
            return False

    def _wait_ready(self, client: redis.Redis, timeout: float = START_TIMEOUT) -> None:
        """等 Redis 起来（最多 ``timeout`` 秒），中途子进程死了就立刻报错。"""
        deadline = time.monotonic() + timeout
        while True:
            process = self._process
            if process is not None and process.poll() is not None:
                raise RuntimeError(
                    f"redis-server 启动后立刻退出（退出码 {process.returncode}）。"
                    f"请看日志 {self._settings.redis_data_dir / 'redis.log'}；"
                    "常见原因：端口被占用，或配置文件不兼容当前版本。"
                )
            if self._ping(client):
                return
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"redis-server 已启动，但 {timeout:.0f} 秒内没有 PING 通"
                    f"（{self._target()}）。请看日志 {self._settings.redis_data_dir / 'redis.log'}。"
                )
            time.sleep(0.2)

    # ---------------------------------------------------------------- 子进程

    def _spawn(self, server: Path) -> subprocess.Popen[bytes]:
        """写配置、起子进程、记 pid、挂 Job Object / 信号处理。"""
        data_dir = self._settings.redis_data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        conf_path = data_dir / "redis.conf"
        try:
            conf_path.write_text(self.build_config(), encoding="utf-8")
        except UnicodeEncodeError:
            conf_path.write_text(self.build_config(), encoding="gbk", errors="replace")

        creationflags = 0
        if os.name == "nt":
            # CREATE_NO_WINDOW：不弹黑框；CREATE_NEW_PROCESS_GROUP：Ctrl+C 不会直冲子进程
            creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP

        _log(f"启动 redis-server：{server} --conf {conf_path}")
        process = subprocess.Popen(
            [str(server), str(conf_path)],
            cwd=str(data_dir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        self._process = process
        self._write_pid_file(process.pid)
        self._job_handle = _join_job_object(process.pid) if os.name == "nt" else None
        self.install_signal_handlers()
        return process

    def _kill_process(self, process: subprocess.Popen[bytes]) -> None:
        """terminate → 等 10 秒 → kill；Windows 上再兜一层 taskkill /T /F。"""
        if process.poll() is not None:
            return
        try:
            process.terminate()
        except Exception:
            pass
        try:
            process.wait(timeout=10)
            return
        except Exception:
            pass
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    check=False,
                )
            except Exception:
                pass
        try:
            process.kill()
        except Exception:
            pass
        try:
            process.wait(timeout=5)
        except Exception:
            pass

    # ------------------------------------------------------------------ pid 文件

    def _write_pid_file(self, pid: int) -> None:
        try:
            self._settings.redis_data_dir.joinpath("redis.pid").write_text(str(pid), encoding="ascii")
        except OSError:
            pass  # pid 文件只是给下次启动留个线索，写不了不算错

    def _drop_pid_file(self) -> None:
        try:
            (self._settings.redis_data_dir / "redis.pid").unlink(missing_ok=True)
        except OSError:
            pass

    # ---------------------------------------------------------------- 信号处理

    def install_signal_handlers(self) -> None:
        """给 SIGINT/SIGTERM 包一层：先关我们自己拉起来的 Redis，再交给原 handler。

        为什么要包而不是替换：uvicorn 自己也要处理这两个信号。我们保存它原来的
        handler，处理完照样调它，所以无论谁先注册都不会打架。要是 uvicorn 注册得
        比我们晚，它会把我们的 handler 覆盖掉 —— 那也没关系，``atexit`` 还兜着底。
        """
        if threading.current_thread() is not threading.main_thread():
            return
        for name in ("SIGINT", "SIGTERM"):
            signum = getattr(signal, name, None)
            if signum is None:
                continue
            try:
                previous = signal.getsignal(signum)
            except (OSError, ValueError):
                continue
            if self._signal_previous.get(signum) is previous:
                continue  # 这个信号已经包过一层了
            self._signal_previous[signum] = previous

            def _handler(received: int, frame: Any, _previous: Any = previous) -> None:
                try:
                    self.stop()
                except Exception:
                    pass
                if _previous is signal.SIG_IGN:
                    return
                if callable(_previous):
                    _previous(received, frame)
                    return
                # 原来的行为是「默认」：SIGINT → KeyboardInterrupt，其余按 128+n 退出。
                # 抛异常而不是直接 os._exit，这样 atexit 还有机会跑。
                if received == getattr(signal, "SIGINT", None):
                    raise KeyboardInterrupt
                raise SystemExit(128 + int(received))

            try:
                signal.signal(signum, _handler)
            except (OSError, ValueError):
                continue

    # ------------------------------------------------------------------ 下载

    def _work_dir(self) -> Path:
        """下载/解包的临时目录（放 .cache/redis 下面，绝不会被提交）。"""
        work = self._settings.redis_data_dir / ".download"
        work.mkdir(parents=True, exist_ok=True)
        return work

    def _download_server(self) -> Path:
        """按平台自动下载 redis-server 到 ``redis/<win|linux>/``。失败给中文可操作报错。"""
        bin_dir = self._settings.redis_bin_dir
        bin_dir.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            return self._download_windows(bin_dir)
        return self._download_linux(bin_dir)

    def _download_windows(self, bin_dir: Path) -> Path:
        """Windows：下 tporadowski 免安装 zip，解出 redis-server.exe（顺带 redis-cli.exe）。"""
        target = bin_dir / "redis-server.exe"
        _log(f"正在下载 Windows 版 Redis（约 12 MB）：{WINDOWS_ZIP_URL}")
        work = self._work_dir()
        try:
            archive = _download_file(WINDOWS_ZIP_URL, work / "redis-win.zip")
            keep = {name.lower() for name in WINDOWS_KEEP_FILES}
            extracted: list[str] = []
            with zipfile.ZipFile(archive) as zip_file:
                for member in zip_file.infolist():
                    if member.is_dir():
                        continue
                    base = Path(member.filename).name
                    lower = base.lower()
                    if lower not in keep and not lower.endswith(".dll"):
                        continue
                    with zip_file.open(member) as src, (bin_dir / base).open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                    extracted.append(base)
            if not target.is_file():
                raise RuntimeError(
                    f"压缩包 {WINDOWS_ZIP_URL} 里没有 redis-server.exe"
                    f"（解出来的文件：{', '.join(extracted) or '空'}）"
                )
            _log(f"redis-server 已就绪：{target}")
            return target
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"自动下载 Windows 版 Redis 失败：{exc}\n"
                f"请手动把 redis-server.exe 放到 {bin_dir}\\（从 {WINDOWS_ZIP_URL} 下载解压即可），"
                "或用 TONICUISC_REDIS_SERVER 指定它的完整路径，"
                "或先装一个 Redis 再用 TONICUISC_REDIS_URL 指过去。"
            ) from exc
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _download_linux(self, bin_dir: Path) -> Path:
        """Linux：优先官方 deb（ar + tar 解出 usr/bin/redis-server），不行退回源码编译。"""
        target = bin_dir / "redis-server"
        deb_error: Exception | None = None
        try:
            return self._install_from_deb(bin_dir, target)
        except Exception as exc:
            deb_error = exc
            _log(f"deb 方式安装失败（{exc}），改用源码编译兜底")

        try:
            return self._build_from_source(bin_dir, target)
        except Exception as exc:
            raise RuntimeError(
                "自动下载 Linux 版 Redis 失败：\n"
                f"  · deb 方式：{deb_error}\n"
                f"  · 源码方式：{exc}\n"
                "请任选一种手动处理：\n"
                "  1) apt install redis-server（最省事）\n"
                f"  2) 把 linux 版 redis-server 二进制放进 {bin_dir}/ 并 chmod +x\n"
                "  3) 用 TONICUISC_REDIS_SERVER=/path/to/redis-server 指定路径\n"
                "  4) 用 TONICUISC_REDIS_URL=redis://主机:端口/0 指向现成的 Redis"
            ) from exc

    def _install_from_deb(self, bin_dir: Path, target: Path) -> Path:
        """下载官方 deb，用 ``ar x`` + ``tar xf data.tar.*`` 抠出 usr/bin/redis-server。"""
        if shutil.which("ar") is None:
            raise RuntimeError("系统里没有 ar（binutils），解不了 deb")
        if shutil.which("tar") is None:
            raise RuntimeError("系统里没有 tar，解不了 deb")

        _log(f"正在下载 Debian 版 Redis：{LINUX_DEB_URL}")
        work = self._work_dir()
        try:
            deb_path = _download_file(LINUX_DEB_URL, work / "redis-server.deb")
            unpack = work / "deb"
            unpack.mkdir(exist_ok=True)
            _run_checked(["ar", "x", str(deb_path)], unpack)
            data_members = sorted(unpack.glob("data.tar.*"))
            if not data_members:
                raise RuntimeError("deb 里没有 data.tar.*（文件不完整？）")
            _run_checked(["tar", "xf", data_members[0].name], unpack)
            source = unpack / "usr" / "bin" / "redis-server"
            if not source.is_file():
                raise RuntimeError("deb 里没有 usr/bin/redis-server")
            shutil.copy2(source, target)
            target.chmod(0o755)
            _log(f"redis-server 已就绪：{target}")
            return target
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _build_from_source(self, bin_dir: Path, target: Path) -> Path:
        """兜底：下源码包现场 ``make``（只在有 gcc/make 时，可能要几分钟）。"""
        for tool in ("make", "gcc"):
            if shutil.which(tool) is None:
                raise RuntimeError(f"系统里没有 {tool}，编译不了 Redis 源码")

        _log(f"正在下载 Redis 源码：{LINUX_SOURCE_URL}")
        work = self._work_dir()
        try:
            tarball = _download_file(LINUX_SOURCE_URL, work / f"{LINUX_SOURCE_DIR}.tar.gz")
            with tarfile.open(tarball) as tar:
                tar.extractall(work)  # noqa: S202 - 官方源码包，来源可信
            source_dir = work / LINUX_SOURCE_DIR
            if not source_dir.is_dir():
                source_dir = next((p for p in work.iterdir() if p.is_dir() and p.name.startswith("redis-")), None)
            if source_dir is None:
                raise RuntimeError("源码包解出来的目录找不到")

            jobs = str(os.cpu_count() or 2)
            _log(f"正在编译 Redis（make -j{jobs}，可能要几分钟）...")
            _run_checked(["make", f"-j{jobs}"], source_dir)
            built = source_dir / "src" / "redis-server"
            if not built.is_file():
                raise RuntimeError("编译完了却没看到 src/redis-server")
            shutil.copy2(built, target)
            target.chmod(0o755)
            _log(f"redis-server 已就绪：{target}")
            return target
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"<RedisRuntime target={self._target()} started_by_us={self.started_by_us} "
            f"server={self.server_path}>"
        )


#: 进程级单例
RUNTIME = RedisRuntime()


def ensure_redis_client() -> redis.Redis:
    """``RUNTIME.ensure()`` 的简写：拿到一个可用的 redis-py 客户端。"""
    return RUNTIME.ensure()


def shutdown_redis() -> None:
    """``RUNTIME.stop()`` 的简写：只关我们自己拉起来的那个 Redis。"""
    RUNTIME.stop()


#: 进程退出时兜底回收（SIGKILL / taskkill /F 不会跑 atexit，那种情况交给 Job Object）
atexit.register(shutdown_redis)


if __name__ == "__main__":  # pragma: no cover - 手动排查用：python -m tonicuisc_server.redis_runtime
    client = ensure_redis_client()
    print(f"PING -> {client.ping()}  server={RUNTIME.server_path} started_by_us={RUNTIME.started_by_us}")
    if "--stop" in sys.argv:
        shutdown_redis()
        print("已停止")
