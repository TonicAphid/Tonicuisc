"""逐字歌词：KRC 编解码、匹配、缓存与后台抓取。全部离线。"""

from __future__ import annotations

import base64
import json
import time
import zlib
from types import SimpleNamespace

import pytest

from conftest import make_storage
from tonicuisc_server.lyrics.clean import strip_header
from tonicuisc_server.lyrics.krc import KRC_KEY, decode_krc, parse_krc
from tonicuisc_server.lyrics.matcher import score
from tonicuisc_server.lyrics.models import EnhancedLyrics, LyricsLine, LyricsWord
from tonicuisc_server.lyrics.service import LyricService


def _krc_blob(plain: str) -> bytes:
    """把明文按酷狗的方式「加密」：去掉前 4 字节后 XOR，正好还原 zlib 流。"""
    packed = zlib.compress(plain.encode("utf-8"))
    masked = bytes(byte ^ KRC_KEY[i % len(KRC_KEY)] for i, byte in enumerate(packed))
    return b"krc1" + masked


PLAIN = (
    "[ti:测试歌]\n"
    "[ar:测试歌手]\n"
    "[0,1000]<0,200,0>你<200,200,0>好\n"
    "[1000,500]<0,500,0>世界\n"
)


def test_decode_and_parse_krc() -> None:
    text = decode_krc(_krc_blob(PLAIN))
    assert text == PLAIN

    lines = parse_krc(text)

    assert len(lines) == 2
    assert [word.text for word in lines[0].words] == ["你", "好"]
    assert lines[0].words[0].start_ms == 0
    assert lines[0].words[1].start_ms == 200
    assert lines[0].words[1].end_ms == 400
    # 第二行的字时间是相对行首的
    assert lines[1].start_ms == 1000
    assert lines[1].words[0].start_ms == 1000
    assert lines[1].words[0].end_ms == 1500


def test_parse_krc_keeps_untagged_line_as_single_word() -> None:
    lines = parse_krc("[0,800]没有逐字\n")
    assert len(lines) == 1
    assert lines[0].text == "没有逐字"
    assert lines[0].words[0].start_ms == 0


def test_parse_krc_reads_translation_and_romaji() -> None:
    language = {
        "content": [
            {"type": 0, "lyricContent": [["ko", "n"], ["ni", "chi"]]},
            {"type": 1, "lyricContent": [["你好"], ["今天"]]},
        ]
    }
    encoded = base64.b64encode(json.dumps(language, ensure_ascii=False).encode("utf-8")).decode("ascii")
    plain = (
        f"[language:{encoded}]\n"
        "[0,1000]<0,200,0>こ<200,200,0>ん\n"
        "[1000,1000]<0,200,0>に<200,200,0>ち\n"
    )

    lines = parse_krc(plain)

    assert [word.roman for word in lines[0].words] == ["ko", "n"]
    assert lines[0].roman == "kon"
    assert lines[0].translation == "你好"
    assert lines[1].translation == "今天"


def test_language_tracks_skip_empty_lines() -> None:
    """Language 两档跳过空行，时间轴里的空行要靠 offset 对齐。"""
    language = {
        "content": [
            {"type": 0, "lyricContent": [["hi"]]},
            {"type": 1, "lyricContent": [["嗨"]]},
        ]
    }
    encoded = base64.b64encode(json.dumps(language).encode("utf-8")).decode("ascii")
    plain = (
        f"[language:{encoded}]\n"
        "[0,500]\n"
        "[500,500]<0,500,0>word\n"
    )

    lines = parse_krc(plain)

    assert len(lines) == 1
    assert lines[0].translation == "嗨"
    assert lines[0].roman == "hi"


def test_fill_pinyin_adds_line_but_keeps_original() -> None:
    pytest.importorskip("pypinyin")

    from tonicuisc_server.lyrics.pinyin import fill_pinyin

    lines = (LyricsLine(0, 1000, (LyricsWord(0, 500, "晴"), LyricsWord(500, 1000, "天"))),)
    filled = fill_pinyin(lines)

    assert filled[0].text == "晴天", "原文不能被拼音替换"
    assert filled[0].roman
    assert all(word.roman for word in filled[0].words)


def test_fill_pinyin_leaves_existing_roman_alone() -> None:
    pytest.importorskip("pypinyin")

    from tonicuisc_server.lyrics.pinyin import fill_pinyin

    lines = (LyricsLine(0, 1000, (LyricsWord(0, 1000, "こん"),), roman="kon"),)
    assert fill_pinyin(lines)[0].roman == "kon"


def test_to_enhanced_lrc() -> None:
    lyrics = EnhancedLyrics(
        provider="kugou",
        lines=(
            LyricsLine(0, 1000, (LyricsWord(0, 200, "你"), LyricsWord(200, 400, "好"))),
            LyricsLine(1000, 1500, (LyricsWord(1000, 1500, "世界"),)),
        ),
    )

    assert lyrics.to_lrc().splitlines() == [
        "[00:00.00]<00:00.00>你<00:00.20>好<00:01.00>",
        "[00:01.00]<00:01.00>世界<00:01.50>",
    ]


def test_header_line_is_removed() -> None:
    lines = (
        LyricsLine(0, 1000, (LyricsWord(0, 1000, "晴天 - 周杰伦 (Jay Chou)"),)),
        LyricsLine(1000, 2000, (LyricsWord(1000, 2000, "故事的小黄花"),)),
    )

    cleaned = strip_header(lines, "晴天", "周杰伦")

    assert len(cleaned) == 1
    assert cleaned[0].text == "故事的小黄花"


def test_matcher_prefers_title_and_duration() -> None:
    good = score(
        title="晴天", artists="周杰伦", duration_s=269,
        cand_title="晴天", cand_artists="周杰伦", cand_duration_s=269,
    )
    live = score(
        title="晴天", artists="周杰伦", duration_s=269,
        cand_title="晴天 (Live)", cand_artists="周杰伦", cand_duration_s=299,
    )
    unrelated = score(
        title="晴天", artists="周杰伦", duration_s=269,
        cand_title="稻香", cand_artists="周杰伦", cand_duration_s=223,
    )

    assert good > live > unrelated
    assert unrelated < 0.6, "完全不同的歌不能过阈值"


class _FakeProvider:
    name = "fake"

    def __init__(self, result: EnhancedLyrics | None) -> None:
        self.result = result
        self.calls = 0

    def fetch(self, song) -> EnhancedLyrics | None:
        self.calls += 1
        return self.result


SONG = SimpleNamespace(song_name="测试歌", singers="测试歌手", duration_s=1.5)


def _wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _sample() -> EnhancedLyrics:
    return EnhancedLyrics(provider="fake", lines=(LyricsLine(0, 1000, (LyricsWord(0, 1000, "你"),)),))


def test_service_fetches_in_background_and_caches() -> None:
    storage = make_storage(namespace="lyric-test")
    provider = _FakeProvider(_sample())
    service = LyricService(storage, providers=[provider], max_workers=1)
    try:
        first = service.enhanced("item-1", SONG)
        assert first == {"format": "enhanced", "ready": False}

        assert _wait_for(lambda: storage.load_lyrics("item-1") is not None)
        second = service.enhanced("item-1", SONG)

        assert second["ready"] is True
        assert second["provider"] == "fake"
        assert second["lines"][0]["text"] == "你"
        assert provider.calls == 1, "第二次必须命中缓存"
    finally:
        service.close()
        storage.close()


def test_service_caches_miss() -> None:
    storage = make_storage(namespace="lyric-miss")
    provider = _FakeProvider(None)
    service = LyricService(storage, providers=[provider], max_workers=1)
    try:
        service.enhanced("item-2", SONG)
        assert _wait_for(lambda: storage.load_lyrics("item-2") is not None)

        again = service.enhanced("item-2", SONG)
        assert again == {"format": "enhanced", "ready": False}
        assert provider.calls == 1, "负缓存期内不该反复打上游"
    finally:
        service.close()
        storage.close()


def test_service_dedupes_inflight() -> None:
    storage = make_storage(namespace="lyric-dedupe")

    class SlowProvider(_FakeProvider):
        def fetch(self, song):
            time.sleep(0.1)
            return super().fetch(song)

    provider = SlowProvider(_sample())
    service = LyricService(storage, providers=[provider], max_workers=2)
    try:
        for _ in range(3):
            service.enhanced("item-3", SONG)
        assert _wait_for(lambda: storage.load_lyrics("item-3") is not None)
        assert provider.calls == 1, "同一首歌并发只该跑一个任务"
    finally:
        service.close()
        storage.close()


def test_provider_exception_falls_back_to_next_source() -> None:
    storage = make_storage(namespace="lyric-fallback")

    class Broken:
        name = "broken"

        def fetch(self, song):
            raise RuntimeError("上游挂了")

    good = _FakeProvider(_sample())
    service = LyricService(storage, providers=[Broken(), good], max_workers=1)  # type: ignore[list-item]
    try:
        result = service.prepare("item-4", SONG)
        assert result["ready"] is True
        assert good.calls == 1
    finally:
        service.close()
        storage.close()
