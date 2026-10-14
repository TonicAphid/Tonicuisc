"""服务端控制台（那个 `>` 提示符）：命令解析、执行与提示符补画。不联网、不动真 Redis。"""

from __future__ import annotations

import io
import logging
import sys
import time
from types import SimpleNamespace

import pytest

from conftest import make_storage
from tonicuisc_server.cli import commands as cli_commands
from tonicuisc_server.cli import console as cli
from tonicuisc_server.cli.console import console_command as _console_command
from tonicuisc_server.cli.console import resolve_command as _resolve_command
from tonicuisc_server.auth import AuthManager


@pytest.fixture()
def store():
    s = make_storage()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def server():
    return SimpleNamespace(should_exit=False)


# ------------------------------------------------------------------ 解析
def test_resolve_exact_alias_and_name() -> None:
    assert _resolve_command("h") == ("help", [])
    assert _resolve_command("?") == ("help", [])
    assert _resolve_command("help") == ("help", [])
    assert _resolve_command("HELP") == ("help", [])  # 大小写不挑
    assert _resolve_command("q") == ("stop", [])
    assert _resolve_command("s") == ("stop", [])
    assert _resolve_command("cc") == ("clear", [])
    assert _resolve_command("c") == ("code", [])  # 简写优先，别被前缀匹配抢走


def test_resolve_unique_prefix_and_ambiguity() -> None:
    assert _resolve_command("li") == ("list", [])
    assert _resolve_command("cl") == (None, ["cleanup", "clear"])  # 歧义时把候选报出来
    assert _resolve_command("zzz") == (None, [])


# ------------------------------------------------------------------ 执行
def test_help_prints_every_command(server, capsys) -> None:
    assert _console_command("h", server, None) is False
    out = capsys.readouterr().out
    for name in ("list", "revoke", "password", "code", "cleanup", "redis", "clear", "log", "stop"):
        assert name in out, f"帮助里少了 {name}"


def test_unknown_and_ambiguous_hints(server, capsys) -> None:
    _console_command("blah", server, None)
    assert "不认识" in capsys.readouterr().out

    _console_command("cl", server, None)
    out = capsys.readouterr().out
    assert "cleanup" in out and "clear" in out, "歧义要列出候选"


def test_stop_sets_flag(server) -> None:
    assert _console_command("q", server, None) is True
    assert server.should_exit is True


def test_list_and_cleanup_on_empty_store(server, store, capsys) -> None:
    assert _console_command("l", server, store) is False
    assert "还没有已登录设备" in capsys.readouterr().out

    assert _console_command("x", server, store) is False
    assert "已删除无账户设备" in capsys.readouterr().out


def test_clear_cache_and_missing_args(server, store, capsys) -> None:
    assert _console_command("cc", server, store) is False
    assert "已清掉" in capsys.readouterr().out

    assert _console_command("r", server, store) is False
    assert "用法" in capsys.readouterr().out

    assert _console_command("p", server, store) is False
    assert "用法" in capsys.readouterr().out


def test_password_command_wires_through(server, store, monkeypatch, capsys) -> None:
    auth = AuthManager(store, ttl=300)
    auth.register("aphid", "hunter2x")

    answers = iter(["brandnew9", "brandnew9"])
    monkeypatch.setattr(cli_commands, "getpass", SimpleNamespace(getpass=lambda prompt="": next(answers)))

    assert _console_command("p aphid", server, store) is False
    assert auth.authenticate("aphid", "brandnew9") is not None
    assert auth.authenticate("aphid", "hunter2x") is None, "旧密码要失效"
    assert "已重置" in capsys.readouterr().out


def test_log_toggle(server) -> None:
    access = logging.getLogger("uvicorn.access")
    original = access.level
    try:
        assert _console_command("log off", server, None) is False
        assert access.level > logging.INFO, "关掉后不该再吐 INFO"

        assert _console_command("log on", server, None) is False
        assert access.level == logging.INFO

        assert _console_command("log", server, None) is False  # 缺参数 → 给用法
    finally:
        access.setLevel(original)


# ------------------------------------------------------------------ 循环
def test_console_loop_runs_until_stop(monkeypatch, capsys) -> None:
    import builtins

    server = SimpleNamespace(started=True, should_exit=False)
    lines = iter(["h", "", "q"])  # 帮助 → 空行跳过 → 停服
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(lines))

    prompt = cli.PromptingStdout(sys.stdout)  # 此时 sys.stdout 是 capsys 的捕获流
    try:
        cli.console_loop(server, prompt)
    finally:
        prompt.close()

    assert server.should_exit is True, "q 要把 should_exit 立起来"
    out = capsys.readouterr().out
    assert "控制台就绪" in out and "revoke" in out, "启动提示和 h 的帮助都要在"


def test_console_loop_exits_on_eof_without_stopping_server(monkeypatch, capsys) -> None:
    import builtins

    server = SimpleNamespace(started=True, should_exit=False)

    def eof(prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr(builtins, "input", eof)

    prompt = cli.PromptingStdout(sys.stdout)
    try:
        cli.console_loop(server, prompt)
    finally:
        prompt.close()

    assert server.should_exit is False, "stdin 关掉只是没输入可读，不该把服务停了"


# ------------------------------------------------------------------ 提示符补画
def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_prompt_redraws_after_logs_and_multiline_output() -> None:
    real = io.StringIO()
    out = cli.PromptingStdout(real)
    try:
        out.enabled = True
        out.begin_wait()
        assert _wait_until(lambda: real.getvalue().endswith(cli.PROMPT)), "等输入时要画出 >"

        # 日志来了：内容接在提示符后面，安静一小会儿后 > 补回行尾
        out.write("[access] POST /api/library/like 200 0.004s\n")
        assert _wait_until(lambda: real.getvalue().endswith(cli.PROMPT)), "日志一来 > 不能消失"
        assert real.getvalue().count(cli.PROMPT) == 2, "一轮输出只补一个 >"

        # 一次打多行（比如 h 的帮助）只在末尾补一个，不是每行都缀个 >
        out.write("line1\nline2\nline3\n")
        assert _wait_until(lambda: real.getvalue().endswith(cli.PROMPT))
        assert real.getvalue().count(cli.PROMPT) == 3, "多行输出只该在末尾补画一次"

        # 敲完回车（end_wait）再回到等输入（begin_wait）→ 提示符要重新出现
        out.end_wait()
        out.begin_wait()
        assert _wait_until(lambda: real.getvalue().count(cli.PROMPT) == 4), "空输入回车后 > 也要回来"
    finally:
        out.close()


def test_prompt_does_not_draw_while_executing() -> None:
    real = io.StringIO()
    out = cli.PromptingStdout(real)
    try:
        out.enabled = True
        out.end_wait()  # 命令执行期间：不是「等输入」状态
        out.write("命令的输出\n")
        time.sleep(0.2)  # 给足补画线程的两个周期
        assert real.getvalue() == "命令的输出\n", "执行命令期间不许往行尾塞 >"
    finally:
        out.close()


class _RecordingStream(io.StringIO):
    """记 flush 次数的 stdout 替身——提示符不 flush，行缓冲会把它压到后面输出的顶上。"""

    def __init__(self) -> None:
        super().__init__()
        self.flushes = 0

    def flush(self) -> None:
        self.flushes += 1
        super().flush()


def test_prompt_is_drawn_immediately_and_flushed() -> None:
    rec = _RecordingStream()
    out = cli.PromptingStdout(rec)
    try:
        out.enabled = True
        out.begin_wait()
        assert rec.getvalue() == cli.PROMPT, "回到等输入要同步立刻画出 >，不能等补画线程"
        assert rec.flushes >= 1, "画完必须 flush，否则终端行缓冲会让 > 不出现在输入处"
        out.write("一行日志\n")
        assert rec.flushes >= 2, "write 同样要 flush"
        assert _wait_until(lambda: rec.getvalue().endswith(cli.PROMPT)), "日志过后 > 要补回行尾"
    finally:
        out.close()
