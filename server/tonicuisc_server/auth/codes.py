"""设备码与恢复码：生成、规范化、哈希。

两者都是「人要抄一遍 / 手输一遍」的短码，所以共用同一套去掉 ``0/O/1/I`` 的
32 字符表（见 ``constants.CODE_ALPHABET``）——以前这里和设备码那边各写了一份，
字母表还不一样。
"""

from __future__ import annotations

import re
import secrets

from .constants import CODE_ALPHABET
from .tokens import hash_password, verify_password


def new_user_code() -> str:
    """设备码（``A1B1-C1D1``）。

    正常由 **App 生成**（服务端从不生成也不打印），这个函数给测试库和「换一个码」
    的提示文案用。
    """
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def normalize_user_code(value: str) -> str:
    """统一成大写、去掉多余字符，用户手输也能对上。"""
    cleaned = re.sub(r"[^A-Za-z0-9]", "", value or "").upper()
    if len(cleaned) == 8:
        return f"{cleaned[:4]}-{cleaned[4:]}"
    return cleaned


def new_recovery_code() -> str:
    """一次性恢复码，形如 ``K7MP2-QX9RT-3WZHB-ND5LF``（明文只在生成时给一次）。"""
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(20))
    return "-".join(raw[i : i + 5] for i in range(0, 20, 5))


def normalize_recovery_code(value: str) -> str:
    """统一成大写、吃掉用户多打的空格横杠，再按 5 位分组。"""
    cleaned = re.sub(r"[^A-Za-z0-9]", "", value or "").upper()
    if len(cleaned) == 20:
        return "-".join(cleaned[i : i + 5] for i in range(0, 20, 5))
    return cleaned


def hash_recovery_code(code: str) -> str:
    """恢复码跟密码用同一套 PBKDF2：库里被拿走也换不走，还顺带抗爆破。"""
    return hash_password(normalize_recovery_code(code))


def verify_recovery_code(code: str, stored: str) -> bool:
    if not stored:
        return False
    return verify_password(normalize_recovery_code(code), stored)
