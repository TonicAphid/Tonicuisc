"""缓存策略测试：不联网、不需要 musicdl。"""

from __future__ import annotations

import os
import shutil
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import make_storage
from tonicuisc_server.service import SEARCH_RESIDUE_MAX_AGE, MusicService


@pytest.fixture()
def service() -> MusicService:
    """用项目内的临时目录，避免依赖系统 %TEMP%（CI 与本机都稳定）。"""
    root = Path(__file__).resolve().parent / "_scratch" / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    svc = MusicService()
    svc.work_dir = root / "music"
    svc.files_dir = root / "files"
    svc.work_dir.mkdir(parents=True, exist_ok=True)
    svc.files_dir.mkdir(parents=True, exist_ok=True)
    svc.storage.close()
    svc.storage = make_storage()
    try:
        yield svc
    finally:
        svc.storage.close()
        shutil.rmtree(root, ignore_errors=True)


def test_cache_path_is_stable(service: MusicService) -> None:
    song = SimpleNamespace(source="KuwoMusicClient", identifier="201182467", ext=".flac")
    assert service.cache_path(song).name == "KuwoMusicClient_201182467.flac"


def test_promote_moves_file_and_cleans_run_dir(service: MusicService) -> None:
    run_dir = service.work_dir / "KuwoMusicClient" / "2026-01-01-00-00-00 天地龙鳞"
    run_dir.mkdir(parents=True)
    audio = run_dir / "天地龙鳞 - 201182467.flac"
    audio.write_bytes(b"x" * 10)
    (run_dir / "search_results.pkl").write_bytes(b"p")
    (run_dir / "天地龙鳞 - 201182467.lrc").write_text("[00:00.00] hi", encoding="utf-8")

    target = service._promote(audio, service.files_dir / "KuwoMusicClient_201182467.flac")

    assert target.exists() and target.stat().st_size == 10
    assert target.with_suffix(".lrc").exists()
    assert not run_dir.exists(), "时间戳目录应被清理"


def test_prune_keeps_audio_of_other_songs(service: MusicService) -> None:
    """同一时间戳目录里还有别的歌在下载时，不能删掉它的音频。"""
    run_dir = service.work_dir / "KuwoMusicClient" / "run"
    run_dir.mkdir(parents=True)
    (run_dir / "search_results.pkl").write_bytes(b"p")
    other = run_dir / "another-song.flac"
    other.write_bytes(b"a")

    service._prune(run_dir)

    assert other.exists() and other.stat().st_size == 1
    assert not (run_dir / "search_results.pkl").exists()
    assert run_dir.exists()


def test_prune_search_dirs_keeps_recent_and_audio(service: MusicService) -> None:
    stale = time.time() - SEARCH_RESIDUE_MAX_AGE - 10

    old = service.work_dir / "MiguMusicClient" / "old"
    old.mkdir(parents=True)
    (old / "search_results.pkl").write_bytes(b"p")
    os.utime(old, (stale, stale))

    fresh = service.work_dir / "MiguMusicClient" / "fresh"
    fresh.mkdir()
    (fresh / "search_results.pkl").write_bytes(b"p")

    keep = service.work_dir / "KuwoMusicClient" / "withaudio"
    keep.mkdir(parents=True)
    (keep / "song.flac").write_bytes(b"a")
    os.utime(keep, (stale, stale))

    service._prune_search_dirs()

    assert not old.exists(), "只有 pkl 的旧目录应删除"
    assert fresh.exists(), "刚创建的目录不能删（可能正在下载）"
    assert keep.exists(), "含音频的目录不能删"


class _FakeResponse:
    """只实现 relay() 用到的那几个接口。"""

    def __init__(self, chunks: list[bytes], headers: dict[str, str] | None = None) -> None:
        self._chunks = chunks
        self.headers = headers or {}
        self.closed = False

    def iter_content(self, chunk_size: int = 0):
        yield from self._chunks

    def close(self) -> None:
        self.closed = True


def test_relay_writes_cache_file(service: MusicService) -> None:
    target = service.files_dir / "MiguMusicClient_1.mp3"
    temp = target.with_name(target.name + ".part")
    response = _FakeResponse([b"a" * 10, b"b" * 10], {"Content-Length": "20"})

    data = b"".join(service.relay(response, target, temp))

    assert data == b"a" * 10 + b"b" * 10
    assert target.read_bytes() == data
    assert not temp.exists(), "完成后不应残留 .part"
    assert response.closed


def test_relay_drops_partial_file_on_abort(service: MusicService) -> None:
    target = service.files_dir / "MiguMusicClient_2.mp3"
    temp = target.with_name(target.name + ".part")
    response = _FakeResponse([b"a" * 10, b"b" * 10], {"Content-Length": "20"})

    generator = service.relay(response, target, temp)
    next(generator)
    generator.close()

    assert not target.exists(), "中途断开不能留下半截缓存"
    assert not temp.exists()
    assert response.closed


def test_relay_rejects_short_download(service: MusicService) -> None:
    target = service.files_dir / "MiguMusicClient_3.mp3"
    temp = target.with_name(target.name + ".part")
    response = _FakeResponse([b"a" * 10], {"Content-Length": "20"})

    b"".join(service.relay(response, target, temp))

    assert not target.exists() and not temp.exists()


def test_cached_file_returns_none_when_missing(service: MusicService) -> None:
    song = SimpleNamespace(
        source="MiguMusicClient",
        identifier="404",
        ext=".mp3",
        save_path=str(service.work_dir / "nothing-here.mp3"),
    )
    service._songs["MiguMusicClient:404"] = (song, time.time() + 60)
    assert service.cached_file("MiguMusicClient:404") is None
