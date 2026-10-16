"""用 Let's Encrypt 签证书（**DNS-01 验证**，不需要任何入站端口）。

为什么不用 Caddy 自带的 ACME：

* Caddy 内置的 ACME 只会 **HTTP-01 / TLS-ALPN-01**，这两种都要 Let's Encrypt
  能从公网连到你的 80 / 443。大陆家用宽带经常封这两个端口，路由器还得做端口映射，
  任何一环不通就签不下来（实测就是 ``Timeout during connect``）；
* **DNS-01 只要求能往域名写一条 TXT 记录**——我们的 ``deploy/.aliyun.env`` 里
  本来就有云解析权限，所以这条路**不需要入站端口、不需要端口映射**，IPv4 变了也不影响；
* Caddy 官方发行版**不含任何 DNS 模块**（``caddy list-modules`` 里 ``dns.providers`` 为 0），
  所以要么自己编译 Caddy，要么用现成的 ACME 客户端。

这里用 `lego <https://github.com/go-acme/lego>`_：它是 ACME **客户端**（不是 CA），
证书仍然是 **Let's Encrypt** 签发的，只是「怎么证明域名是你的」换成写 DNS 记录。

拿到的证书交给 Caddy 用（Caddyfile 里 ``tls <crt> <key>``），Caddy 就只管终止 TLS。
"""

from __future__ import annotations

import os
import subprocess
import zipfile
from pathlib import Path

from . import download
from .config import ENVIRONMENT, SETTINGS

#: lego 版本（固定，避免「今天能下明天不能」）
LEGO_VERSION = "5.5.2"
_LEGO_RELEASE = f"https://github.com/go-acme/lego/releases/download/v{LEGO_VERSION}"
WINDOWS_ZIP_URL = f"{_LEGO_RELEASE}/lego_v{LEGO_VERSION}_windows_amd64.zip"
LINUX_TAR_URL = f"{_LEGO_RELEASE}/lego_v{LEGO_VERSION}_linux_amd64.tar.gz"

#: Let's Encrypt 生产 / 测试环境
LE_PRODUCTION = "https://acme-v02.api.letsencrypt.org/directory"
LE_STAGING = "https://acme-staging-v02.api.letsencrypt.org/directory"

#: 手动下载时告诉用户去哪拿
DOWNLOAD_PAGE = "https://github.com/go-acme/lego/releases"

#: 证书剩多少天就该续
RENEW_DAYS = 30


def _log(message: str) -> None:
    print(f"[acme] {message}", flush=True)


def is_windows() -> bool:
    return os.name == "nt"


def binary_name() -> str:
    return "lego.exe" if is_windows() else "lego"


def bin_dir() -> Path:
    """和 caddy 放一起：``deploy/<win|linux>/``。"""
    return SETTINGS.caddy_dir / ("win" if is_windows() else "linux")


def state_dir() -> Path:
    """lego 的工作目录（账号、证书都在这下面）。"""
    return SETTINGS.caddy_dir / ".caddy" / "lego"


def cert_dir() -> Path:
    """给 Caddy 用的证书目录（``fullchain.crt`` + ``privkey.key``）。"""
    return SETTINGS.caddy_dir / ".caddy" / "certs"


def cert_paths(domain: str | None = None) -> tuple[Path, Path]:
    """Caddy 要用的证书路径：``<state>/certs/<domain>.crt`` 与 ``.key``。"""
    name = (domain or ENVIRONMENT.domain).strip()
    safe = "".join(ch if ch.isalnum() or ch in ".-_" else "_" for ch in name)
    return cert_dir() / f"{safe}.crt", cert_dir() / f"{safe}.key"


def _aliyun_env() -> dict[str, str]:
    """把 ``deploy/.aliyun.env`` 里的 AK 映射成 lego 认的变量名。

    注意名字不一样：我们自己脚本用 ``ALIYUN_*``，lego 的 alidns provider 用 ``ALICLOUD_*``。
    """
    from .ddns import _load_aliyun_env

    values = _load_aliyun_env()
    access_key = (os.getenv("ALICLOUD_ACCESS_KEY") or values.get("ALIYUN_ACCESS_KEY_ID") or "").strip()
    secret_key = (os.getenv("ALICLOUD_SECRET_KEY") or values.get("ALIYUN_ACCESS_KEY_SECRET") or "").strip()
    env: dict[str, str] = {}
    if access_key:
        env["ALICLOUD_ACCESS_KEY"] = access_key
    if secret_key:
        env["ALICLOUD_SECRET_KEY"] = secret_key
    return env


def find_lego(*, auto_download: bool = True) -> Path | None:
    """找 lego：``TONICUISC_LEGO_SERVER`` → ``deploy/<win|linux>/`` → PATH → 下载。"""
    import shutil as _shutil

    configured = os.getenv("TONICUISC_LEGO_SERVER", "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.exists():
            return candidate

    local = bin_dir() / binary_name()
    if local.exists():
        return local

    found = _shutil.which("lego")
    if found:
        return Path(found)

    if not auto_download:
        return None
    return download_lego()


def download_lego() -> Path:
    """下载并解出 lego 到 ``deploy/<win|linux>/``。"""
    target = bin_dir() / binary_name()
    target.parent.mkdir(parents=True, exist_ok=True)
    url = WINDOWS_ZIP_URL if is_windows() else LINUX_TAR_URL
    _log(f"本地没有 lego，开始下载 v{LEGO_VERSION}（走 gh-proxy 镜像，多个源逐个试）")

    errors: list[str] = []
    for candidate in download.download_candidates(url):
        host = candidate.split("/")[2] if "//" in candidate else candidate
        try:
            _log(f"尝试 {host}")
            archive = download.download_file(candidate, target.parent / f"lego-{LEGO_VERSION}.part-download")
            _extract(archive, target)
            archive.unlink(missing_ok=True)
            _log(f"已就绪：{target}")
            return target
        except Exception as exc:  # 换下一个源
            errors.append(f"{host}: {type(exc).__name__}: {exc}")
            _log(f"  {host} 失败")

    raise RuntimeError(
        "lego 下载失败（试过 "
        + str(len(errors))
        + " 个源）：\n  "
        + "\n  ".join(errors)
        + f"\n手动下载：{DOWNLOAD_PAGE}\n放到：{target}"
    )


def _extract(archive: Path, target: Path) -> None:
    """从 zip（Windows）/ tar.gz（Linux）里解出 lego 可执行文件。"""
    import tarfile

    if is_windows():
        with zipfile.ZipFile(archive) as bundle:
            member = next((name for name in bundle.namelist() if name.endswith("lego.exe")), None)
            if member is None:
                raise RuntimeError(f"{archive.name} 里没有 lego.exe")
            with bundle.open(member) as src, target.open("wb") as dst:
                import shutil as _shutil

                _shutil.copyfileobj(src, dst)
    else:
        with tarfile.open(archive, "r:gz") as bundle:
            member = next(
                (item for item in bundle.getmembers() if item.isfile() and Path(item.name).name == "lego"),
                None,
            )
            if member is None:
                raise RuntimeError(f"{archive.name} 里没有 lego")
            extracted = bundle.extractfile(member)
            if extracted is None:
                raise RuntimeError(f"{archive.name} 里的 lego 读不出来")
            with extracted, target.open("wb") as dst:
                import shutil as _shutil

                _shutil.copyfileobj(extracted, dst)
        target.chmod(0o755)


def lego_version(path: Path) -> str:
    try:
        result = subprocess.run(
            [str(path), "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (result.stdout or result.stderr or "").strip().splitlines()[0] if result.returncode == 0 else ""


def cert_expires_in_days(crt: Path) -> int | None:
    """证书还有多少天过期（读不出来返回 ``None``）。"""
    try:
        import datetime as dt

        from cryptography import x509
    except ImportError:
        return None
    try:
        data = x509.load_pem_x509_certificate(crt.read_bytes())
        # cryptography 42+ 用 not_valid_after_utc；旧版只有 naive 的那个
        expires = getattr(data, "not_valid_after_utc", None) or data.not_valid_after.replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None
    return (expires - dt.datetime.now(dt.timezone.utc)).days


def needs_renewal(domain: str | None = None, *, days: int = RENEW_DAYS) -> bool:
    """证书不存在 / 空文件 / 快过期 / 读不出来 → 需要（重新）签。"""
    crt, key = cert_paths(domain)
    if not crt.exists() or not key.exists():
        return True
    try:
        # 空文件 / 被截断的文件也算「需要重签」——Caddy 会照样去加载它，
        # 结果是握手失败，而且看起来像证书问题（实测踩过 0 字节的 .crt）
        if crt.stat().st_size == 0 or key.stat().st_size == 0:
            return True
    except OSError:
        return True
    remaining = cert_expires_in_days(crt)
    if remaining is None:
        return True
    return remaining <= days


def obtain(domain: str | None = None, *, email: str = "", ca: str = "", force: bool = False) -> tuple[Path, Path]:
    """用 DNS-01（阿里云解析）向 Let's Encrypt 签一张证书，返回 Caddy 能用的 (crt, key)。

    幂等：证书还有 ``RENEW_DAYS`` 天以上就直接返回现有的，不会重复申请
    （Let's Encrypt 有「同一组域名每周 5 张」的限制，别乱签）。
    """
    name = (domain or ENVIRONMENT.domain).strip()
    if not name:
        raise RuntimeError("不知道给哪个域名签证书")

    crt, key = cert_paths(name)
    if not force and not needs_renewal(name):
        remaining = cert_expires_in_days(crt)
        _log(f"{name} 的证书还有 {remaining} 天，不用重签（要强制就加 --force）")
        return crt, key

    credentials = _aliyun_env()
    if not credentials:
        raise RuntimeError(
            "没有阿里云 AK，签不了（DNS-01 要往解析里写 TXT 记录）。\n"
            "先跑：python -m tonicuisc_server ddns set-key"
        )

    path = find_lego()
    if path is None:
        raise RuntimeError(f"没找到 lego，手动下一个放这里：{bin_dir() / binary_name()}")

    work = state_dir()
    work.mkdir(parents=True, exist_ok=True)
    cert_dir().mkdir(parents=True, exist_ok=True)

    cmd = [
        str(path),
        "run",
        "--accept-tos",
        "--dns", "alidns",
        "--domains", name,
        "--path", str(work),
        "--key-type", "EC256",
        "--renew-days", str(RENEW_DAYS),
        # 关掉 ARI（RFC 9773 的 renewalInfo）。
        # 我们自己按 --renew-days 判断续期时机就够了；而 ARI 会拿**磁盘上那张旧证书**
        # 去问 CA「它是不是该换了」，一旦旧证书是**另一个 CA 签的**（比如先用 staging 试过、
        # 再切生产），CA 就回 404 "Authority Key Identifier did not match a known issuer"，
        # 整个续签直接失败。实测踩过。
        "--ari-disable",
    ]
    if force:
        # **必须显式传 --renew-force**：lego 自己也会判断「还没到期就不签」
        # （实测：不加这个，`--force` 只绕过了我们的检查，lego 照样回
        #  "Skip renewal: ... the renewal can be performed in 33d" 然后什么都不做）
        cmd.append("--renew-force")
        # 别让 lego 在续签前随机等最多 30 秒——手动触发时我们希望立刻动手
        cmd.append("--no-random-sleep")
    if email:
        cmd += ["--email", email]
    if ca:
        cmd += ["--server", ca]

    env = dict(os.environ)
    env.update(credentials)
    _log(f"向 Let's Encrypt 申请 {name}（DNS-01 / 阿里云解析，不需要入站端口）")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=600,
    )
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    if output:
        for line in output.splitlines():
            _log("  " + line)
    if result.returncode != 0:
        raise RuntimeError(f"lego 退出码 {result.returncode}（上面是它的原话）")

    # lego 把证书放在 <path>/certificates/<domain>.crt（**已经是完整链**）+ .issuer.crt
    issued = work / "certificates"
    leaf = issued / f"{name}.crt"
    issuer = issued / f"{name}.issuer.crt"
    private = issued / f"{name}.key"
    for needed in (leaf, private):
        if not needed.exists():
            raise RuntimeError(f"lego 说成功了，但没找到 {needed}")

    # Caddy 要的是**完整链**。lego v5 的 .crt 里本来就含中间证书
    # （实测：leaf 4 条 = 叶子 + 3 张中间证书，而 .issuer.crt 是其中 3 张的重复），
    # 所以**不能无脑拼**——拼了就是重复 3 条、每次握手白传几 KB。
    # 只有当 .crt 里确实只有一张（老版本 lego 的行为）才补上中间证书。
    chain = leaf.read_text(encoding="utf-8")
    if chain.count("BEGIN CERTIFICATE") < 1:
        raise RuntimeError(f"{leaf} 里没有证书（lego 是不是中途失败了？）")
    if chain.count("BEGIN CERTIFICATE") < 2 and issuer.exists():
        chain += issuer.read_text(encoding="utf-8")

    # **先全部准备好、再落盘**：早先先写 .crt 再写 .key，中途出错就会留下
    # 一个 0 字节的 .crt（Caddy 会去加载它，然后握手失败）——实测踩过。
    private_text = private.read_text(encoding="utf-8")
    if "PRIVATE KEY" not in private_text:
        raise RuntimeError(f"{private} 不像私钥")

    crt.write_text(chain, encoding="utf-8")
    key.write_text(private_text, encoding="utf-8")
    try:
        key.chmod(0o600)
    except OSError:
        pass

    remaining = cert_expires_in_days(crt)
    _log(f"证书已就绪：{crt}（{remaining} 天后过期）")
    return crt, key


def ensure(domain: str | None = None, *, email: str = "", ca: str = "") -> tuple[Path, Path] | None:
    """需要就签、不需要就跳过；**失败只报错不抛**（服务端要照常跑）。"""
    try:
        return obtain(domain, email=email, ca=ca)
    except Exception as exc:
        _log(f"证书没搞定：{exc}")
        return None


__all__ = [
    "DOWNLOAD_PAGE",
    "LEGO_VERSION",
    "LE_PRODUCTION",
    "LE_STAGING",
    "RENEW_DAYS",
    "cert_expires_in_days",
    "cert_paths",
    "download_lego",
    "ensure",
    "find_lego",
    "lego_version",
    "needs_renewal",
    "obtain",
]
