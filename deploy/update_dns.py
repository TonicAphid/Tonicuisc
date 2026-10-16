"""维护 tonicaphid.cn 下的 A / AAAA 记录（家里部署：外部 v4 + v6 双栈）。

背景：家里部署、不买服务器，外面既走 IPv4 也走 IPv6（哪个通用哪个）。
家宽的地址**会变**——IPv6 前缀重播就可能换一段，IPv4 也可能变——所以需要定期把
A / AAAA 记录刷成当前值。

**在托管那个域名的那台机器上跑**：脚本探测的是「本机」的公网地址。
每台机器的 IPv6 后缀都不一样（同一家里两条线可能是同一个 IPv4、不同 IPv6），
所以 uisc 要在部署机跑、tuisc 在这台测试机跑。

用法：

    python deploy/update_dns.py                       # 默认 uisc，只探测并打印
    python deploy/update_dns.py --record tuisc.tonicaphid.cn
    python deploy/update_dns.py --update              # 真的写进阿里云解析
    python deploy/update_dns.py --only v6             # 只维护 AAAA
    python deploy/update_dns.py --ipv4 1.2.3.4 --ipv6 240e:...   # 手动指定（探测不到时）

阿里云 AK 放 deploy/.aliyun.env（见 .aliyun.env.example），或设环境变量
ALIYUN_ACCESS_KEY_ID / ALIYUN_ACCESS_KEY_SECRET。建议用 RAM 子账号，权限只给云解析 DNS。

── 两个必须知道的坑 ──────────────────────────────────────────────────────
1. **IPv6 要用稳定地址，不是临时地址**：网卡上通常有两个全局 IPv6——
   稳定地址（接口标识固定）和临时地址（隐私扩展，会轮换）。
   系统默认拿**临时地址**出网，所以探测服务回的是临时地址；但对外提供服务要用**稳定地址**。
   两者前缀相同属于正常（不是挂了代理），脚本按前缀判断。
   Windows 用 `Get-NetIPAddress`（SuffixOrigin=Link 是稳定、Random 是临时）；
   Linux 用 `ip -6 addr`。**注意 Linux 上 `use_tempaddr=0` 时根本没有 `temporary`
   地址**，一台机器可能同时有两条都算「稳定」的全局地址（内核按 MAC 自动配置的
   EUI-64，以及 DHCPv6 / 随机 IID 的），脚本按 **EUI-64 优先** 来选。
2. **别只加 AAAA 而忘了 A**：只留一条的话，另一族网络下的客户端会白等一轮超时。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

# 中文输出别被 GBK 控制台搞崩：统一成 UTF-8（重配失败也不影响主流程）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

#: 正式域名（App 全部访问走它，双栈）
DEFAULT_RECORD = "uisc.tonicaphid.cn"
#: 测试域名（这台电脑自己用）
TEST_RECORD = "tuisc.tonicaphid.cn"

ALIYUN_ENDPOINT = "https://alidns.aliyuncs.com/"

#: AK 可以放这里，免得密钥出现在命令行 / 对话记录里（已在 .gitignore）
ENV_FILE = Path(__file__).resolve().parent / ".aliyun.env"

#: 公网 IPv4 探测源。**HTTP 优先**：部分网络环境下 HTTPS 出网会被拦（实测过）。
IPV4_PROBES = (
    "http://ip.3322.net",
    "http://members.3322.org/dyndns/getip",
    "http://ip-api.com/line/?fields=query",
    "https://ifconfig.me/ip",
)

#: 只认 IPv6 的探测源（回的是系统实际出网用的那个地址，通常是临时地址）
IPV6_PROBES = (
    "https://api6.ipify.org",
    "https://ipv6.icanhazip.com",
    "https://api-ipv6.ip.sb/ip",
)

IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
IPV6_RE = re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){2,}[0-9a-fA-F]{0,4}\b")


# ------------------------------------------------------------------ 探测
def _http_get(url: str, timeout: int = 10) -> str | None:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _valid_ipv4(value: str) -> bool:
    parts = value.split(".")
    return len(parts) == 4 and all(part.isdigit() and 0 <= int(part) <= 255 for part in parts)


def probe_public_ipv4() -> str | None:
    """本机的公网 IPv4（从外部服务看到的）。"""
    for url in IPV4_PROBES:
        body = _http_get(url)
        if not body:
            continue
        for candidate in IPV4_RE.findall(body):
            if _valid_ipv4(candidate) and not candidate.startswith("127."):
                return candidate
    return None


def probe_outbound_ipv6() -> str | None:
    """系统实际出网用的 IPv6（通常是隐私扩展的临时地址）。"""
    for url in IPV6_PROBES:
        body = _http_get(url)
        if not body:
            continue
        for candidate in IPV6_RE.findall(body.strip()):
            low = candidate.lower()
            if low.startswith(("fe80", "fc", "fd")) or low == "::1":
                continue
            return candidate
    return None


def _powershell_json(command: str) -> list[dict]:
    """调 PowerShell 拿网卡信息；拿不到就返回空列表（非 Windows 等）。"""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0 or not (result.stdout or "").strip():
        return []
    try:
        data = json.loads(result.stdout)
    except ValueError:
        return []
    return data if isinstance(data, list) else [data]


def _is_eui64(ip: str) -> bool:
    """接口标识是不是 EUI-64（按网卡 MAC 生成）。

    EUI-64 的 IID 里固定插了 ``ff:fe``（展开成 8 段后落在第 6、7 段），
    用它把「跟 MAC 绑定、永不变」的地址和随机 / DHCPv6 地址区分开。
    """
    try:
        groups = ipaddress.IPv6Address(ip).exploded.split(":")
    except ValueError:
        return False
    return len(groups) == 8 and groups[5].endswith("ff") and groups[6].startswith("fe")


def _linux_global_ipv6() -> list[dict]:
    """Linux：用 ``ip -6 addr`` 拿全局 IPv6（排除回环 / 链路本地 / ULA）。

    临时地址（隐私扩展，会轮换）标成 ``SuffixOrigin=Random``，这样
    :func:`pick_local_ipv6` 那套「稳定优先」的逻辑可以**原样复用**——
    没有这一步的话，Debian 上会走到 ``powershell`` 分支拿到空列表，
    误报「本机没有全局 IPv6 地址」（实测踩过）。

    一台机器可能同时有**好几条**全局地址（本机实测：内核按 MAC 自动配置的
    EUI-64 一条、DHCPv6 / 随机 IID 又一条），而它们的 ``SuffixOrigin`` 都算「稳定」。
    ``ip`` 的输出顺序不定（实测随机那条排在前面），所以这里**显式排序**：
    稳定 → EUI-64 → 内核 RA，保证 :func:`pick_local_ipv6` 取到的是最稳的那个。
    """
    try:
        result = subprocess.run(
            ["ip", "-o", "-6", "addr", "show", "scope", "global"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    usable: list[dict] = []
    for line in (result.stdout or "").splitlines():
        # 形如：2: enp2s0    inet6 240e:...:c0c5/64 scope global [temporary] dynamic
        parts = line.split()
        if len(parts) < 4 or parts[2] != "inet6":
            continue
        iface = parts[1]
        ip = parts[3].split("/")[0]
        low = ip.lower()
        if not ip or low == "::1" or low.startswith(("fe80", "fc", "fd")):
            continue
        if "deprecated" in parts:
            # 已弃用、快失效了：发出去只会让人连不上
            continue
        usable.append(
            {
                "IPAddress": ip,
                "PrefixOrigin": "",
                # 有 temporary 标记 = 隐私扩展的临时地址（会轮换）；否则当稳定地址
                "SuffixOrigin": "Random" if "temporary" in parts else "Link",
                "InterfaceAlias": iface,
                # 排序用（不参与对外结构）：EUI-64 最稳，其次内核 RA 自动配置的
                "_eui64": _is_eui64(ip),
                "_from_ra": "kernel_ra" in parts,
            }
        )
    # ip 的输出顺序不定（实测 DHCPv6 那条排在 EUI-64 前面，导致发错地址），
    # 这里显式排序：稳定 → EUI-64 → 内核 RA。
    usable.sort(key=lambda row: (row["SuffixOrigin"] == "Random", not row["_eui64"], not row["_from_ra"]))
    return usable


def local_global_ipv6() -> list[dict]:
    """本机网卡上的全局 IPv6（排除回环 / 链路本地 / ULA）。

    Windows 用 PowerShell，Linux 用 ``ip -6 addr``。
    `SuffixOrigin` 是枚举，`ConvertTo-Json` 默认会转成数字（Link=4、Random=5），
    所以显式取字符串，免得「稳定地址优先」的判断失效。
    """
    if sys.platform != "win32":
        return _linux_global_ipv6()

    rows = _powershell_json(
        "Get-NetIPAddress -AddressFamily IPv6 -ErrorAction SilentlyContinue | ForEach-Object { "
        "[pscustomobject]@{ IPAddress = [string]$_.IPAddress; "
        "PrefixOrigin = [string]$_.PrefixOrigin; "
        "SuffixOrigin = [string]$_.SuffixOrigin; "
        "InterfaceAlias = [string]$_.InterfaceAlias } } | ConvertTo-Json -Compress"
    )
    usable: list[dict] = []
    for row in rows:
        ip = str(row.get("IPAddress") or "")
        if not ip or ip == "::1":
            continue
        low = ip.lower()
        if low.startswith(("fe80", "fc", "fd")):
            continue
        if str(row.get("PrefixOrigin") or "") in {"WellKnown", "2"}:
            continue
        usable.append(row)
    return usable


def _prefix(ip: str) -> str:
    """取 /64 前缀（家宽前缀变了就是「换了一段」，用它判断更稳）。"""
    return ":".join(ip.split(":")[:4]).lower()


def pick_local_ipv6(rows: list[dict], override: str) -> tuple[str, str]:
    """选本机要对外用的 IPv6：**稳定地址优先**（临时地址会轮换）。"""
    if override:
        return override.strip(), "命令行指定"
    if not rows:
        raise SystemExit(
            "本机没有全局 IPv6 地址：确认宽带给了 IPv6"
            "（Windows 的 ipconfig / Linux 的 `ip -6 addr` 里能看到 240e 开头），"
            "或者用 --ipv6 手动指定。"
        )
    stable = [row for row in rows if str(row.get("SuffixOrigin") or "") != "Random"]
    chosen = (stable or rows)[0]
    kind = "稳定地址" if stable else "临时地址（会轮换，建议 --ipv6 固定一个）"
    return str(chosen.get("IPAddress")), f"{chosen.get('InterfaceAlias')} / {chosen.get('SuffixOrigin')} / {kind}"


# ------------------------------------------------------------------ 阿里云
def load_env_file(path: Path) -> dict[str, str]:
    """读 ``deploy/.aliyun.env``：``KEY=VALUE``，支持 # 注释与引号。

    用 ``utf-8-sig``：Windows 记事本 / PowerShell 存 UTF-8 常带 BOM，
    带着 BOM 第一行的 key 会变成 ``\\ufeffALIYUN_ACCESS_KEY_ID``，白读不到。
    """
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.strip().lstrip("\ufeff")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        if key.strip():
            values[key.strip()] = value
    return values


def aliyun_credentials() -> tuple[str, str, str]:
    """取 AK：**环境变量优先**，没有就读 deploy/.aliyun.env。"""
    env = load_env_file(ENV_FILE)
    access_key_id = (os.getenv("ALIYUN_ACCESS_KEY_ID") or env.get("ALIYUN_ACCESS_KEY_ID") or "").strip()
    access_key_secret = (os.getenv("ALIYUN_ACCESS_KEY_SECRET") or env.get("ALIYUN_ACCESS_KEY_SECRET") or "").strip()
    source = "环境变量" if os.getenv("ALIYUN_ACCESS_KEY_ID") else str(ENV_FILE)
    return access_key_id, access_key_secret, source


def _aliyun_sign(params: dict[str, str], secret: str) -> str:
    """阿里云 RPC 签名（HMAC-SHA1，见阿里云「签名机制」文档）。"""
    encoded = urllib.parse.urlencode(sorted(params.items()), quote_via=urllib.parse.quote)
    string_to_sign = "GET&%2F&" + urllib.parse.quote(encoded, safe="")
    digest = hmac.new(f"{secret}&".encode(), string_to_sign.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def aliyun_call(action: str, access_key_id: str, access_key_secret: str, **extra: str) -> dict:
    params = {
        "Action": action,
        "Format": "JSON",
        "Version": "2015-01-09",
        "AccessKeyId": access_key_id,
        "SignatureMethod": "HMAC-SHA1",
        "SignatureVersion": "1.0",
        "SignatureNonce": uuid.uuid4().hex,
        "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **{key: str(value) for key, value in extra.items() if value is not None},
    }
    params["Signature"] = _aliyun_sign(params, access_key_secret)
    url = ALIYUN_ENDPOINT + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"阿里云接口报错（HTTP {exc.code}）：{body[:300]}")
    except (urllib.error.URLError, OSError) as exc:
        raise SystemExit(f"连不上阿里云 DNS：{exc}")


def guess_zone(record: str) -> str:
    """tonicaphid.cn 从 uisc.tonicaphid.cn 里猜：去掉最左边一段。"""
    parts = record.split(".")
    return ".".join(parts[-2:]) if len(parts) > 2 else record


def list_records(record: str, access_key_id: str, access_key_secret: str, zone: str) -> int:
    """**只读**：把域名下现有的记录列出来。

    专门用来验证「AK 填对了没 + RAM 给没给解析权限」——
    这两种问题阿里云会回不同的错误码（``InvalidAccessKeyId`` / ``Forbidden``），
    这里原样透出来，比让人猜强。
    """
    rr = record.split(".")[0]
    listed = aliyun_call(
        "DescribeDomainRecords",
        access_key_id,
        access_key_secret,
        DomainName=zone,
        RRKeyWord=rr,
        PageSize="100",
    )
    rows = [row for row in ((listed.get("DomainRecords") or {}).get("Record") or []) if row.get("RR") == rr]
    if not rows:
        print(f"{record} 目前没有任何记录（可以加了）。")
        return 0
    print(f"{record} 现有记录：")
    for row in rows:
        print(f"  {str(row.get('Type')):<5} {str(row.get('Value')):<45} TTL={row.get('TTL')}")
    return 0


def sync_record(
    record: str,
    record_type: str,
    value: str,
    access_key_id: str,
    access_key_secret: str,
    zone: str,
) -> str:
    """把某条记录刷成 value：没有就建，值不同就改，相同就不动。"""
    rr = record.split(".")[0]
    listed = aliyun_call(
        "DescribeDomainRecords",
        access_key_id,
        access_key_secret,
        DomainName=zone,
        RRKeyWord=rr,
        TypeKeyWord=record_type,
    )
    rows = [row for row in ((listed.get("DomainRecords") or {}).get("Record") or []) if row.get("RR") == rr]

    if not rows:
        aliyun_call(
            "AddDomainRecord",
            access_key_id,
            access_key_secret,
            DomainName=zone,
            RR=rr,
            Type=record_type,
            Value=value,
            TTL="600",  # 地址会变，TTL 别设太大
        )
        return f"新建 {record_type:<4} {record} -> {value}"

    row = rows[0]
    if row.get("Value") == value:
        return f"不变 {record_type:<4} {record} = {value}"
    aliyun_call(
        "UpdateDomainRecord",
        access_key_id,
        access_key_secret,
        RecordId=row["RecordId"],
        RR=rr,
        Type=record_type,
        Value=value,
        TTL="600",
    )
    return f"更新 {record_type:<4} {record} {row.get('Value')} -> {value}"


# ------------------------------------------------------------------------ main
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="维护 A / AAAA 记录（家里部署：外部 v4 + v6 双栈）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"提示：uisc 在部署机跑，tuisc（{TEST_RECORD}）在这台测试机跑。",
    )
    parser.add_argument("--record", default=os.getenv("TONICUISC_RECORD", DEFAULT_RECORD))
    parser.add_argument("--zone", default=os.getenv("TONICUISC_DOMAIN", ""))
    parser.add_argument("--only", choices=("both", "v4", "v6"), default="both", help="只维护某一族（默认两个都管）")
    parser.add_argument("--ipv4", default="", help="手动指定公网 IPv4（不探测）")
    parser.add_argument("--ipv6", default="", help="手动指定对外 IPv6（不探测本机网卡）")
    parser.add_argument("--update", action="store_true", help="真的写进阿里云解析（不加则只打印）")
    parser.add_argument(
        "--check",
        action="store_true",
        help="只读：列出该域名现有记录（验证 AK 与解析权限，不改任何东西）",
    )
    args = parser.parse_args(argv)

    record = args.record.strip()

    # --check：只读验证，不探测本机地址、不动记录
    if args.check:
        access_key_id, access_key_secret, source = aliyun_credentials()
        if not access_key_id or not access_key_secret:
            print(f"没找到阿里云 AK：环境变量和 {ENV_FILE} 里都没有。")
            print(f"填法见 {ENV_FILE.with_suffix('.env.example')}，或跑 python -m tonicuisc_server ddns set-key")
            return 2
        zone = args.zone or guess_zone(record)
        print(f"域名    ：{record}（zone {zone}）")
        print(f"AK      ：{access_key_id[:8]}…（来自 {source}）")
        return list_records(record, access_key_id, access_key_secret, zone)

    print(f"域名    ：{record}")
    print(f"维护    ：{'A + AAAA' if args.only == 'both' else args.only.upper()}")

    targets: list[tuple[str, str]] = []

    if args.only in {"both", "v4"}:
        ipv4 = args.ipv4.strip() or probe_public_ipv4()
        if ipv4:
            print(f"公网 v4 ：{ipv4}")
            targets.append(("A", ipv4))
        else:
            print("公网 v4 ：探测不到（HTTPS/HTTP 都被挡？可以用 --ipv4 手动指定）")

    if args.only in {"both", "v6"}:
        rows = local_global_ipv6()
        try:
            ipv6, detail = pick_local_ipv6(rows, args.ipv6)
        except SystemExit as exc:
            print(f"本机 v6 ：{exc}")
            ipv6, detail = "", ""
        if ipv6:
            print(f"本机 v6 ：{ipv6}")
            print(f"          （{detail}）")
            if len(rows) > 1:
                for row in rows:
                    mark = " ← 用它" if str(row.get("IPAddress")) == ipv6 else ""
                    print(f"          {row.get('IPAddress')}  [{row.get('SuffixOrigin')}]{mark}")
            outbound = probe_outbound_ipv6()
            if outbound:
                if outbound == ipv6:
                    print(f"出网 v6 ：{outbound}（和上面相同）")
                elif _prefix(outbound) == _prefix(ipv6):
                    print(f"出网 v6 ：{outbound}（同一 /64 前缀，是隐私扩展的临时地址——正常）")
                else:
                    print(f"警告    ：出网 v6 {outbound} 与上面的 {ipv6} **前缀不同**！")
                    print("          说明流量走了 VPN / 代理出口；记录要填本机地址，别填出网地址。")
            targets.append(("AAAA", ipv6))

    if not targets:
        print("\n没有可写入的地址，什么都没做。")
        return 1

    print()
    if not args.update:
        print("要写入的内容（加 --update 才会真的改）：")
        for record_type, value in targets:
            print(f"  {record_type:<4} {record} -> {value}")
        print(f"\nAK 从环境变量或 {ENV_FILE} 读；配好后：python deploy/update_dns.py --record {record} --update")
        return 0

    access_key_id, access_key_secret, source = aliyun_credentials()
    if not access_key_id or not access_key_secret:
        print(f"没找到阿里云 AK：环境变量和 {ENV_FILE} 里都没有。")
        print(f"在 {ENV_FILE} 里写两行即可（已 gitignore）：")
        print("  ALIYUN_ACCESS_KEY_ID=LTAI...")
        print("  ALIYUN_ACCESS_KEY_SECRET=...")
        return 2

    zone = args.zone or guess_zone(record)
    print(f"AK      ：{access_key_id[:8]}…（来自 {source}）")
    print(f"DNS     ：阿里云解析 / {zone}")
    for record_type, value in targets:
        print("  " + sync_record(record, record_type, value, access_key_id, access_key_secret, zone))
    return 0


if __name__ == "__main__":
    sys.exit(main())
