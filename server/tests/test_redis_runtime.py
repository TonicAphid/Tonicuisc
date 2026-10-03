"""``RedisRuntime`` 单元测试：不联网、不下载、不真的启动 Redis。

所有用例都只碰「决策逻辑」——连不连、起不起、找哪个二进制、配置文件写了什么、
该不该关进程——真正的网络和子进程一律被替身挡住。
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import sys
import types
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

# 这些测试不该依赖外部服务，所以在没装 redis-py 的机器上塞一个最小替身进去，
# 让 redis_runtime 能 import 成功。装了真包就用真的（下面的用例本来就会把
# redis.Redis 换掉，真假都不影响断言）。
try:  # pragma: no cover - 取决于环境
    import redis as _real_redis  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover - 只在没装 redis-py 时走到
    _stub = types.ModuleType("redis")

    class _StubRedis:
        """占位客户端，真用例会 monkeypatch 掉 ``redis.Redis``。"""

        def __init__(self, *args: object, **kwargs: object) -> None:
            self.args = args
            self.kwargs = kwargs

        @classmethod
        def from_url(cls, *args: object, **kwargs: object) -> "_StubRedis":
            return cls(*args, **kwargs)

        def ping(self) -> bool:  # pragma: no cover - 占位
            raise ConnectionError("stub redis: 没有真的 Redis")

    _stub_exceptions = types.ModuleType("redis.exceptions")

    class _StubRedisError(Exception):
        """替身异常基类。"""

    class _StubConnectionError(_StubRedisError, ConnectionError):
        """替身连接错误。"""

    _stub_exceptions.RedisError = _StubRedisError
    _stub_exceptions.ConnectionError = _StubConnectionError
    _stub.Redis = _StubRedis
    _stub.exceptions = _stub_exceptions
    sys.modules["redis"] = _stub
    sys.modules["redis.exceptions"] = _stub_exceptions

from tonicuisc_server import redis_runtime as rr  # noqa: E402
from tonicuisc_server.config import SETTINGS  # noqa: E402

SERVER_NAME = "redis-server.exe" if os.name == "nt" else "redis-server"

#: 临时目录放这儿（跟 conftest 的 _scratch 同一个地方，.gitignore 已经忽略了）
SCRATCH = Path(__file__).resolve().parent / "_scratch"


@pytest.fixture()
def work_dir() -> Iterator[Path]:
    """一个干净的临时目录。

    不用 pytest 自带的 ``tmp_path``：它建目录时带 0700 权限，在收紧权限的环境里
    会直接访问不了；这里用默认权限，跟仓库里其它测试保持一致。
    """
    root = SCRATCH / f"redis-runtime-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ----------------------------------------------------------------- 测试替身


class _FakeProcess:
    """够用的 ``subprocess.Popen`` 替身：只记 terminate/kill 有没有被叫过。

    ``exits_on_wait=True`` 表示「它自己乖乖退了」（收到 SHUTDOWN 就退出），
    这时就不该再 terminate 了。
    """

    def __init__(self, pid: int = 4242, exits_on_wait: bool = False) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        self.exits_on_wait = exits_on_wait
        self.exited = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        if self.exits_on_wait and self.returncode is None:
            self.returncode = 0
            self.exited = True
        return self.returncode if self.returncode is not None else 0


class _ShutdownRedis:
    """记下有没有对我们发的 ``SHUTDOWN`` 作出反应。"""

    def __init__(self) -> None:
        self.shutdown_called = False
        self.shutdown_kwargs: dict[str, object] = {}

    def shutdown(self, **kwargs: object) -> None:
        self.shutdown_called = True
        self.shutdown_kwargs = kwargs


class _AliveRedis:
    """``ping()`` 永远成功的假客户端。"""

    instances: list["_AliveRedis"] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.args = args
        self.kwargs = kwargs
        type(self).instances.append(self)

    @classmethod
    def from_url(cls, url: str, **kwargs: object) -> "_AliveRedis":
        return cls(url, **kwargs)

    def ping(self) -> bool:
        return True


class _DeadRedis:
    """``ping()`` 永远失败的假客户端（模拟「连不上」）。"""

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.args = args
        self.kwargs = kwargs

    @classmethod
    def from_url(cls, url: str, **kwargs: object) -> "_DeadRedis":
        return cls(url, **kwargs)

    def ping(self) -> bool:
        raise ConnectionError("connection refused")


class _ToggleRedis:
    """先连不上、拉起之后就连得上的假客户端（给「下载 → 拉起」流程用）。"""

    alive = False
    instances: list["_ToggleRedis"] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.args = args
        self.kwargs = kwargs
        type(self).instances.append(self)

    @classmethod
    def from_url(cls, url: str, **kwargs: object) -> "_ToggleRedis":
        return cls(url, **kwargs)

    def ping(self) -> bool:
        if not type(self).alive:
            raise ConnectionError("not up yet")
        return True


def _settings(work_dir: Path, **overrides: object):
    """把 SETTINGS 改成「全部指向 work_dir、不联网、不下载」的一份拷贝。"""
    base: dict[str, object] = {
        "redis_url": "",
        "redis_host": "127.0.0.1",
        "redis_port": 6390,
        "redis_db": 0,
        "redis_server": "",
        "redis_dir": work_dir / "redis",
        "cache_dir": work_dir / "cache",
        "redis_autostart": True,
        "redis_download": False,
        "redis_appendonly": True,
    }
    base.update(overrides)
    return dataclasses.replace(SETTINGS, **base)


# ------------------------------------------------------- ① 只连接、不启动


def test_redis_url_only_connects_and_never_starts(work_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """给了 TONICUISC_REDIS_URL：只连接，绝不拉起、绝不下载。"""
    _AliveRedis.instances = []
    monkeypatch.setattr(rr.redis, "Redis", _AliveRedis)
    # 这两个只要被调用就说明走错了路
    monkeypatch.setattr(rr.RedisRuntime, "_download_server", _unexpected)
    monkeypatch.setattr(rr.RedisRuntime, "_spawn", _unexpected)

    settings = _settings(work_dir, redis_url="redis://example.invalid:6399/2", redis_autostart=False)
    runtime = rr.RedisRuntime(settings)

    client = runtime.ensure()

    assert isinstance(client, _AliveRedis)
    assert _AliveRedis.instances[0].args[0] == "redis://example.invalid:6399/2", "url 要走 from_url"
    assert runtime.started_by_us is False, "外部 Redis 不该被标成「我们拉起来的」"
    assert runtime.server_path is None, "连外部 Redis 时不该有 server_path"
    # 幂等：第二次拿到的还是同一个客户端，而且没再建新的
    assert runtime.ensure() is client
    assert len(_AliveRedis.instances) == 1


def test_host_port_config_uses_plain_kwargs(work_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """没有 url 时走 host/port/db，并且带上 decode_responses 和超时。"""
    _AliveRedis.instances = []
    monkeypatch.setattr(rr.redis, "Redis", _AliveRedis)
    settings = _settings(work_dir, redis_port=6391, redis_db=3)
    runtime = rr.RedisRuntime(settings)

    runtime.ensure()

    kwargs = _AliveRedis.instances[0].kwargs
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 6391
    assert kwargs["db"] == 3
    assert kwargs["decode_responses"] is True
    assert kwargs["socket_connect_timeout"] > 0
    assert kwargs["socket_timeout"] > 0
    assert runtime.started_by_us is False


def test_redis_url_that_is_down_is_never_replaced_by_a_local_one(
    work_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """给了 URL 但连不上：报错走人，绝不偷偷下载/拉起本地 Redis 顶上。"""
    _AliveRedis.instances = []
    monkeypatch.setattr(rr.redis, "Redis", _DeadRedis)
    monkeypatch.setattr(rr.RedisRuntime, "_download_server", _unexpected)
    monkeypatch.setattr(rr.RedisRuntime, "_spawn", _unexpected)

    settings = _settings(work_dir, redis_url="redis://example.invalid:6399/0", redis_autostart=True)
    runtime = rr.RedisRuntime(settings)

    with pytest.raises(RuntimeError) as excinfo:
        runtime.ensure()

    assert "TONICUISC_REDIS_URL" in str(excinfo.value)
    assert runtime.started_by_us is False
    assert runtime.server_path is None


def test_autostart_disabled_reports_actionable_error(work_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """连不上 + 关掉自动拉起 → 报错里要明确告诉人用哪个环境变量。"""
    monkeypatch.setattr(rr.redis, "Redis", _DeadRedis)
    settings = _settings(work_dir, redis_autostart=False)
    runtime = rr.RedisRuntime(settings)

    with pytest.raises(RuntimeError) as excinfo:
        runtime.ensure()

    message = str(excinfo.value)
    assert "TONICUISC_REDIS_URL" in message
    assert "TONICUISC_REDIS_AUTOSTART" in message
    assert runtime.started_by_us is False


# ------------------------------------------------------------ ② 找 redis-server


def test_explicit_server_setting_wins(work_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SETTINGS.redis_server 优先级最高，压过 redis/<平台>/ 里的同名人。"""
    explicit = work_dir / "somewhere" / SERVER_NAME
    explicit.parent.mkdir(parents=True)
    explicit.write_bytes(b"")

    bin_dir = work_dir / "redis" / ("win" if os.name == "nt" else "linux")
    bin_dir.mkdir(parents=True)
    (bin_dir / SERVER_NAME).write_bytes(b"")

    runtime = rr.RedisRuntime(_settings(work_dir, redis_server=str(explicit)))
    assert runtime.find_server() == explicit.resolve()


def test_bin_dir_used_when_setting_empty(work_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """没显式指定时，用 redis_bin_dir（redis/win 或 redis/linux）里的可执行文件。"""
    bin_dir = work_dir / "redis" / ("win" if os.name == "nt" else "linux")
    bin_dir.mkdir(parents=True)
    fake = bin_dir / SERVER_NAME
    fake.write_bytes(b"")

    runtime = rr.RedisRuntime(_settings(work_dir))
    assert runtime.find_server() == fake.resolve()


def test_find_server_falls_back_to_path_then_none(work_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """bin 目录里没有就查 PATH；PATH 里也没有就返回 None（交给下载逻辑）。"""
    runtime = rr.RedisRuntime(_settings(work_dir))

    monkeypatch.setattr(rr.shutil, "which", lambda name: "/usr/bin/redis-server")
    assert runtime.find_server() == Path("/usr/bin/redis-server").resolve()

    monkeypatch.setattr(rr.shutil, "which", lambda name: None)
    assert runtime.find_server() is None

    # 显式指定的路径不存在时，不该直接炸，而是退回自动查找
    missing = work_dir / "nope" / "redis-server"
    assert rr.RedisRuntime(_settings(work_dir, redis_server=str(missing))).find_server() is None


# ------------------------------------------------------------- ③ 配置文件内容


def test_build_config_has_port_dir_and_appendonly(work_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = rr.RedisRuntime(_settings(work_dir, redis_port=6390, redis_appendonly=True))

    text = runtime.build_config()
    data_dir = (work_dir / "cache" / "redis").as_posix()

    assert "port 6390" in text
    assert f'dir "{data_dir}"' in text
    assert "appendonly yes" in text
    assert "appendfsync everysec" in text
    assert 'save ""' in text, "开了 AOF 就不用落 RDB"
    # 只兼容 Redis 5 有的指令：这几个都得在
    assert "bind 127.0.0.1" in text
    assert "daemonize no" in text
    assert "protected-mode no" in text
    assert f'logfile "{data_dir}/redis.log"' in text
    assert "\\" not in text.split('dir "')[1].split('"')[0], "Windows 路径要写成正斜杠，别用反斜杠"


def test_build_config_without_appendonly_keeps_rdb(work_dir: Path) -> None:
    runtime = rr.RedisRuntime(_settings(work_dir, redis_appendonly=False))

    text = runtime.build_config()

    assert "appendonly no" in text
    assert 'save ""' not in text, "关掉 AOF 时要留给 RDB，不然一点持久化都没有"


# ------------------------------------------------------------------ ④ stop


def test_stop_is_safe_and_idempotent_without_anything_started(work_dir: Path) -> None:
    runtime = rr.RedisRuntime(_settings(work_dir))

    runtime.stop()
    runtime.stop()

    assert runtime.started_by_us is False
    assert runtime.server_path is None


def test_stop_only_kills_our_own_process(work_dir: Path) -> None:
    runtime = rr.RedisRuntime(_settings(work_dir))

    # 我们拉起来的：要关（这里模拟它不理 SHUTDOWN，所以必须 terminate）
    ours = _FakeProcess(exits_on_wait=False)
    runtime._process = ours
    runtime.started_by_us = True
    runtime.stop()
    assert ours.terminated is True
    assert ours.killed is False, "正常 terminate 就够了，不该上 kill"
    assert runtime.started_by_us is False

    # 别人的（复用的 / 外部的）：绝对不碰
    theirs = _FakeProcess()
    runtime._process = theirs
    runtime.started_by_us = False
    runtime.stop()
    assert theirs.terminated is False, "绝不能关用户自己的 Redis"
    assert theirs.killed is False


def test_stop_asks_redis_to_shutdown_before_killing(work_dir: Path) -> None:
    """先请 Redis 自己退出（会把 AOF 刷盘），自己退了就别再硬杀。

    直接 terminate 是 TerminateProcess，``appendfsync everysec`` 下最多丢 1 秒写入，
    用户「点完喜欢就关服务」正好撞上。
    """
    runtime = rr.RedisRuntime(_settings(work_dir))
    fake_client = _ShutdownRedis()
    graceful = _FakeProcess(exits_on_wait=True)
    runtime._process = graceful
    runtime._client = fake_client
    runtime.started_by_us = True

    runtime.stop()

    assert fake_client.shutdown_called is True, "要先发 SHUTDOWN 让 Redis 把 AOF 落地"
    assert graceful.exited is True
    assert graceful.terminated is False, "它自己退了就不该再 terminate"
    assert graceful.killed is False


def test_stop_after_failed_start_is_noop(work_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rr.redis, "Redis", _DeadRedis)
    settings = _settings(work_dir, redis_autostart=False)
    runtime = rr.RedisRuntime(settings)
    with pytest.raises(RuntimeError):
        runtime.ensure()

    runtime.stop()
    assert runtime.ping() is False


# ---------------------------------------------------------- ⑤ 自动下载与拉起


def test_ensure_downloads_then_starts_when_nothing_found(
    work_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """找不到 redis-server 且允许下载：下载 → 拉起 → PING 通 → 标记成「我们的」。"""
    downloaded = work_dir / "redis" / ("win" if os.name == "nt" else "linux") / SERVER_NAME
    spawned: list[Path] = []
    process = _FakeProcess()
    _ToggleRedis.alive = False
    _ToggleRedis.instances = []

    monkeypatch.setattr(rr.shutil, "which", lambda name: None)
    monkeypatch.setattr(rr.redis, "Redis", _ToggleRedis)
    monkeypatch.setattr(rr.RedisRuntime, "_download_server", lambda self: downloaded)

    def fake_spawn(self: rr.RedisRuntime, server: Path) -> _FakeProcess:
        spawned.append(server)
        _ToggleRedis.alive = True  # 子进程起来了，端口就通了
        return process

    monkeypatch.setattr(rr.RedisRuntime, "_spawn", fake_spawn)

    runtime = rr.RedisRuntime(_settings(work_dir, redis_download=True))
    client = runtime.ensure()

    assert spawned == [downloaded], "应该用下载出来的那个二进制去启动"
    assert runtime.started_by_us is True
    assert runtime.server_path == downloaded
    assert client.ping() is True


def test_autostart_without_download_gives_manual_instructions(
    work_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """找不到二进制又不许下载：报错要给出可操作的手动办法。"""
    monkeypatch.setattr(rr.shutil, "which", lambda name: None)
    monkeypatch.setattr(rr.redis, "Redis", _DeadRedis)

    runtime = rr.RedisRuntime(_settings(work_dir, redis_download=False))
    with pytest.raises(RuntimeError) as excinfo:
        runtime.ensure()

    message = str(excinfo.value)
    assert "TONICUISC_REDIS_DOWNLOAD" in message
    assert "TONICUISC_REDIS_SERVER" in message


def test_download_failure_message_is_actionable(work_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """下载失败（断网 / 没有 gcc）时给出中文的手动处理办法，而不是抛底层异常。"""

    def boom(url: str, dest: Path) -> Path:
        raise OSError("network unreachable")

    monkeypatch.setattr(rr, "_download_file", boom)
    runtime = rr.RedisRuntime(_settings(work_dir, redis_download=True))

    with pytest.raises(RuntimeError) as excinfo:
        runtime._download_server()

    message = str(excinfo.value)
    assert "手动" in message or "apt install redis-server" in message


# ----------------------------------------------------------- ⑥ 公开 API 形状


def test_module_public_api() -> None:
    """公开 API 的形状要跟约定的签名一致（storage/main 都按这个调）。"""
    assert isinstance(rr.RUNTIME, rr.RedisRuntime)
    assert callable(rr.ensure_redis_client)
    assert callable(rr.shutdown_redis)
    # 下载地址都必须是 https 常量，方便 README 和排查对齐
    assert rr.WINDOWS_ZIP_URL.startswith("https://")
    assert rr.LINUX_DEB_URL.startswith("https://")
    assert rr.LINUX_SOURCE_URL.startswith("https://")


def _unexpected(*args: object, **kwargs: object) -> object:
    raise AssertionError("这条路径不该被走到")
