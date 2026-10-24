"""酷狗 KRC 编解码。

KRC 不是私有加密：去掉 4 字节头之后按固定 key 逐字节 XOR，再 zlib 解压。
明文每行是 ``[行开始,行时长]<字开始,字时长,0>字…``，字时间是**相对行首**的。

多语言（``[language:base64]``）里：``type 0`` 是逐字罗马音（日语 romaji /
部分中文拼音），``type 1`` 是逐行翻译。两档都经常为空，所以要按「有就填」处理。
"""

from __future__ import annotations

import base64
import json
import re
import zlib
from dataclasses import replace

from .models import LyricsLine, LyricsWord

#: 文件头（第 5 个字节是 '8' 的一部分，解码时只去掉前 4 个）
KRC_MAGIC = b"krc18"

#: 公开的固定混淆 key（不是加密密钥）
KRC_KEY = b"@Gaw^2tGQ61-\xce\xd2ni"

_TAG_RE = re.compile(r"^\[(\w+):([^\]]*)\]$")
_LINE_RE = re.compile(r"^\[(\d+),(\d+)\](.*)$")
_WORD_RE = re.compile(r"<(\d+),(\d+),\d+>([^<]*)")


def decode_krc(blob: bytes) -> str:
    """解密 KRC 字节流 → 明文文本。

    ``blob`` 是 ``lyrics.kugou.com/download`` 返回的 base64 解码后的内容，
    以 ``krc18`` 开头。
    """
    if not blob:
        raise ValueError("空的 KRC 数据")
    # 与 LDDC/酷狗客户端一致：只去掉前 4 字节，保留 '8'——
    # 它 XOR 之后正好是 zlib 的 0x78 头，去掉 5 字节反而解不开。
    body = bytearray(blob)[4:]
    plain = bytearray(b ^ KRC_KEY[i % len(KRC_KEY)] for i, b in enumerate(body))
    try:
        return zlib.decompress(plain).decode("utf-8")
    except zlib.error as exc:
        raise ValueError(f"KRC 解压失败: {exc}") from exc


def parse_krc(text: str) -> tuple[LyricsLine, ...]:
    """明文 KRC → 逐行 + 逐字时间轴（带翻译/罗马音）。"""
    tags: dict[str, str] = {}
    timed: list[tuple[int, int, tuple[LyricsWord, ...], bool]] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        tag = _TAG_RE.match(line)
        if tag:
            tags[tag.group(1)] = tag.group(2)
            continue
        match = _LINE_RE.match(line)
        if not match:
            continue
        line_start = int(match.group(1))
        line_end = line_start + int(match.group(2))
        words = tuple(
            LyricsWord(
                start_ms=line_start + int(word.group(1)),
                end_ms=line_start + int(word.group(1)) + int(word.group(2)),
                text=word.group(3),
            )
            for word in _WORD_RE.finditer(match.group(3))
        )
        if not words:
            plain = match.group(3).strip()
            if plain:
                words = (LyricsWord(line_start, line_end, plain),)
        # 空行也要保留在 timed 里：language 的两档都跳过空行，要靠 offset 对齐
        timed.append((line_start, line_end, words, not words or all(not w.text for w in words)))

    romaji_lines, translation_lines = _language_tracks(tags.get("language", ""))
    lines: list[LyricsLine] = []
    offset = 0
    for index, (line_start, line_end, words, is_empty) in enumerate(timed):
        if is_empty or not words:
            offset += 1
            continue
        track_index = index - offset
        if 0 <= track_index < len(romaji_lines):
            words = _attach_romaji(words, romaji_lines[track_index])
        translation = _join_line(translation_lines[track_index]) if 0 <= track_index < len(translation_lines) else ""
        roman = "".join(word.roman for word in words).strip()
        lines.append(LyricsLine(line_start, line_end, words, translation=translation, roman=roman))
    return tuple(lines)


def _language_tracks(encoded: str) -> tuple[list, list]:
    """解析 ``[language:base64]``，返回 (逐字罗马音, 逐行翻译)。"""
    if not encoded:
        return [], []
    try:
        payload = json.loads(base64.b64decode(encoded).decode("utf-8"))
    except (ValueError, TypeError, UnicodeDecodeError):
        return [], []
    romaji: list = []
    translation: list = []
    for item in payload.get("content") or []:
        if not isinstance(item, dict):
            continue
        content = item.get("lyricContent")
        if not isinstance(content, list):
            continue
        if item.get("type") == 0:
            romaji = content
        elif item.get("type") == 1:
            translation = content
    return romaji, translation


def _attach_romaji(words: tuple[LyricsWord, ...], track: object) -> tuple[LyricsWord, ...]:
    if not isinstance(track, list):
        return words
    out: list[LyricsWord] = []
    for index, word in enumerate(words):
        roman = track[index] if index < len(track) else ""
        out.append(replace(word, roman=str(roman)))
    return tuple(out)


def _join_line(track: object) -> str:
    if not isinstance(track, list):
        return ""
    return "".join(str(part) for part in track if str(part).strip()).strip()
