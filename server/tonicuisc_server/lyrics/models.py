"""逐字歌词的数据模型（与传输格式）。

这里只放「数据长什么样」和「怎么序列化」，不碰网络、不碰 Redis。
时间单位统一用毫秒整数，客户端自己转 Duration。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class LyricsWord:
    start_ms: int
    end_ms: int
    text: str
    roman: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"s": self.start_ms, "e": self.end_ms, "t": self.text}
        if self.roman:
            payload["r"] = self.roman
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> LyricsWord:
        return cls(
            int(payload["s"]),
            int(payload["e"]),
            str(payload.get("t") or ""),
            str(payload.get("r") or ""),
        )


@dataclass(frozen=True, slots=True)
class LyricsLine:
    start_ms: int
    end_ms: int
    words: tuple[LyricsWord, ...]
    translation: str = ""
    roman: str = ""

    @property
    def text(self) -> str:
        return "".join(word.text for word in self.words)

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start_ms,
            "end": self.end_ms,
            "text": self.text,
            "words": [word.to_dict() for word in self.words],
            "translation": self.translation,
            "roman": self.roman,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> LyricsLine:
        return cls(
            start_ms=int(payload["start"]),
            end_ms=int(payload["end"]),
            words=tuple(LyricsWord.from_dict(word) for word in payload.get("words") or []),
            translation=str(payload.get("translation") or ""),
            roman=str(payload.get("roman") or ""),
        )


@dataclass(frozen=True, slots=True)
class EnhancedLyrics:
    provider: str
    lines: tuple[LyricsLine, ...]
    offset_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "enhanced",
            "ready": True,
            "provider": self.provider,
            "offset_ms": self.offset_ms,
            "lines": [line.to_dict() for line in self.lines],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> EnhancedLyrics:
        return cls(
            provider=str(payload.get("provider") or ""),
            lines=tuple(LyricsLine.from_dict(line) for line in payload.get("lines") or []),
            offset_ms=int(payload.get("offset_ms") or 0),
        )

    def to_lrc(self) -> str:
        """导出增强型 LRC：`[行开始]<字开始>字…<行结束>`。

        只发「字开始」标签，字的结束时间由下一个标签（或行尾标签）决定——
        这是 A2 扩展最常见的写法，也不会出现连续两个相同时间戳。
        """
        out: list[str] = []
        for line in self.lines:
            head = f"[{_stamp(line.start_ms)}]"
            if not line.words:
                out.append(head + line.text)
                continue
            body = "".join(f"<{_stamp(word.start_ms)}>{word.text}" for word in line.words)
            tail = max(line.end_ms, line.words[-1].end_ms)
            out.append(head + body + f"<{_stamp(tail)}>")
        return "\n".join(out)


def _stamp(ms: int) -> str:
    """毫秒 → `mm:ss.xx`（增强 LRC 常用两位小数）。"""
    ms = max(int(ms), 0)
    minutes, rest = divmod(ms, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{minutes:02d}:{seconds:02d}.{millis // 10:02d}"
