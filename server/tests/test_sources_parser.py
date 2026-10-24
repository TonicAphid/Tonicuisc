"""酷我解析顺序补丁：官方优先、第三方兜底。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tonicuisc_server.services import sources as sources_module
from tonicuisc_server.services.sources import _prefer_official_parser


class FakeKuwo:
    source = "KuwoMusicClient"

    def __init__(self, official_ok: bool) -> None:
        self.official_ok = official_ok
        self.calls: list[str] = []
        self.third_result = SimpleNamespace(with_valid_download_url=True, tag="third")

    def _parsewithofficialapiv1(self, search_result, song_info_flac=None, **kwargs):
        self.calls.append("official")
        return SimpleNamespace(with_valid_download_url=self.official_ok, tag="official")

    def _parsewiththirdpartapis(self, search_result, request_overrides=None):
        self.calls.append("third")
        return self.third_result


@pytest.fixture(autouse=True)
def _no_musicdl_import(monkeypatch):
    """测试环境不 import musicdl（它会往用户 AppData 写日志）。"""
    monkeypatch.setattr(
        sources_module,
        "_empty_song",
        lambda source: SimpleNamespace(source=source, with_valid_download_url=False),
    )


def test_official_first_skips_third_party():
    client = FakeKuwo(official_ok=True)
    _prefer_official_parser(client)

    song = client._parsewithofficialapiv1({"MUSICRID": "1"})

    assert song.tag == "official"
    assert client.calls == ["official"]
    assert client._tonicuisc_official_first is True


def test_third_party_still_works_as_fallback():
    client = FakeKuwo(official_ok=False)
    _prefer_official_parser(client)

    song = client._parsewithofficialapiv1({"MUSICRID": "1"})

    assert song is client.third_result
    assert client.calls == ["official", "third"]


def test_third_party_tier_returns_empty_song():
    client = FakeKuwo(official_ok=True)
    _prefer_official_parser(client)

    song = client._parsewiththirdpartapis({})

    assert song.with_valid_download_url is False
    assert client.calls == []  # 不再真的去试第三方接口


def test_patch_is_idempotent():
    client = FakeKuwo(official_ok=True)
    _prefer_official_parser(client)
    _prefer_official_parser(client)

    song = client._parsewithofficialapiv1({"MUSICRID": "1"})

    assert song.tag == "official"
    assert client.calls == ["official"]


def test_other_sources_untouched():
    class FakeMigu:
        source = "MiguMusicClient"

    client = FakeMigu()
    _prefer_official_parser(client)

    assert not getattr(client, "_tonicuisc_official_first", False)


def test_fallback_failure_keeps_official_result():
    class Broken(FakeKuwo):
        def _parsewiththirdpartapis(self, search_result, request_overrides=None):
            self.calls.append("third")
            raise RuntimeError("third party down")

    client = Broken(official_ok=False)
    _prefer_official_parser(client)

    song = client._parsewithofficialapiv1({"MUSICRID": "1"})

    assert song.tag == "official"
    assert client.calls == ["official", "third"]
