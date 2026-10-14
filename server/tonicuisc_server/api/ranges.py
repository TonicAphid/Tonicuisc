"""HTTP Range 解析与本地文件分块读取（``/api/stream`` 用）。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from fastapi import HTTPException


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Return an inclusive (start, end) byte range, or None for "whole file"."""
    if not header or not header.startswith("bytes=") or size <= 0:
        return None
    spec = header[len("bytes=") :].split(",")[0].strip()
    start_s, _, end_s = spec.partition("-")
    try:
        if not start_s:  # suffix range: bytes=-500
            length = int(end_s or 0)
            if length <= 0:
                return None
            return max(size - length, 0), size - 1
        start = int(start_s)
        end = int(end_s) if end_s else size - 1
    except ValueError:
        # RFC 7233：Range 头是垃圾就当没带（返回整个文件），别让 int() 炸成 500
        return None
    if start >= size or end < start:
        # 起点超出文件、或 start > end（会算出负的 Content-Length）→ 416
        raise HTTPException(
            status_code=416,
            detail="Range not satisfiable",
            headers={"Content-Range": f"bytes */{size}"},
        )
    return start, min(end, size - 1)


def file_chunks(path: Path, start: int, end: int, chunk_size: int = 512 * 1024) -> Iterator[bytes]:
    remaining = end - start + 1
    with path.open("rb") as fp:
        fp.seek(start)
        while remaining > 0:
            data = fp.read(min(chunk_size, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data
