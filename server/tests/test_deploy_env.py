"""环境选择 / .env 加载 / Caddy 配置生成 / DDNS 辅助 / ACME 签证书。不联网、不装任何东西。"""

from __future__ import annotations

import os
import platform
from pathlib import Path

import pytest

from tonicuisc_server.core import acme
from tonicuisc_server.core import caddy
from tonicuisc_server.core import ddns
from tonicuisc_server.core import environment as env_mod


def _scratch_dir() -> Path:
    """不用 pytest 的 tmp_path：这个环境的系统临时目录读不了（权限问题）。"""
    import uuid

    path = Path(__file__).resolve().parent / "_scratch" / "deploy" / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    return path


def _pem(label: str) -> str:
    """造一段长得像证书的 PEM（只为数 BEGIN CERTIFICATE，不做真解析）。"""
    return f"-----BEGIN CERTIFICATE-----\n{label}\n-----END CERTIFICATE-----\n"


def _fake_settings(
    root: Path,
    *,
    host: str = "0.0.0.0",
    port: int = 8000,
    acme_dns01: bool = False,
    https_port: int = 443,
):
    """一个够用的假 Settings：caddy_dir / caddy_config / host / port / https_port / acme_*。"""
    from types import SimpleNamespace

    return SimpleNamespace(
        caddy_dir=root,
        caddy_config=root / "Caddyfile",
        caddy_download=False,
        caddy_autostart=True,
        acme_dns01=acme_dns01,
        acme_email="",
        acme_ca="",
        host=host,
        port=port,
        https_port=https_port,
    )


# ------------------------------------------------------------------ .env 解析
def test_parse_env_file_handles_quotes_comments_and_bom() -> None:
    text = (
        "\ufeff# 注释行\n"
        "TONICUISC_HOST=127.0.0.1\n"
        'TONICUISC_PUBLIC_URL="https://uisc.tonicaphid.cn"\n'
        "export TONICUISC_PORT=8123\n"
        "\n"
        "没有等号的行\n"
        "TONICUISC_SOURCES=migu,kuwo\n"
    )
    values = env_mod.parse_env_file(text)
    assert values["TONICUISC_HOST"] == "127.0.0.1"
    # BOM 不能把第一行的 key 污染成 \ufeffTONICUISC_HOST
    assert "\ufeffTONICUISC_HOST" not in values
    assert values["TONICUISC_PUBLIC_URL"] == "https://uisc.tonicaphid.cn"
    assert values["TONICUISC_PORT"] == "8123"
    assert values["TONICUISC_SOURCES"] == "migu,kuwo"
    assert "没有等号的行" not in values


def test_load_env_file_does_not_override_real_env(monkeypatch) -> None:
    import shutil

    root = _scratch_dir()
    try:
        path = root / "x.env"
        path.write_text("TONICUISC_HOST=from_file\n", encoding="utf-8")
        monkeypatch.setenv("TONICUISC_HOST", "from_env")
        env_mod.load_env_file(path)
        assert os.environ["TONICUISC_HOST"] == "from_env", "真环境变量优先，别被文件覆盖"

        monkeypatch.delenv("TONICUISC_HOST")
        env_mod.load_env_file(path)
        assert os.environ["TONICUISC_HOST"] == "from_file"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_write_env_value_replaces_only_that_line() -> None:
    import shutil

    root = _scratch_dir()
    try:
        path = root / ".env"
        path.write_text("# 手写的注释\nTONICUISC_ENV=tuisc\nTONICUISC_PORT=8000\n", encoding="utf-8")
        env_mod.write_env_value("TONICUISC_ENV", "uisc", path)
        text = path.read_text(encoding="utf-8")
        assert "TONICUISC_ENV=uisc" in text
        assert "TONICUISC_PORT=8000" in text, "别的行不能被冲掉"
        assert "# 手写的注释" in text, "注释也要留着"

        # 原来没有的 key → 追加
        env_mod.write_env_value("TONICUISC_THREADS", "64", path)
        assert "TONICUISC_THREADS=64" in path.read_text(encoding="utf-8")
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ------------------------------------------------------------------ 环境选择
def test_environments_have_distinct_domains() -> None:
    uisc = env_mod.ENVIRONMENTS["uisc"]
    tuisc = env_mod.ENVIRONMENTS["tuisc"]
    assert uisc.domain == "uisc.tonicaphid.cn"
    assert tuisc.domain == "tuisc.tonicaphid.cn"
    assert uisc.public_url == "https://uisc.tonicaphid.cn"
    assert uisc.is_production and not tuisc.is_production


def test_get_environment_falls_back_to_default(monkeypatch) -> None:
    monkeypatch.setenv(env_mod.ENV_KEY, "不存在的环境")
    assert env_mod.get_environment().key == env_mod.DEFAULT_ENV_KEY
    monkeypatch.setenv(env_mod.ENV_KEY, "tuisc")
    assert env_mod.get_environment().key == "tuisc"


def test_apply_environment_syncs_public_url(monkeypatch) -> None:
    """切环境时 PUBLIC_URL 必须跟着走（这里踩过坑：旧域名会残留）。"""
    import shutil

    root = _scratch_dir()
    try:
        monkeypatch.delenv("TONICUISC_PUBLIC_URL", raising=False)
        path = root / ".env"
        # 别写真的 server/.env：指到 scratch
        monkeypatch.setattr(env_mod, "ENV_PATH", path)

        env_mod.apply_environment(env_mod.ENVIRONMENTS["tuisc"], persist=False)
        assert os.environ["TONICUISC_PUBLIC_URL"] == "https://tuisc.tonicaphid.cn"

        env_mod.apply_environment(env_mod.ENVIRONMENTS["uisc"], persist=False)
        assert os.environ["TONICUISC_PUBLIC_URL"] == "https://uisc.tonicaphid.cn"

        env_mod.apply_environment(env_mod.ENVIRONMENTS["tuisc"], persist=True)
        written = path.read_text(encoding="utf-8")
        assert "TONICUISC_ENV=tuisc" in written, written
        assert "TONICUISC_PUBLIC_URL=https://tuisc.tonicaphid.cn" in written, written
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ------------------------------------------------------------------ Caddy
def test_download_candidates_order(monkeypatch) -> None:
    source = "https://github.com/a/b/releases/download/v1/x.zip"

    monkeypatch.delenv("TONICUISC_GH_PROXY", raising=False)
    default = caddy.download_candidates(source)
    assert default[0].startswith("https://gh-proxy.com/"), "默认先走镜像"
    assert default[-1] == source, "直连兜底放最后"
    assert all(candidate.endswith(source) for candidate in default)

    monkeypatch.setenv("TONICUISC_GH_PROXY", "https://my-mirror.example/")
    custom = caddy.download_candidates(source)
    assert custom[0] == "https://my-mirror.example/" + source
    assert custom[-1] == source

    monkeypatch.setenv("TONICUISC_GH_PROXY", "")
    assert caddy.download_candidates(source) == [source], "空串 = 只直连"


def test_binary_name_and_dir_follow_platform(monkeypatch) -> None:
    assert caddy.binary_name() == ("caddy.exe" if os.name == "nt" else "caddy")
    assert caddy.bin_dir().name == ("win" if os.name == "nt" else "linux")


def test_generate_config_uses_environment_domain() -> None:
    """生成的配置必须用当前环境的域名，且反代目标不能是 0.0.0.0。"""
    import shutil

    root = _scratch_dir()
    try:
        (root / "Caddyfile.template").write_text(
            "{DOMAIN} {\n\treverse_proxy {UPSTREAM}\n\tlog {\n\t\toutput file {LOG}\n\t}\n}\n",
            encoding="utf-8",
        )
        settings = _fake_settings(root)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(caddy, "SETTINGS", settings)
            patch.setattr(caddy, "ENVIRONMENT", env_mod.ENVIRONMENTS["tuisc"])

            generated = caddy.generate_config()
            text = generated.read_text(encoding="utf-8")
            assert "tuisc.tonicaphid.cn {" in text
            assert "reverse_proxy 127.0.0.1:8000" in text, "0.0.0.0 是监听地址，不能拿去连"
            assert "0.0.0.0" not in text

            # 内容没变就不重写（避免无谓 reload）
            before = generated.stat().st_mtime_ns
            caddy.generate_config()
            assert generated.stat().st_mtime_ns == before
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_generate_config_uses_nonstandard_https_port() -> None:
    """住宅宽带 443 被运营商封了就换非标端口：站点地址要变成「域名:8443」。"""
    import shutil

    root = _scratch_dir()
    try:
        (root / "Caddyfile.template").write_text(
            "{SITE} {\n\treverse_proxy {UPSTREAM}\n}\n",
            encoding="utf-8",
        )

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(caddy, "SETTINGS", _fake_settings(root, https_port=8443))
            patch.setattr(caddy, "ENVIRONMENT", env_mod.ENVIRONMENTS["tuisc"])
            text = caddy.generate_config().read_text(encoding="utf-8")
            assert "tuisc.tonicaphid.cn:8443 {" in text
            assert "tuisc.tonicaphid.cn {" not in text

        # 默认 443 时不带端口（换了端口值要能重新生成）
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(caddy, "SETTINGS", _fake_settings(root, https_port=443))
            patch.setattr(caddy, "ENVIRONMENT", env_mod.ENVIRONMENTS["tuisc"])
            text = caddy.generate_config().read_text(encoding="utf-8")
            assert "tuisc.tonicaphid.cn {" in text
            assert ":8443" not in text
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_generate_config_keeps_handwritten_file() -> None:
    """没有模板但有手写 Caddyfile 时，别覆盖用户的东西。"""
    import shutil

    root = _scratch_dir()
    try:
        target = root / "Caddyfile"
        target.write_text("example.com { respond ok }\n", encoding="utf-8")
        settings = _fake_settings(root, host="127.0.0.1")

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(caddy, "SETTINGS", settings)
            assert caddy.generate_config() == target
        assert "example.com" in target.read_text(encoding="utf-8")
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ------------------------------------------------------------------ DDNS
def test_every_to_minutes() -> None:
    assert ddns._every_to_minutes("10m") == 10
    assert ddns._every_to_minutes("2h") == 120
    assert ddns._every_to_minutes("30") == 30
    with pytest.raises(SystemExit):
        ddns._every_to_minutes("abc")
    with pytest.raises(SystemExit):
        ddns._every_to_minutes("0m")


def test_record_for_environment_follows_env(monkeypatch) -> None:
    monkeypatch.setattr(ddns, "ENVIRONMENT", env_mod.ENVIRONMENTS["uisc"])
    assert ddns.record_for_environment() == "uisc.tonicaphid.cn"
    monkeypatch.setattr(ddns, "ENVIRONMENT", env_mod.ENVIRONMENTS["tuisc"])
    assert ddns.record_for_environment() == "tuisc.tonicaphid.cn"


def test_ddns_install_requires_aliyun_key(monkeypatch) -> None:
    """没配 AK 时要明确提示，而不是装个跑不起来的任务。"""
    monkeypatch.setattr(ddns, "_load_aliyun_env", lambda: {})
    monkeypatch.delenv("ALIYUN_ACCESS_KEY_ID", raising=False)

    class FakeSettings:
        caddy_dir = _scratch_dir()

    monkeypatch.setattr(ddns, "SETTINGS", FakeSettings)
    assert ddns.install([]) == 2


def test_ddns_script_path_points_into_deploy(monkeypatch) -> None:
    class FakeSettings:
        caddy_dir = Path("/tmp/deploy")

    monkeypatch.setattr(ddns, "SETTINGS", FakeSettings)
    assert ddns.script_path().name == "update_dns.py"
    assert ddns.script_path().parent.name == "deploy"


def test_platform_branch_is_chosen(monkeypatch) -> None:
    """确认按系统选调度器：Windows 走 schtasks、其它走 systemd/cron。"""
    calls: list[str] = []
    monkeypatch.setattr(ddns, "_load_aliyun_env", lambda: {"ALIYUN_ACCESS_KEY_ID": "x", "ALIYUN_ACCESS_KEY_SECRET": "y"})
    monkeypatch.setattr(ddns, "script_path", lambda: Path(__file__))
    monkeypatch.setattr(ddns, "_install_windows", lambda minutes, record, only="both": calls.append(f"win:{minutes}:{record}:{only}") or 0)
    monkeypatch.setattr(ddns, "_install_linux", lambda minutes, record, only="both": calls.append(f"linux:{minutes}:{record}:{only}") or 0)

    ddns.install(["--every", "15m", "--record", "tuisc.tonicaphid.cn"])
    expected_prefix = "win" if platform.system() == "Windows" else "linux"
    assert calls and calls[0].startswith(expected_prefix), calls
    assert calls[0].endswith("15:tuisc.tonicaphid.cn:both"), calls

    ddns.install(["--only", "v6"])
    assert calls[-1].endswith(":v6"), calls


def test_only_helpers() -> None:
    """``--only`` 的拼接 / 解析（家里没公网 v4 时只发 AAAA）。"""
    assert ddns._only_suffix("both") == ""
    assert ddns._only_suffix("v6") == " --only v6"
    assert ddns._only_args("v6") == ["--only", "v6"]
    assert ddns._only_from(None) == "both"
    assert ddns._only_from(["--only", "v6"]) == "v6"
    assert ddns._only_from(["--only", "bogus"]) == "both"


def test_install_rejects_bad_only(monkeypatch, capsys) -> None:
    monkeypatch.setattr(ddns, "_load_aliyun_env", lambda: {"ALIYUN_ACCESS_KEY_ID": "x", "ALIYUN_ACCESS_KEY_SECRET": "y"})
    assert ddns.install(["--only", "v7"]) == 2
    assert "只认" in capsys.readouterr().out


# ------------------------------------------------------- 监听地址 / 启动询问
def test_host_defaults_to_loopback() -> None:
    """默认必须只监听回环。

    以前默认是 0.0.0.0：本机没写 TONICUISC_HOST 时，uvicorn 直接绑所有网卡，
    外网能绕过 Caddy 直连服务端（TLS 和鉴权链一起跳过）。
    """
    if os.getenv("TONICUISC_HOST"):
        pytest.skip("本机设了 TONICUISC_HOST，测不了默认值")
    from tonicuisc_server.core.config import SETTINGS

    assert SETTINGS.host == "127.0.0.1"


def test_display_host_replaces_wildcards() -> None:
    """通配监听地址不能当主机名用（否则横幅打出点不开的链接）。"""
    from tonicuisc_server.core.config import display_host

    for wildcard in ("0.0.0.0", "::", "[::]", "*", "", "  "):
        assert display_host(wildcard) == "127.0.0.1", wildcard
    assert display_host("192.168.5.37") == "192.168.5.37"
    assert display_host("127.0.0.1") == "127.0.0.1"


def test_login_banner_uses_domain_and_avoids_wildcard() -> None:
    """横幅：配了 PUBLIC_URL 就用它；没配也不该打 0.0.0.0。"""
    from types import SimpleNamespace

    from tonicuisc_server.api.app import login_banner

    with_domain = login_banner(
        SimpleNamespace(public_url="https://uisc.tonicaphid.cn", host="0.0.0.0", port=8000, scheme="http")
    )
    assert "https://uisc.tonicaphid.cn/login" in with_domain

    without = login_banner(SimpleNamespace(public_url="", host="0.0.0.0", port=8000, scheme="http"), enabled=True)
    assert "0.0.0.0" not in without, without
    assert "http://127.0.0.1:8000/login" in without


def test_choose_environment_skips_when_already_chosen(monkeypatch) -> None:
    """选过就不再问——启动时突然弹问题很烦。"""
    from tonicuisc_server.cli import launch

    monkeypatch.setenv(env_mod.ENV_KEY, "uisc")
    called: list[str] = []
    monkeypatch.setattr("builtins.input", lambda prompt="": called.append(prompt) or "2")
    assert launch.choose_environment(interactive=True) is False
    assert called == [], "已经选过就不要再读输入"


def test_choose_environment_asks_and_persists(monkeypatch, capsys) -> None:
    """没选过时要问，并且把选择写进 .env。"""
    import shutil

    from tonicuisc_server.cli import launch

    root = _scratch_dir()
    try:
        monkeypatch.delenv(env_mod.ENV_KEY, raising=False)
        monkeypatch.delenv("TONICUISC_PUBLIC_URL", raising=False)
        path = root / ".env"
        monkeypatch.setattr(env_mod, "ENV_PATH", path)
        monkeypatch.setattr("builtins.input", lambda prompt="": "2")  # 选第 2 个 = tuisc

        assert launch.choose_environment(interactive=True) is True
        out = capsys.readouterr().out
        assert "这是哪套环境" in out, out
        assert "uisc" in out and "tuisc" in out, out

        written = path.read_text(encoding="utf-8")
        assert "TONICUISC_ENV=tuisc" in written, written
        assert "TONICUISC_PUBLIC_URL=https://tuisc.tonicaphid.cn" in written, written
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_choose_environment_accepts_name_and_default(monkeypatch) -> None:
    """输名字（tuisc）和直接回车（用默认）都要认。"""
    import shutil

    from tonicuisc_server.cli import launch

    root = _scratch_dir()
    try:
        monkeypatch.delenv(env_mod.ENV_KEY, raising=False)
        monkeypatch.setattr(env_mod, "ENV_PATH", root / ".env")

        monkeypatch.setattr("builtins.input", lambda prompt="": "tuisc")
        assert launch.choose_environment(interactive=True) is True
        assert (root / ".env").read_text(encoding="utf-8").startswith("TONICUISC_ENV=tuisc")

        monkeypatch.delenv(env_mod.ENV_KEY, raising=False)
        monkeypatch.setattr("builtins.input", lambda prompt="": "")  # 回车 = 第一个
        assert launch.choose_environment(interactive=True) is True
        assert "TONICUISC_ENV=uisc" in (root / ".env").read_text(encoding="utf-8")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_choose_environment_reprompts_on_bad_input(monkeypatch) -> None:
    """输错了要重问，而不是当成默认值收下。"""
    import shutil

    from tonicuisc_server.cli import launch

    root = _scratch_dir()
    try:
        monkeypatch.delenv(env_mod.ENV_KEY, raising=False)
        monkeypatch.setattr(env_mod, "ENV_PATH", root / ".env")
        answers = iter(["随便写", "99", "tuisc"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

        assert launch.choose_environment(interactive=True) is True
        assert "TONICUISC_ENV=tuisc" in (root / ".env").read_text(encoding="utf-8")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_choose_environment_not_interactive_is_quiet(monkeypatch) -> None:
    """非交互（systemd / 计划任务）不能阻塞在 input() 上。"""
    from tonicuisc_server.cli import launch

    monkeypatch.delenv(env_mod.ENV_KEY, raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("非交互时不该读输入"))
    assert launch.choose_environment(interactive=False) is False


def test_choose_environment_eof_does_not_decide(monkeypatch, capsys) -> None:
    """读不到输入（后台跑 / 重定向）不该当成「回车用默认」，也不该写 .env。

    实测踩过：后台起服务时 input() 立刻 EOF，于是它默默把 uisc 写进了 .env。
    EOF 是「没人能回答」，不是「用户选了默认」。
    """
    import shutil

    from tonicuisc_server.cli import launch

    root = _scratch_dir()
    try:
        monkeypatch.delenv(env_mod.ENV_KEY, raising=False)
        path = root / ".env"
        monkeypatch.setattr(env_mod, "ENV_PATH", path)

        def eof(prompt: str = "") -> str:
            raise EOFError

        monkeypatch.setattr("builtins.input", eof)
        assert launch.choose_environment(interactive=True) is False
        assert "读不到输入" in capsys.readouterr().out
        assert not path.exists(), "EOF 时不该写 .env"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_choose_environment_ctrl_c_does_not_decide(monkeypatch) -> None:
    import shutil

    from tonicuisc_server.cli import launch

    root = _scratch_dir()
    try:
        monkeypatch.delenv(env_mod.ENV_KEY, raising=False)
        path = root / ".env"
        monkeypatch.setattr(env_mod, "ENV_PATH", path)

        def interrupt(prompt: str = "") -> str:
            raise KeyboardInterrupt

        monkeypatch.setattr("builtins.input", interrupt)
        assert launch.choose_environment(interactive=True) is False
        assert not path.exists(), "Ctrl+C 时不该写 .env"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_apply_runtime_env_picks_up_public_url(monkeypatch) -> None:
    """刚选完的环境要立刻生效，否则横幅退回 host:port。

    Settings 的默认值是类定义时求值的，所以选完环境必须显式重建。
    """
    from tonicuisc_server.core import config

    monkeypatch.setenv(env_mod.ENV_KEY, "tuisc")
    monkeypatch.setenv("TONICUISC_PUBLIC_URL", "https://tuisc.tonicaphid.cn")
    try:
        settings = config.apply_runtime_env()
        assert config.ENVIRONMENT.key == "tuisc"
        assert settings.public_url == "https://tuisc.tonicaphid.cn"
    finally:
        monkeypatch.setenv(env_mod.ENV_KEY, "uisc")
        monkeypatch.delenv("TONICUISC_PUBLIC_URL", raising=False)
        config.apply_runtime_env()


# ----------------------------------------------------------- env 子命令
def test_env_command_show_and_switch(monkeypatch, capsys) -> None:
    import shutil

    from tonicuisc_server.cli.commands import env_command

    root = _scratch_dir()
    try:
        path = root / ".env"
        monkeypatch.setattr(env_mod, "ENV_PATH", path)
        monkeypatch.setenv(env_mod.ENV_KEY, "uisc")

        assert env_command([]) == 0
        out = capsys.readouterr().out
        assert "当前环境：uisc" in out, out
        assert "env --ask" in out, "要告诉人怎么重选"

        assert env_command(["tuisc"]) == 0
        assert "TONICUISC_ENV=tuisc" in path.read_text(encoding="utf-8")
        assert env_command(["不存在的"]) == 2
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_env_command_ask_forces_prompt(monkeypatch, capsys) -> None:
    """`env --ask` 要能重选（哪怕 .env 里已经选了）。"""
    import shutil

    from tonicuisc_server.cli.commands import env_command

    root = _scratch_dir()
    try:
        monkeypatch.setattr(env_mod, "ENV_PATH", root / ".env")
        monkeypatch.setenv(env_mod.ENV_KEY, "uisc")  # 假装之前选过
        monkeypatch.setattr("builtins.input", lambda prompt="": "2")

        assert env_command(["--ask"]) == 0
        assert "这是哪套环境" in capsys.readouterr().out
        assert "TONICUISC_ENV=tuisc" in (root / ".env").read_text(encoding="utf-8")
    finally:
        shutil.rmtree(root, ignore_errors=True)


# --------------------------------------------------------- 阿里云 AK (set-key)
def test_set_key_writes_file_and_hides_secret(monkeypatch, capsys) -> None:
    """set-key 把 AK 写进 deploy/.aliyun.env；**Secret 不能出现在输出里**。"""
    import shutil
    from types import SimpleNamespace

    root = _scratch_dir()
    try:
        monkeypatch.setattr(ddns, "SETTINGS", SimpleNamespace(caddy_dir=root))
        secret = "SK-should-never-be-printed-123456"
        monkeypatch.setattr("builtins.input", lambda prompt="": "LTAI_test_id")
        monkeypatch.setattr("getpass.getpass", lambda prompt="": secret)
        monkeypatch.setattr(ddns, "_run_script", lambda extra, record=None: (0, "（验证输出）"))

        assert ddns.set_key([]) == 0
        text = (root / ".aliyun.env").read_text(encoding="utf-8")
        assert "ALIYUN_ACCESS_KEY_ID=LTAI_test_id" in text
        assert f"ALIYUN_ACCESS_KEY_SECRET={secret}" in text
        assert secret not in capsys.readouterr().out, "Secret 不该被打印出来"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_set_key_cancel_on_empty(monkeypatch) -> None:
    import shutil
    from types import SimpleNamespace

    root = _scratch_dir()
    try:
        monkeypatch.setattr(ddns, "SETTINGS", SimpleNamespace(caddy_dir=root))
        monkeypatch.setattr("builtins.input", lambda prompt="": "")
        assert ddns.set_key([]) == 2
        assert not (root / ".aliyun.env").exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_set_key_reports_why_verification_failed(monkeypatch, capsys) -> None:
    """验证失败时要把阿里云的原始原因透出来（AK 错 vs 没权限是两种问题）。"""
    import shutil
    from types import SimpleNamespace

    root = _scratch_dir()
    try:
        monkeypatch.setattr(ddns, "SETTINGS", SimpleNamespace(caddy_dir=root))
        monkeypatch.setattr("builtins.input", lambda prompt="": "LTAI_test_id")
        monkeypatch.setattr("getpass.getpass", lambda prompt="": "secret")
        monkeypatch.setattr(ddns, "_run_script", lambda extra, record=None: (1, "InvalidAccessKeyId.NotFound"))

        assert ddns.set_key([]) == 1
        out = capsys.readouterr().out
        assert "InvalidAccessKeyId.NotFound" in out, "要把阿里云的原话打出来"
        assert "Forbidden" in out, "也要提示没给权限这种情况"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_check_passes_check_flag(monkeypatch) -> None:
    """`ddns check` 必须用 --check（只读），绝不能误用 --update。"""
    calls: list[list[str]] = []

    def fake(extra, record=None):
        calls.append(list(extra))
        return 0, "ok"

    monkeypatch.setattr(ddns, "_run_script", fake)
    assert ddns.check([]) == 0
    assert calls == [["--check"]], calls


def test_run_once_uses_update_flag(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(ddns, "STATE_FILE", _scratch_dir() / "state.txt")
    monkeypatch.setattr(ddns, "_run_script", lambda extra, record=None: calls.append(list(extra)) or (0, "ok"))
    assert ddns.run_once() == 0
    assert calls == [["--update"]], calls


def test_install_requires_key_file_not_env_var(monkeypatch, capsys) -> None:
    """定时任务看不到 shell 里的环境变量，所以必须落在文件里。"""
    import shutil
    from types import SimpleNamespace

    root = _scratch_dir()
    try:
        monkeypatch.setattr(ddns, "SETTINGS", SimpleNamespace(caddy_dir=root))
        monkeypatch.setattr(ddns, "_load_aliyun_env", lambda: {})  # 文件里没有
        monkeypatch.setenv("ALIYUN_ACCESS_KEY_ID", "LTAI_only_in_shell")

        assert ddns.install([]) == 2
        out = capsys.readouterr().out
        assert "定时任务看不到它" in out, out
        assert "set-key" in out, out
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_list_records_is_readonly(monkeypatch, capsys) -> None:
    """--check 只读：只调 DescribeDomainRecords，绝不 Add/Update。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "update_dns_probe", Path(__file__).resolve().parent.parent.parent / "deploy" / "update_dns.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    actions: list[str] = []

    def fake_call(action, *_args, **_kwargs):
        actions.append(action)
        return {
            "DomainRecords": {
                "Record": [
                    {"RR": "uisc", "Type": "A", "Value": "1.2.3.4", "TTL": 600},
                    {"RR": "uisc", "Type": "AAAA", "Value": "240e::1", "TTL": 600},
                    {"RR": "other", "Type": "A", "Value": "9.9.9.9", "TTL": 600},
                ]
            }
        }

    monkeypatch.setattr(module, "aliyun_call", fake_call)
    assert module.list_records("uisc.tonicaphid.cn", "id", "secret", "tonicaphid.cn") == 0
    assert actions == ["DescribeDomainRecords"], actions
    out = capsys.readouterr().out
    assert "1.2.3.4" in out and "240e::1" in out
    assert "9.9.9.9" not in out, "只列本记录，别把别的子域带出来"


# --------------------------------------------------- Caddy 状态 / 自动启动
def test_state_dir_is_inside_project() -> None:
    """Caddy 的状态目录必须在项目里，不能是 %AppData%。

    实测踩过：受限令牌下 `mkdir %AppData%\\Roaming\\Caddy` 报 Access denied，
    Caddy 直接放弃申请证书 → 443 握不了手 → 浏览器 ERR_SSL_PROTOCOL_ERROR。
    """
    import shutil

    root = _scratch_dir()
    try:
        settings = _fake_settings(root)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(caddy, "SETTINGS", settings)
            assert caddy.state_dir() == root / ".caddy"
            assert caddy.data_dir() == root / ".caddy" / "data"
            assert caddy.config_home() == root / ".caddy" / "config"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_caddy_env_redirects_home_and_makes_dirs() -> None:
    """子进程的环境变量要把 Caddy 的「家」指到项目里，并且目录先建好。"""
    import shutil

    root = _scratch_dir()
    try:
        settings = _fake_settings(root)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(caddy, "SETTINGS", settings)
            env = caddy.caddy_env()

        assert env["APPDATA"] == str(root / ".caddy" / "config")
        assert env["XDG_CONFIG_HOME"] == str(root / ".caddy" / "config")
        assert env["XDG_DATA_HOME"] == str(root / ".caddy" / "data")
        assert (root / ".caddy" / "data").is_dir(), "目录要先建好，Caddy 才写得进去"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_generate_config_fills_storage_placeholder() -> None:
    """模板里的 {STORAGE} 必须被替换成绝对路径，且不留占位符。"""
    import shutil

    root = _scratch_dir()
    try:
        (root / "Caddyfile.template").write_text(
            "{\n\tstorage file_system {STORAGE}\n}\n\n{DOMAIN} {\n\treverse_proxy {UPSTREAM}\n}\n",
            encoding="utf-8",
        )
        settings = _fake_settings(root)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(caddy, "SETTINGS", settings)
            patch.setattr(acme, "SETTINGS", settings)
            patch.setattr(caddy, "ENVIRONMENT", env_mod.ENVIRONMENTS["uisc"])
            text = caddy.generate_config().read_text(encoding="utf-8")

        assert "{STORAGE}" not in text, "占位符没替换掉"
        assert "storage file_system" in text
        assert str(root / ".caddy" / "data").replace("\\", "/") in text
        assert "AppData" not in text, "不能再用 %AppData%"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_admin_alive_detects_existing_caddy(monkeypatch) -> None:
    """已经有一个 Caddy 在跑时要能识别出来（管理端点 2019 有响应）。

    实测踩过：手动开的那个占着 2019，自动启动永远失败在
    `bind: Only one usage of each socket address`，看起来像端口/权限问题。
    """
    import urllib.request

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=None: FakeResponse())
    assert caddy.admin_alive() is True

    def refuse(url, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    assert caddy.admin_alive() is False


def test_ensure_running_skips_when_already_up(monkeypatch, capsys) -> None:
    """已有 Caddy 在跑时不再起第二个，并且返回 None（表示「不是我们起的」）。"""
    called: list[str] = []
    monkeypatch.setattr(caddy, "admin_alive", lambda **kw: True)
    monkeypatch.setattr(caddy, "find_caddy", lambda **kw: called.append("find") or Path("caddy"))
    monkeypatch.setattr(caddy, "start_caddy", lambda *a, **kw: called.append("start"))

    assert caddy.ensure_running() is None
    assert called == [], "已经在跑就不该再去找/再启动"
    assert "已经有一个 Caddy 在跑" in capsys.readouterr().out


def test_ensure_running_survives_start_failure(monkeypatch, capsys) -> None:
    """Caddy 起不来不该抛异常（服务端要照常跑）。"""
    import shutil

    root = _scratch_dir()
    try:
        (root / "Caddyfile.template").write_text("{DOMAIN} {\n\t{TLS}\treverse_proxy {UPSTREAM}\n}\n", encoding="utf-8")
        settings = _fake_settings(root)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(caddy, "SETTINGS", settings)
            patch.setattr(acme, "SETTINGS", settings)
            patch.setattr(caddy, "admin_alive", lambda **kw: False)
            patch.setattr(caddy, "find_caddy", lambda **kw: Path("caddy"))
            patch.setattr(caddy, "validate_config", lambda *a, **kw: (True, "ok"))

            def boom(*a, **kw):
                raise RuntimeError("Caddy 启动后立刻退出了（退出码 1）")

            patch.setattr(caddy, "start_caddy", boom)
            assert caddy.ensure_running() is None  # 不抛

        out = capsys.readouterr().out
        assert "Caddy 没起来" in out, out
        assert "open-firewall" in out, "要把「入站没放行」这条线索给出来"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_stop_caddy_ignores_foreign_process() -> None:
    """不是我们起的（None）就什么都不做——别去停别人开的 Caddy。"""
    caddy.stop_caddy(None)  # 不抛即通过


def test_caddy_runtime_log_goes_to_file(monkeypatch) -> None:
    """默认把 Caddy 的输出重定向到文件——那十几行 INFO + Go 运行时信息不该刷控制台。

    `maxprocs` / `GOMEMLIMIT` 是 Go 运行时直接写 stderr 的，**不经过 Caddy 日志系统**，
    所以只调 Caddyfile 的日志级别掐不掉，必须重定向子进程。
    """
    import shutil
    import subprocess as sp

    root = _scratch_dir()
    try:
        settings = _fake_settings(root)
        settings.caddy_log_console = False
        config = root / "Caddyfile"
        config.write_text("x\n", encoding="utf-8")
        captured: dict = {}

        class FakeProc:
            pid = 4321
            returncode = None

            def poll(self):
                return None

        def fake_popen(cmd, **kwargs):
            captured["kwargs"] = kwargs
            return FakeProc()

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(caddy, "SETTINGS", settings)
            patch.setattr(caddy.subprocess, "Popen", fake_popen)
            patch.setattr(caddy.time, "sleep", lambda _s: None)
            caddy.start_caddy(Path("caddy"), config, wait_seconds=0)
            # log_path() 也要在 patch 作用域里问，否则读到的是真实 SETTINGS
            assert caddy.log_path() == root / ".caddy" / "runtime.log"

        kwargs = captured["kwargs"]
        assert kwargs.get("stdout") is not None, "默认要把 stdout 重定向走"
        assert kwargs.get("stderr") is not None, "默认要把 stderr 重定向走"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_caddy_console_mode_does_not_redirect(monkeypatch) -> None:
    """显式要 console 时不重定向（前台手动起 Caddy 时想看实时输出）。"""
    import shutil

    root = _scratch_dir()
    try:
        settings = _fake_settings(root)
        settings.caddy_log_console = True
        config = root / "Caddyfile"
        config.write_text("x\n", encoding="utf-8")
        captured: dict = {}

        class FakeProc:
            pid = 1
            returncode = None

            def poll(self):
                return None

        def fake_popen(cmd, **kwargs):
            captured["kwargs"] = kwargs
            return FakeProc()

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(caddy, "SETTINGS", settings)
            patch.setattr(caddy.subprocess, "Popen", fake_popen)
            patch.setattr(caddy.time, "sleep", lambda _s: None)
            caddy.start_caddy(Path("caddy"), config, wait_seconds=0)

        assert captured["kwargs"].get("stdout") is None
        assert captured["kwargs"].get("stderr") is None
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_start_failure_shows_log_tail(monkeypatch) -> None:
    """起不来时要把日志尾部打出来——否则用户只看到「退出了」无从下手。"""
    import shutil

    root = _scratch_dir()
    try:
        settings = _fake_settings(root)
        settings.caddy_log_console = False
        config = root / "Caddyfile"
        config.write_text("x\n", encoding="utf-8")
        # 直接写 scratch 下的路径：**别调 caddy.log_path()**，那会按真实 SETTINGS
        # 解析到项目里的 deploy/.caddy/（实测踩过：测试把假日志写进了真目录）
        log = root / ".caddy" / "runtime.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("第一行\n第二行: 443 被占用\n", encoding="utf-8")

        class DeadProc:
            pid = 1
            returncode = 1

            def poll(self):
                return 1

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(caddy, "SETTINGS", settings)
            patch.setattr(caddy.subprocess, "Popen", lambda *a, **kw: DeadProc())
            patch.setattr(caddy.time, "sleep", lambda _s: None)
            with pytest.raises(RuntimeError) as info:
                caddy.start_caddy(Path("caddy"), config, wait_seconds=0)

        assert "443 被占用" in str(info.value), "要把日志尾部带进错误信息"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_caddy_autostart_default_is_on() -> None:
    """默认跟着服务一起起（家里部署本来就两个进程）。"""
    import dataclasses

    from tonicuisc_server.core.config import Settings

    field = next(f for f in dataclasses.fields(Settings) if f.name == "caddy_autostart")
    assert field.default is True, "默认应为开；要关就设 TONICUISC_CADDY_AUTOSTART=0"


# ------------------------------------------------------------------ ACME / 证书
def test_cert_paths_are_inside_project() -> None:
    """证书要落在项目里（好备份、好排查），文件名用域名。"""
    import shutil

    root = _scratch_dir()
    try:
        settings = _fake_settings(root)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(acme, "SETTINGS", settings)
            crt, key = acme.cert_paths("tuisc.tonicaphid.cn")
            assert crt == root / ".caddy" / "certs" / "tuisc.tonicaphid.cn.crt"
            assert key == root / ".caddy" / "certs" / "tuisc.tonicaphid.cn.key"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_needs_renewal_when_missing_or_empty() -> None:
    """证书不存在 / 是 0 字节 → 需要签。

    0 字节那个是实测踩过的：先写 .crt 再写 .key，中途出错会留下空证书，
    Caddy 照样去加载它，然后握手失败（看起来像证书问题）。
    """
    import shutil

    root = _scratch_dir()
    try:
        settings = _fake_settings(root)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(acme, "SETTINGS", settings)
            assert acme.needs_renewal("tuisc.tonicaphid.cn") is True

            crt, key = acme.cert_paths("tuisc.tonicaphid.cn")
            crt.parent.mkdir(parents=True, exist_ok=True)
            crt.write_text("", encoding="utf-8")
            key.write_text("x", encoding="utf-8")
            assert acme.needs_renewal("tuisc.tonicaphid.cn") is True, "空证书要重签"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_obtain_skips_when_cert_is_fresh(monkeypatch) -> None:
    """证书还有很久才过期就别重签——Let's Encrypt 有每周签发限额。"""
    import shutil

    root = _scratch_dir()
    try:
        settings = _fake_settings(root)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(acme, "SETTINGS", settings)
            crt, key = acme.cert_paths("tuisc.tonicaphid.cn")
            crt.parent.mkdir(parents=True, exist_ok=True)
            crt.write_text(_pem("X"), encoding="utf-8")
            key.write_text("k", encoding="utf-8")
            patch.setattr(acme, "cert_expires_in_days", lambda _p: 80)

            called: list[str] = []
            patch.setattr(acme, "find_lego", lambda **kw: called.append("find") or None)
            got_crt, got_key = acme.obtain("tuisc.tonicaphid.cn")
            assert (got_crt, got_key) == (crt, key)
            assert called == [], "不该去找 lego（说明没打算重签）"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_obtain_requires_aliyun_key(monkeypatch) -> None:
    """没有 AK 就明确说清楚（DNS-01 要往解析里写记录）。"""
    import shutil

    root = _scratch_dir()
    try:
        settings = _fake_settings(root)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(acme, "SETTINGS", settings)
            patch.setattr(acme, "_aliyun_env", lambda: {})
            patch.setattr(acme, "needs_renewal", lambda *a, **kw: True)

            with pytest.raises(RuntimeError) as info:
                acme.obtain("tuisc.tonicaphid.cn")
            assert "ddns set-key" in str(info.value), "要告诉用户怎么配 AK"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_obtain_builds_dns01_command(monkeypatch) -> None:
    """命令必须是 DNS-01（--dns alidns）——这条决定了「不需要入站端口」。

    同时确认 lego v5 的 .crt **已经含完整链**时不再拼 .issuer.crt（拼了就是重复）。
    """
    import shutil
    import subprocess as sp

    root = _scratch_dir()
    try:
        settings = _fake_settings(root)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(acme, "SETTINGS", settings)
            patch.setattr(acme, "needs_renewal", lambda *a, **kw: True)
            patch.setattr(acme, "_aliyun_env", lambda: {"ALICLOUD_ACCESS_KEY": "id", "ALICLOUD_SECRET_KEY": "sk"})
            patch.setattr(acme, "find_lego", lambda **kw: Path("lego"))
            patch.setattr(acme, "cert_expires_in_days", lambda _p: 90)

            captured: dict = {}

            def fake_run(cmd, **kwargs):
                captured["cmd"] = cmd
                captured["env"] = kwargs.get("env") or {}
                work = Path(cmd[cmd.index("--path") + 1]) / "certificates"
                work.mkdir(parents=True, exist_ok=True)
                name = cmd[cmd.index("--domains") + 1]
                (work / f"{name}.crt").write_text(_pem("LEAF") + _pem("MIDDLE"), encoding="utf-8")
                (work / f"{name}.issuer.crt").write_text(_pem("MIDDLE"), encoding="utf-8")
                (work / f"{name}.key").write_text(
                    "-----BEGIN PRIVATE KEY-----\nFAKE\n-----END PRIVATE KEY-----\n", encoding="utf-8"
                )
                return sp.CompletedProcess(cmd, 0, "ok", "")

            patch.setattr(acme.subprocess, "run", fake_run)
            crt, key = acme.obtain("tuisc.tonicaphid.cn")

        cmd = captured["cmd"]
        assert "--dns" in cmd and cmd[cmd.index("--dns") + 1] == "alidns", cmd
        assert "--accept-tos" in cmd, "lego 不加这个会拒绝服务条款"
        assert "--domains" in cmd and "tuisc.tonicaphid.cn" in cmd
        # AK 必须以 lego 认的名字传进去（ALICLOUD_* 而不是我们的 ALIYUN_*）
        assert captured["env"]["ALICLOUD_ACCESS_KEY"] == "id"
        assert captured["env"]["ALICLOUD_SECRET_KEY"] == "sk"

        text = crt.read_text(encoding="utf-8")
        assert "LEAF" in text and "MIDDLE" in text
        assert text.count("BEGIN CERTIFICATE") == 2, f"中间证书被重复拼进去了：{text!r}"
        assert "PRIVATE KEY" in key.read_text(encoding="utf-8")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_obtain_appends_issuer_only_when_needed(monkeypatch) -> None:
    """老版本 lego 的 .crt 只有叶子证书，这时才补中间证书。"""
    import shutil
    import subprocess as sp

    root = _scratch_dir()
    try:
        settings = _fake_settings(root)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(acme, "SETTINGS", settings)
            patch.setattr(acme, "needs_renewal", lambda *a, **kw: True)
            patch.setattr(acme, "_aliyun_env", lambda: {"ALICLOUD_ACCESS_KEY": "id", "ALICLOUD_SECRET_KEY": "sk"})
            patch.setattr(acme, "find_lego", lambda **kw: Path("lego"))
            patch.setattr(acme, "cert_expires_in_days", lambda _p: 90)

            def fake_run(cmd, **kwargs):
                work = Path(cmd[cmd.index("--path") + 1]) / "certificates"
                work.mkdir(parents=True, exist_ok=True)
                name = cmd[cmd.index("--domains") + 1]
                (work / f"{name}.crt").write_text(_pem("LEAFONLY"), encoding="utf-8")
                (work / f"{name}.issuer.crt").write_text(_pem("MIDDLE"), encoding="utf-8")
                (work / f"{name}.key").write_text(
                    "-----BEGIN PRIVATE KEY-----\nFAKE\n-----END PRIVATE KEY-----\n", encoding="utf-8"
                )
                return sp.CompletedProcess(cmd, 0, "ok", "")

            patch.setattr(acme.subprocess, "run", fake_run)
            crt, _key = acme.obtain("tuisc.tonicaphid.cn")

        text = crt.read_text(encoding="utf-8")
        assert text.count("BEGIN CERTIFICATE") == 2, "只有叶子时要把中间证书补上"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_obtain_rejects_broken_lego_output(monkeypatch) -> None:
    """lego 说成功但文件是空的 → 必须报错，不能写出 0 字节证书。"""
    import shutil
    import subprocess as sp

    root = _scratch_dir()
    try:
        settings = _fake_settings(root)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(acme, "SETTINGS", settings)
            patch.setattr(acme, "needs_renewal", lambda *a, **kw: True)
            patch.setattr(acme, "_aliyun_env", lambda: {"ALICLOUD_ACCESS_KEY": "id", "ALICLOUD_SECRET_KEY": "sk"})
            patch.setattr(acme, "find_lego", lambda **kw: Path("lego"))

            def fake_run(cmd, **kwargs):
                work = Path(cmd[cmd.index("--path") + 1]) / "certificates"
                work.mkdir(parents=True, exist_ok=True)
                name = cmd[cmd.index("--domains") + 1]
                (work / f"{name}.crt").write_text("", encoding="utf-8")  # 空的
                (work / f"{name}.key").write_text("k", encoding="utf-8")
                return sp.CompletedProcess(cmd, 0, "ok", "")

            patch.setattr(acme.subprocess, "run", fake_run)
            with pytest.raises(RuntimeError):
                acme.obtain("tuisc.tonicaphid.cn")

            crt, _key = acme.cert_paths("tuisc.tonicaphid.cn")
            assert not crt.exists() or crt.stat().st_size > 0, "不该留下空证书"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_ensure_swallows_failure(monkeypatch, capsys) -> None:
    """签不下来不该抛异常（服务端要照常跑）。"""
    monkeypatch.setattr(acme, "obtain", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("阿里云抽风")))
    assert acme.ensure("tuisc.tonicaphid.cn") is None
    assert "阿里云抽风" in capsys.readouterr().out


def test_generate_config_uses_cert_when_present() -> None:
    """有证书时 Caddyfile 要写 tls <crt> <key>（Caddy 就不再自己跑 ACME）。"""
    import shutil

    root = _scratch_dir()
    try:
        (root / "Caddyfile.template").write_text(
            "{DOMAIN} {\n{TLS}\treverse_proxy {UPSTREAM}\n}\n", encoding="utf-8"
        )
        settings = _fake_settings(root)
        # **caddy 和 acme 的 SETTINGS 都要换**：generate_config 内部会问 acme 要证书路径，
        # 只换 caddy.SETTINGS 的话 acme 会拿真实路径（曾经因此把假证书写进项目目录）。
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(caddy, "SETTINGS", settings)
            patch.setattr(acme, "SETTINGS", settings)
            patch.setattr(caddy, "ENVIRONMENT", env_mod.ENVIRONMENTS["tuisc"])

            text = caddy.generate_config().read_text(encoding="utf-8")
            assert "{TLS}" not in text, "占位符要替换掉"
            assert "tls " not in text, "没证书时不该写 tls 指令"

            crt, key = acme.cert_paths("tuisc.tonicaphid.cn")
            assert str(root) in str(crt), "测试必须落在临时目录里"
            crt.parent.mkdir(parents=True, exist_ok=True)
            crt.write_text(_pem("X"), encoding="utf-8")
            key.write_text("k", encoding="utf-8")

            text = caddy.generate_config(force=True).read_text(encoding="utf-8")
            assert "tls " in text, "有证书时要让 Caddy 用它"
            assert crt.as_posix() in text
    finally:
        shutil.rmtree(root, ignore_errors=True)
