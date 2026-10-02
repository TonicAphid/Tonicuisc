"""QQ 封面匹配判断的单元测试（纯函数，不联网）。"""

from __future__ import annotations

from tonicuisc_server.qqmusic import _is_same_song


def _song(title: str, *singers: str, mid: str = "album123") -> dict:
    return {"songname": title, "singer": [{"name": name} for name in singers], "albummid": mid}


def test_exact_match() -> None:
    assert _is_same_song(_song("天地龙鳞", "王力宏"), "天地龙鳞", "王力宏")


def test_ignores_case_space_and_punctuation() -> None:
    assert _is_same_song(_song("Hello, World!", "Adele"), "hello world", "adele")


def test_accepts_title_with_suffix() -> None:
    assert _is_same_song(_song("天地龙鳞 (Live)", "王力宏"), "天地龙鳞", "王力宏")
    assert _is_same_song(_song("天地龙鳞", "王力宏"), "天地龙鳞（听歌版）", "王力宏")


def test_rejects_short_fragment() -> None:
    # 搜「不存在的歌名xyzabc」时 QQ 会返回 XY&Z，不能认
    assert not _is_same_song(_song("XY&Z", "松本梨香"), "不存在的歌名xyzabc", "无")
    assert not _is_same_song(_song("P.XyZ (漂向宇宙尽头)", "鹿晗"), "不存在的歌名xyzabc", "无")


def test_rejects_wrong_singer() -> None:
    assert not _is_same_song(_song("晴天", "周杰伦"), "晴天", "五月天")


def test_accepts_when_singer_has_suffix() -> None:
    assert _is_same_song(_song("莫问归期", "蒋雪儿Snow.J"), "莫问归期", "蒋雪儿")


def test_without_singer_only_title_counts() -> None:
    assert _is_same_song(_song("晴天", "周杰伦"), "晴天", "")
    assert not _is_same_song(_song("晴天", "周杰伦"), "阴天", "")


def test_empty_title_is_rejected() -> None:
    assert not _is_same_song(_song("", "周杰伦"), "", "周杰伦")
