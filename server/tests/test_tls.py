"""HTTPS / TLS：参数装配、协议选择、自签证书生成。不联网。"""

from __future__ import annotations

import dataclasses
import importlib.util
import shutil
import ssl
import uuid
from pathlib import Path

import pytest

from tonicuisc_server.api.app import login_banner
from tonicuisc_server.core.config import SETTINGS
from tonicuisc_server.core.tls import cert_paths, generate_self_signed, ssl_kwargs

#: 不用 pytest 的 tmp_path：这个环境里系统临时目录读不了（权限问题），
#: 跟 conftest 一样落在仓库内的 _scratch 下。
SCRATCH = Path(__file__).resolve().parent / "_scratch" / "tls"


@pytest.fixture()
def tmp_path():
    path = SCRATCH / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _settings(**changes):
    return dataclasses.replace(SETTINGS, **changes)


def test_plain_http_by_default() -> None:
    settings = _settings(tls_cert="", tls_key="")
    assert settings.tls_enabled is False
    assert settings.scheme == "http"
    assert ssl_kwargs(settings) == {}, "没配证书就不该给 uvicorn 塞 TLS 参数"


def test_tls_needs_both_cert_and_key() -> None:
    assert _settings(tls_cert="/tmp/a.crt", tls_key="").tls_enabled is False, "只给证书当作没配"
    both = _settings(tls_cert="/tmp/a.crt", tls_key="/tmp/a.key")
    assert both.tls_enabled is True
    assert both.scheme == "https"
    assert ssl_kwargs(both) == {"ssl_certfile": "/tmp/a.crt", "ssl_keyfile": "/tmp/a.key"}


def test_banner_follows_scheme() -> None:
    plain = login_banner(_settings(public_url="", host="127.0.0.1", port=8000, tls_cert="", tls_key=""))
    assert "http://127.0.0.1:8000/login" in plain

    secure = login_banner(_settings(public_url="", host="127.0.0.1", port=8000, tls_cert="c", tls_key="k"))
    assert "https://127.0.0.1:8000/login" in secure

    # 配了对外地址就以它为准（反代终止 TLS 时用这条）
    proxied = login_banner(_settings(public_url="https://uisc.tonicaphid.cn", tls_cert="", tls_key=""))
    assert "https://uisc.tonicaphid.cn/login" in proxied


def test_cert_paths_are_sanitized(tmp_path) -> None:
    cert, key = cert_paths(tmp_path, "192.168.5.37")
    assert cert.name == "192.168.5.37.crt" and key.name == "192.168.5.37.key"

    weird_cert, weird_key = cert_paths(tmp_path, "a/b:c")
    assert "/" not in weird_cert.name and "\\" not in weird_cert.name and ":" not in weird_cert.name
    assert weird_key.suffix == ".key"


@pytest.mark.skipif(importlib.util.find_spec("cryptography") is None, reason="没装 cryptography")
def test_self_signed_cert_loads(tmp_path) -> None:
    cert, key = cert_paths(tmp_path, "192.168.5.37")
    tool = generate_self_signed("192.168.5.37", cert, key)
    assert tool in {"cryptography", "openssl"}
    assert cert.exists() and key.exists()

    # 能被 OpenSSL 读进来才算真证书（格式错在这里就会炸）
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(cert), keyfile=str(key))
