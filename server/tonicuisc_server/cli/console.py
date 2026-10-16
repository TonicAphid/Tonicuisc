"""服务控制台：底部常驻的 ``>`` 提示符 + 命令派发。

服务起来后，如果 stdin 是控制台，就在后台线程里挂一个提示符（像 MC 服务端那样）：

    h  帮助          l   看设备和账户        r <ID>  吊销设备（ID 可只输前几位）
    p <用户> 重置密码  c <用户> 生成恢复码    x   清理无主 / 已吊销的设备记录
    rd  Redis 状态    cc  清搜索缓存          env 看当前环境   acme 看证书状态
    log off 关掉请求日志                      q   停止服务

命令表就是本模块的 ``COMMANDS``（唯一出处，``h`` 直接遍历它打印）。
"""

from __future__ import annotations

import logging
import sys
import threading
import time

from . import commands

PROMPT = "> "

#: 擦掉当前行：回车 + 清到行尾。
#: 日志来的时候要先把已经画出的 ``> `` 擦掉，否则会拼成 ``> [timing] ...``（实测踩过）。
CLEAR_LINE = "\r\033[K"


def enable_ansi(stream) -> bool:
    """尽量让终端认 ANSI 转义序列；返回是否可用。

    Windows 10+ 的 conhost / Windows Terminal 都支持，但**默认不一定开**
    （``ENABLE_VIRTUAL_TERMINAL_PROCESSING``）。不开的话 ``\\033[K`` 会被原样打出来，
    比不擦行还难看，所以要先探测、失败了就退回「只补画、不擦除」。
    """
    if not hasattr(stream, "isatty") or not stream.isatty():
        return False
    if not hasattr(stream, "fileno"):
        return False
    try:
        if sys.platform != "win32":
            return True
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        if mode.value & 0x0004:  # 已经开了
            return True
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


class PromptingStdout:
    """包一层 ``sys.stdout``：控制台空闲时，任何输出之后把 ``>`` 补画回行尾。

    uvicorn 的 ``INFO:`` 行、应用自己 ``print`` 的 ``[access]`` / ``[timing]`` 都走这层；
    用户敲字的回显走控制台驱动、不经过这层，所以不会被打断。

    **日志来时先把已画出的提示符擦掉**（``\\r`` + 清行），再写日志，最后重画 ``>``。
    不擦的话日志会直接接在提示符后面，变成 ``> [timing] ...``——看着像提示符被吃掉了。

    补画时机：回到等输入时**立刻**画一个；等输入期间来了日志，则安静一小会儿（60ms）
    后补画——这样 ``h`` 那种一次打十来行的输出只在末尾补一个，连续几条日志也只补一次。
    **每次画完必须 flush**：提示符没有换行符，stdout 在终端是行缓冲的，不 flush 它会
    一直憋在缓冲区，等下一段输出的换行才一起冲出去——表现就是 ``>`` 跑到后面输出的
    顶上、而不是待在输入的地方（踩过一次，别改回去）。
    """

    def __init__(self, real) -> None:
        self.real = real
        self.enabled = False  # 控制台循环起来才开始补画，启动日志期间不插嘴
        self.waiting = False  # 正卡在 input() 里等输入
        self._drawn = False  # 这一轮的提示符已经画过了（防重影）
        self._pending = False  # 有输出还没补画
        self._last_output = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        #: 能不能用 ANSI 擦行；不行就只补画（宁可多个 > 也不要打印出乱码）
        self._ansi = enable_ansi(real)
        self._thread = threading.Thread(target=self._watch, daemon=True, name="console-prompt")
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)

    # ------------------------------------------------------------ sys.stdout 接口
    def write(self, text: str) -> int:
        with self._lock:
            # 提示符已经画在行尾了：先把它「收掉」，日志才能独占一行。
            # 不处理的话就是 `> [timing] ...`，看着像提示符被吃掉了（用户反馈过）。
            #
            # 两种收法：
            #   · 支持 ANSI → 擦掉整行（最干净，提示符原地消失、日志顶上来）
            #   · 不支持   → 先换行（会在上一行留一个 `> `，但日志至少是独立的行）
            # 后者是保底：宁可留个孤零零的 `> `，也不要日志跟提示符糊在一起。
            if self.enabled and self._drawn and text:
                self.real.write(CLEAR_LINE if self._ansi else "\n")
                self._drawn = False
            self.real.write(text)
            self.real.flush()  # 别让输出（尤其没换行的）在缓冲区里跟提示符换了位次
            if self.enabled:
                self._drawn = False
                self._pending = True
                self._last_output = time.monotonic()
        return len(text)

    def flush(self) -> None:
        with self._lock:
            self.real.flush()

    def __getattr__(self, name):  # encoding / fileno / isatty / line_buffering … 透传
        return getattr(self.real, name)

    # ------------------------------------------------------------ 循环侧的进出
    def begin_wait(self) -> None:
        with self._lock:
            self.waiting = True
            if self.enabled and not self._drawn:
                # 回到等输入就**立刻**画（不等安静期）：敲完回车 prompt 要马上出现在
                # 输入处，不然手快的人会在没有 > 的地方先敲进字去
                self.real.write(PROMPT)
                self.real.flush()
                self._drawn = True
            self._pending = False

    def end_wait(self) -> None:
        with self._lock:
            self.waiting = False
            self._drawn = False
            self._pending = False

    # ------------------------------------------------------------ 补画线程
    def _watch(self) -> None:
        while not self._stop.wait(0.05):
            with self._lock:
                if (
                    self.enabled
                    and self.waiting
                    and self._pending
                    and not self._drawn
                    and time.monotonic() - self._last_output >= 0.06
                ):
                    self.real.write(PROMPT)
                    self.real.flush()  # 同上：不 flush 的 > 会憋到下段输出的顶上
                    self._drawn = True
                    self._pending = False


#: 命令表：全名 -> (简写集合, 参数, 一句话说明)。简写随便挑好敲的，h / q 最要紧。
COMMANDS: dict[str, tuple[tuple[str, ...], str, str]] = {
    "help": (("h", "?"), "", "这张表"),
    "list": (("l", "ls"), "", "看设备和账户"),
    "revoke": (("r", "rv"), "<设备ID>", "吊销设备（ID 太长可以只输前几位，唯一就行）"),
    "password": (("p", "pw"), "<用户名>", "重置密码（不用旧密码，交互输两次新的）"),
    "code": (("c", "rc"), "<用户名>", "生成恢复码（旧的作废，明文只显示一次）"),
    "cleanup": (("x", "clean"), "", "清理无主 / 已吊销的设备记录"),
    "redis": (("rd",), "", "看 Redis 状态"),
    "clear": (("cc",), "", "清掉搜索结果缓存"),
    "env": ((), "", "看当前环境（切环境要停服务后跑 env <uisc|tuisc>）"),
    "acme": ((), "", "看证书状态（签发：acme --issue）"),
    "log": ((), "on|off", "开 / 关请求日志（关掉控制台更清净）"),
    "stop": (("q", "quit", "s"), "", "停止服务"),
}


def resolve_command(token: str) -> tuple[str | None, list[str]]:
    """把敲进去的词认成命令：先精确（简写或全名都行），再唯一前缀。

    返回 ``(命令名, 候选)``：认出来时候选为空；没认出来时命令名是 None，
    候选是能前缀匹配上的命令（给「输长一点」的提示用）。
    """
    token = (token or "").strip().lower()
    for name, (aliases, _usage, _help) in COMMANDS.items():
        if token == name or token in aliases:
            return name, []
    hits = [name for name in COMMANDS if name.startswith(token)]
    return (None, hits) if len(hits) != 1 else (hits[0], [])


def print_help() -> None:
    print("命令（简写 / 全名都行，唯一前缀也行）：")
    for name, (aliases, usage, help_text) in COMMANDS.items():
        cmd = name + (f" {usage}" if usage else "")
        short = "|".join(aliases) if aliases else "-"
        print(f"  {short:<9}{cmd:<21}{help_text}")


def set_access_log(args: list[str]) -> int:
    """uvicorn 的请求日志开 / 关（关掉后控制台只剩应用自己的输出，提示符不会被刷走）。"""
    state = (args[0].lower() if args else "")
    access = logging.getLogger("uvicorn.access")
    if state in {"off", "0", "close"}:
        access.setLevel(logging.WARNING)
        print("请求日志已关（uvicorn.access）")
        return 0
    if state in {"on", "1", "open"}:
        access.setLevel(logging.INFO)
        print("请求日志已开（uvicorn.access）")
        return 0
    print("用法: log on / log off")
    return 2


def console_command(line: str, server, store) -> bool:
    """执行一条控制台命令；返回 True 表示「停服务」，循环该收尾了。"""
    parts = line.split()
    name, candidates = resolve_command(parts[0])
    if name is None:
        if candidates:
            print(f"「{parts[0]}」对应好几个命令：{', '.join(candidates)}，输长一点")
        else:
            print("不认识这个命令，输 h 看命令列表")
        return False

    args = parts[1:]
    if name == "help":
        print_help()
    elif name == "list":
        commands.devices_command(store)
    elif name == "revoke":
        if not args:
            print("用法: r <设备ID>（可以只输前几位，唯一就行）")
        else:
            commands.revoke_command(store, args[0])
    elif name in {"password", "code"}:
        if not args:
            print(f"用法: {'p' if name == 'password' else 'c'} <用户名>")
        else:
            commands.account_command(store, "set-password" if name == "password" else "recovery-code", args[0])
    elif name == "cleanup":
        commands.cleanup_command(store)
    elif name == "redis":
        commands.redis_command()
    elif name == "clear":
        commands.clear_cache_command(store)
    elif name == "env":
        # 只读：切环境要写 .env 且重启才生效，在服务跑着的时候切很容易把人搞糊涂
        commands.env_command([])
    elif name == "acme":
        commands.acme_command(["--status"])
    elif name == "log":
        set_access_log(args)
    elif name == "stop":
        print("正在停止服务…")
        server.should_exit = True
        return True
    return False


def console_loop(server, prompt: PromptingStdout) -> None:
    """后台线程：服务起来后在控制台挂一个 ``>`` 提示符，直到 ``q`` / Ctrl+C / stdin 关闭。

    放后台线程是为了让 uvicorn 留在主线程（信号处理、Ctrl+C 都归它），
    它挂了这线程是 daemon，跟着一起退出。提示符的补画交给 ``prompt``
    （见 ``PromptingStdout``）——这里只管「等输入 / 输入回来了」两个时机。
    """
    from ..storage import Storage

    for _ in range(300):  # 最多等 30 秒让服务完成启动，别把 > 打在启动日志中间
        if server.started or server.should_exit:
            break
        time.sleep(0.1)
    if server.should_exit:
        return

    store = Storage()
    prompt.enabled = True
    print("控制台就绪：输 h 看命令，q 停止服务")  # 结尾换行，> 由补画线程接上
    try:
        while not server.should_exit:
            prompt.begin_wait()
            try:
                line = input()  # 提示符不在这画：等输入的期间由补画线程维护
            except (EOFError, OSError):
                return  # stdin 不可交互（被管道 / 重定向了），安静收工
            except KeyboardInterrupt:
                prompt.end_wait()
                print()
                server.should_exit = True
                return
            prompt.end_wait()
            if not line.strip():
                continue
            try:
                if console_command(line, server, store):
                    return
            except Exception as exc:  # 单条命令炸了不能把服务一起带走
                print(f"命令出错：{type(exc).__name__}: {exc}")
    finally:
        store.close()


def attach(server) -> PromptingStdout | None:
    """stdin 是终端就装上提示符（必须在 uvicorn.Config 之前调，见下），返回包装流。"""
    if sys.stdin is None or not sys.stdin.isatty():
        return None
    # 必须赶在 uvicorn.Config 之前装：uvicorn 的日志 stream 在构造时按
    # ext://sys.stdout 解析，装晚了它的 INFO 行就绕过补画、日志一来 > 又没了。
    prompt = PromptingStdout(sys.stdout)
    sys.stdout = prompt
    threading.Thread(target=console_loop, args=(server, prompt), daemon=True).start()
    return prompt


def detach(prompt: PromptingStdout | None) -> None:
    if prompt is None:
        return
    prompt.enabled = False
    sys.stdout = prompt.real
    prompt.close()
