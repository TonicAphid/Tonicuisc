"""本地自检 Dart 括号配平，改完代码先跑一遍，别等 CI。

按字符扫描：跳过 `//`、`/* */`、`'...'`、`"..."`、`r'...'` 以及 `${...}` 插值里的字符串，
只统计真正参与结构的括号。用法（仓库根目录）：

    python tool/check_dart_balance.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAIRS = {"(": ")", "{": "}", "[": "]"}


def scan_brackets(source: str) -> dict[str, int]:
    counts = {ch: 0 for ch in PAIRS} | {ch: 0 for ch in PAIRS.values()}
    quote_starts = ("'", '"')
    i, n = 0, len(source)
    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""

        if ch == "/" and nxt == "/":  # 行注释
            end = source.find("\n", i)
            i = n if end == -1 else end
            continue
        if ch == "/" and nxt == "*":  # 块注释
            end = source.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        if ch == "r" and nxt in quote_starts:  # 原始字符串
            i += 2
            while i < n and source[i] != nxt:
                i += 1
            i += 1
            continue
        if ch in quote_starts:  # 普通字符串
            quote = ch
            i += 1
            while i < n:
                if source[i] == "\\":
                    i += 2
                    continue
                if source[i] == quote:
                    i += 1
                    break
                if source[i] == "$" and i + 1 < n and source[i + 1] == "{":
                    depth = 0
                    i += 1
                    while i < n:  # 插值内部：跳过嵌套字符串，按花括号配对
                        inner = source[i]
                        if inner in quote_starts:
                            nested_quote = inner
                            i += 1
                            while i < n and source[i] != nested_quote:
                                if source[i] == "\\":
                                    i += 1
                                i += 1
                            i += 1
                            continue
                        if inner == "{":
                            depth += 1
                        elif inner == "}":
                            depth -= 1
                            if depth == 0:
                                i += 1
                                break
                        i += 1
                    continue
                i += 1
            continue

        if ch in counts:
            counts[ch] += 1
        i += 1
    return counts


def main() -> int:
    targets = sorted((ROOT / "app" / "lib").rglob("*.dart")) + sorted((ROOT / "app" / "test").rglob("*.dart"))
    bad = 0
    for path in targets:
        counts = scan_brackets(path.read_text(encoding="utf-8"))
        problems = [
            f"{opening}{closing}={counts[opening]}/{counts[closing]}"
            for opening, closing in PAIRS.items()
            if counts[opening] != counts[closing]
        ]
        if problems:
            bad += 1
            print(f"BAD  {path.relative_to(ROOT)}  " + "  ".join(problems))
    if bad:
        print(f"\n{bad} 个文件括号不配平（多半是少写了一个 ) ）")
    else:
        print(f"OK   {len(targets)} 个 dart 文件括号配平")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
