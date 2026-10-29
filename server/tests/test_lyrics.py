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


# ------------------------------------------------------- 署名行（「简介」）不能被吞
def _line(start: int, text: str) -> LyricsLine:
    return LyricsLine(start, start + 1000, (LyricsWord(start, start + 1000, text),))


def test_credit_lines_are_kept() -> None:
    """``词：周杰伦`` 这类署名行以前会因为含歌手名被当成头部删掉。"""
    lines = (
        _line(0, "词：周杰伦"),
        _line(1000, "曲：周杰伦"),
        _line(2000, "编曲：洪敬尧"),
        _line(3000, "故事的小黄花"),
    )

    cleaned = strip_header(lines, "晴天", "周杰伦")

    assert [line.text for line in cleaned] == ["词：周杰伦", "曲：周杰伦", "编曲：洪敬尧", "故事的小黄花"]


def test_title_only_line_is_kept() -> None:
    """只有歌名、没有歌手的一行不敢删：正文第一句也可能正好是歌名。"""
    lines = (_line(0, "晴天"), _line(1000, "故事的小黄花"))
    assert len(strip_header(lines, "晴天", "周杰伦")) == 2


def test_two_line_header_is_removed() -> None:
    """「歌名」一行 + 「歌手」一行的老式头部照删。"""
    lines = (_line(0, "晴天"), _line(1000, "周杰伦"), _line(2000, "故事的小黄花"))
    cleaned = strip_header(lines, "晴天", "周杰伦")
    assert [line.text for line in cleaned] == ["故事的小黄花"]


#: 截图里那行：歌名带影视说明，两位歌手各自还带括号英文名
_MULTI_ARTIST_HEADER = "缘分一道桥 (《长城》电影片尾曲) - 王力宏 (Leehom Wang)/谭维维 (Sitar Tan)"


@pytest.mark.parametrize(
    "artists",
    [
        "王力宏/谭维维",
        "王力宏、谭维维",
        "王力宏 (Leehom Wang)/谭维维 (Sitar Tan)",
        "王力宏",  # 元信息只给了第一歌手，也要能认出来
    ],
)
def test_header_with_multiple_artists_and_latin_names(artists: str) -> None:
    """``王力宏`` 和 ``谭维维`` 中间夹着 ``(Leehom Wang)/``，整串匹配不到，要按单个名字比。"""
    lines = (_line(0, _MULTI_ARTIST_HEADER), _line(1000, "秦时明月汉时关"))
    cleaned = strip_header(lines, "缘分一道桥", artists)

    assert [line.text for line in cleaned] == ["秦时明月汉时关"]


def test_artist_keys_split() -> None:
    from tonicuisc_server.lyrics.clean import artist_keys

    assert "王力宏" in artist_keys("王力宏 (Leehom Wang)/谭维维 (Sitar Tan)")
    assert "谭维维" in artist_keys("王力宏、谭维维")
    assert "周杰伦" in artist_keys("周杰伦 feat. 杨瑞代")
    assert artist_keys("") == set()


# ---------------------------------------------- 制作名单块（按结构，不靠角色词表）
#: 《天地龙鳞》开头那一整块（截图里被注音的就是它）
_CREDIT_BLOCK = (
    "词：方文山",
    "曲：王力宏",
    "编曲：唐达",
    "制作人：王力宏",
    "录音室：西东音乐 北京",
    "特别鸣谢：西东音乐（王力宏工作室）",
    "主题歌音乐专辑工作团队",
    "总策划：张洋",
    "海报设计团队：本真工作室：李贞（海报设计总监）/段冰洋",
    "MV团队：",
    "承制：北京龙生堂文化传媒有限公司",
    "音乐发行：太合麦田",
    "项目总统筹：闫曼嘉",
    "项目/艺人统筹：李艺佳/李靖",
    "项目/艺人协力：张靓琳/肖晰美（实习）/陈尧天（实习）",
    "北京很有可能文化传播有限公司：",
    "金朝顺/沈雨娟/张倩玉/夏童/田贝贝/汪斯遥/丁敏",
    "独家短视频平台：抖音",
)


def _block_lines() -> tuple[LyricsLine, ...]:
    lines = tuple(_line(index * 1000, text) for index, text in enumerate(_CREDIT_BLOCK))
    return lines + (_line(99_000, "这江山我起笔 民族血脉又几万里"), _line(100_000, "几世纪六百年里"))


def test_credit_block_is_detected_without_a_word_list() -> None:
    """「承制」「音乐发行」「项目总统筹」「海报设计团队」这些词不在任何表里，也要认出来。"""
    from tonicuisc_server.lyrics.clean import credit_prefix_length

    lines = _block_lines()

    assert credit_prefix_length(lines) == len(_CREDIT_BLOCK)


def test_credit_block_stops_at_first_lyric() -> None:
    from tonicuisc_server.lyrics.clean import credit_prefix_length

    assert credit_prefix_length((_line(0, "故事的小黄花"), _line(1000, "从出生那年就飘着"))) == 0
    assert credit_prefix_length((_line(0, "词：周杰伦"), _line(1000, "故事的小黄花"))) == 1


def test_placeholder_timing_counts_as_credit() -> None:
    """KRC 给不唱的行按每字 ~27ms 铺时间，正文一行 3~9 秒。"""
    from tonicuisc_server.lyrics.clean import looks_like_credit_line

    credit = LyricsLine(0, 162, (LyricsWord(0, 162, "无名小卒甲"),))
    lyric = LyricsLine(0, 7000, (LyricsWord(0, 7000, "无名小卒甲"),))

    assert looks_like_credit_line(credit) is True
    assert looks_like_credit_line(lyric) is False


def test_parenthesized_notice_counts_as_credit() -> None:
    from tonicuisc_server.lyrics.clean import looks_like_credit_line

    assert looks_like_credit_line(_line(0, "（未经著作人许可，不得翻唱翻录或使用）")) is True
    assert looks_like_credit_line(_line(0, "这一路上走走停停")) is False


def test_pinyin_skips_whole_credit_block() -> None:
    """整块署名都不注音，正文照常注音——而且一个字都不删。"""
    pytest.importorskip("pypinyin")
    from tonicuisc_server.lyrics.clean import credit_prefix_length
    from tonicuisc_server.lyrics.pinyin import fill_pinyin

    lines = _block_lines()
    credits = credit_prefix_length(lines)
    filled = fill_pinyin(lines, credit_count=credits)

    assert len(filled) == len(lines), "不能删行"
    assert all(line.roman == "" for line in filled[:credits])
    assert filled[credits].text == "这江山我起笔 民族血脉又几万里"
    assert filled[credits].roman.startswith("zhè")


# ------------------------------------------------------------------ 拼音的适配
def test_pinyin_skips_credit_lines() -> None:
    """``词：周杰伦`` 不该被注成 ``ci zhou jie lun``。"""
    pytest.importorskip("pypinyin")
    from tonicuisc_server.lyrics.pinyin import fill_pinyin

    lines = (_line(0, "词：周杰伦"), _line(1000, "故事的小黄花"))
    filled = fill_pinyin(lines)

    assert filled[0].roman == ""
    assert filled[0].text == "词：周杰伦"
    assert filled[1].roman  # 正文照常注音


def test_pinyin_skips_english_lines_with_stray_chinese() -> None:
    """英语歌里夹一句中文不该整行注音。"""
    pytest.importorskip("pypinyin")
    from tonicuisc_server.lyrics.pinyin import fill_pinyin

    lines = (_line(0, "I love you 我爱你"), _line(1000, "Baby 我爱你"))
    filled = fill_pinyin(lines)

    assert filled[0].roman == "", "英文为主的句子不注音"
    assert filled[1].roman, "中文占一半以上的句子照注"


def test_pinyin_skips_japanese_and_single_char_lines() -> None:
    """日文行（pypinyin 读的是中文音）和「啊」这种单字行都不注。"""
    pytest.importorskip("pypinyin")
    from tonicuisc_server.lyrics.pinyin import fill_pinyin

    lines = (_line(0, "君の名は"), _line(1000, "啊"), _line(2000, "晴天"))
    filled = fill_pinyin(lines)

    assert [line.roman for line in filled][:2] == ["", ""]
    assert filled[2].roman


def test_credit_line_roman_is_stripped() -> None:
    """KRC 自带的那档 roman 也会给「作词：」注音，一样要清掉（不依赖 pypinyin）。"""
    from tonicuisc_server.lyrics.pinyin import fill_pinyin

    lines = (
        LyricsLine(0, 1000, (LyricsWord(0, 1000, "作词：米津玄師", roman="sa ku shi"),), roman="sa ku shi"),
        _line(1000, "晴天"),
    )
    filled = fill_pinyin(lines)

    assert filled[0].text == "作词：米津玄師", "原文不动"
    assert filled[0].roman == ""
    assert all(word.roman == "" for word in filled[0].words)


def test_pinyin_never_changes_original_text() -> None:
    """注音只加附加行，原文一个字都不能动。"""
    pytest.importorskip("pypinyin")
    from tonicuisc_server.lyrics.pinyin import fill_pinyin

    lines = (_line(0, "故事的小黄花"),)
    filled = fill_pinyin(lines)

    assert filled[0].text == "故事的小黄花"
    assert "".join(word.text for word in filled[0].words) == "故事的小黄花"


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
