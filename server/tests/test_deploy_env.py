"""环境选择 / .env 加载 / Caddy 配置生成 / DDNS 辅助。不联网、不装任何东西。"""

from __future__ import annotations

import os
import platform
from pathlib import Path

import pytest

from tonicuisc_server.core import caddy
from tonicuisc_server.core import ddns
from tonicuisc_server.core import environment as env_mod


def _scratch_dir() -> Path:
    """不用 pytest 的 tmp_path：这个环境的系统临时目录读不了（权限问题）。"""
    import uuid

    path = Path(__file__).resolve().parent / "_scratch" / "deploy" / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    return path


def _fake_settings(root: Path, *, host: str = "0.0.0.0", port: int = 8000):
    """一个够用的假 Settings：caddy_dir / caddy_config / host / port。"""
    from types import SimpleNamespace

    return SimpleNamespace(caddy_dir=root, caddy_config=root / "Caddyfile", host=host, port=port)


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


def test_ddns_install_requires_aliyun_key(tmp_path, monkeypatch) -> None:
    """没配 AK 时要明确提示，而不是装个跑不起来的任务。"""
    monkeypatch.setattr(ddns, "_load_aliyun_env", lambda: {})
    monkeypatch.delenv("ALIYUN_ACCESS_KEY_ID", raising=False)

    class FakeSettings:
        caddy_dir = tmp_path

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
    monkeypatch.setattr(ddns, "_install_windows", lambda minutes, record: calls.append(f"win:{minutes}:{record}") or 0)
    monkeypatch.setattr(ddns, "_install_linux", lambda minutes, record: calls.append(f"linux:{minutes}:{record}") or 0)

    ddns.install(["--every", "15m", "--record", "tuisc.tonicaphid.cn"])
    expected_prefix = "win" if platform.system() == "Windows" else "linux"
    assert calls and calls[0].startswith(expected_prefix), calls
    assert calls[0].endswith("15:tuisc.tonicaphid.cn"), calls
