"""HTTPS：证书定位、自签生成、uvicorn 参数。

服务端支持两条路，互不冲突：

1. **原生 TLS**——设 ``TONICUISC_TLS_CERT`` / ``TONICUISC_TLS_KEY``，uvicorn 直接跑
   HTTPS，不用装 Caddy（内网 / 不想多一个进程时用这个）；
2. **反代终止**——Caddy 拿 Let's Encrypt 证书，服务端只监听 ``127.0.0.1:8000``
   （公网正式部署用这个，见 ``deploy/Caddyfile``）。

内网没有公网域名、也拿不到 Let's Encrypt 时，用
``python -m tonicuisc_server cert <域名或IP>`` 生成一张自签证书。
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import shutil
import subprocess
from pathlib import Path

#: 自签证书默认有效期（天）
DEFAULT_DAYS = 825  # 超过 825 天 iOS / macOS 会直接不认

#: 自签时总是带上这几种写法，本机调试（127.0.0.1 / localhost）都能验过
_ALWAYS_NAMES = ("localhost",)


def ssl_kwargs(settings) -> dict[str, str]:
    """给 uvicorn 的 TLS 参数；没配就返回空 dict（= 明文 HTTP）。"""
    if not settings.tls_enabled:
        return {}
    return {"ssl_certfile": settings.tls_cert, "ssl_keyfile": settings.tls_key}


def cert_paths(out_dir: Path, name: str) -> tuple[Path, Path]:
    """自签证书的落盘位置：``<out_dir>/<name>.crt`` 与 ``.key``。"""
    safe = "".join(ch if ch.isalnum() or ch in ".-_" else "_" for ch in name)
    return out_dir / f"{safe}.crt", out_dir / f"{safe}.key"


def _san_entries(name: str) -> list:
    """构造 subjectAltName 条目（域名或 IP，再补上 localhost / 回环）。"""
    from cryptography import x509

    entries: list = []
    candidates = (name, *_ALWAYS_NAMES, "127.0.0.1", "::1")
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            entries.append(x509.IPAddress(ipaddress.ip_address(candidate)))
        except ValueError:
            entries.append(x509.DNSName(candidate))
    return entries


def _generate_with_cryptography(name: str, cert_path: Path, key_path: Path, days: int) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)  # 自签：签发者就是自己
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))  # 容忍客户端时钟略慢
        .not_valid_after(now + dt.timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName(_san_entries(name)), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def _generate_with_openssl(name: str, cert_path: Path, key_path: Path, days: int) -> None:
    openssl = shutil.which("openssl")
    if not openssl:
        raise RuntimeError("没装 cryptography，PATH 里也没有 openssl，无法生成证书")
    subprocess.run(
        [
            openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-days", str(days),
            "-keyout", str(key_path),
            "-out", str(cert_path),
            "-subj", f"/CN={name}",
            "-addext", f"subjectAltName=DNS:{name},DNS:localhost,IP:127.0.0.1",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def generate_self_signed(name: str, cert_path: Path, key_path: Path, days: int = DEFAULT_DAYS) -> str:
    """生成自签证书，返回实际用到的工具名（``cryptography`` / ``openssl``）。

    优先 ``cryptography``（没装才退回 openssl 命令行）：Windows 上 openssl 不一定有。
    """
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import cryptography  # noqa: F401
    except ImportError:
        _generate_with_openssl(name, cert_path, key_path, days)
        return "openssl"
    _generate_with_cryptography(name, cert_path, key_path, days)
    return "cryptography"
