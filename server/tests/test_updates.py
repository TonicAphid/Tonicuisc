"""桌面更新检查：Release 资产选择、代理 URL、版本比较与缓存。全部离线。"""

from __future__ import annotations

import pytest
import requests

from tonicuisc_server.services.updates import UpdateService


class _Response:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)  # type: ignore[arg-type]

    def json(self) -> dict:
        return self._payload


def _release(tag: str = "v0.2.5", assets: dict[str, str] | None = None) -> dict:
    assets = assets if assets is not None else {
        "tonicuisc-windows-x64-setup.exe": "https://github.com/o/r/releases/download/v0.2.5/setup.exe",
        "tonicuisc-windows-x64-portable.zip": "https://github.com/o/r/releases/download/v0.2.5/portable.zip",
        "tonicuisc-linux-x64.tar.gz": "https://github.com/o/r/releases/download/v0.2.5/linux.tar.gz",
        "tonicuisc-macos-arm64.dmg": "https://github.com/o/r/releases/download/v0.2.5/arm64.dmg",
        "tonicuisc-macos-x64.dmg": "https://github.com/o/r/releases/download/v0.2.5/x64.dmg",
    }
    return {
        "tag_name": tag,
        "body": "修复若干问题",
        "published_at": "2026-10-24T01:00:00Z",
        "html_url": f"https://github.com/o/r/releases/tag/{tag}",
        "assets": [
            {"name": name, "size": 123, "browser_download_url": url, "digest": "sha256:abc"}
            for name, url in assets.items()
        ],
    }


def _service(release: dict, calls: list[int] | None = None) -> UpdateService:
    calls = calls if calls is not None else []

    def fake_get(url, **kwargs):
        calls.append(1)
        return _Response(release)

    return UpdateService(repo="o/r", token="", ttl=600, http_get=fake_get)


def test_windows_prefers_installer_and_uses_gh_proxy() -> None:
    service = _service(_release())

    result = service.check("windows", version="v0.2.4", build="12")

    assert result["ready"] is True
    assert result["has_update"] is True
    assert result["latest"] == "0.2.5"
    assert result["asset"]["name"] == "tonicuisc-windows-x64-setup.exe"
    assert result["asset"]["url"].startswith("https://gh-proxy.com/https://github.com/")
    assert result["asset"]["direct_url"].startswith("https://github.com/")


def test_macos_arch_selects_matching_dmg() -> None:
    service = _service(_release())

    arm = service.check("macos", arch="arm64", version="0.2.5")
    intel = service.check("macos", arch="x64", version="0.2.5")

    assert arm["asset"]["name"] == "tonicuisc-macos-arm64.dmg"
    assert intel["asset"]["name"] == "tonicuisc-macos-x64.dmg"
    assert arm["has_update"] is False


def test_missing_asset_is_not_ready() -> None:
    service = _service(_release(assets={"tonicuisc-windows-x64-setup.exe": "https://github.com/o/r/setup.exe"}))

    result = service.check("macos", arch="arm64", version="v0.2.4")

    assert result["ready"] is False
    assert result["asset"] is None
    assert "还没上传完" in result["message"]


def test_ios_is_unsupported() -> None:
    result = UpdateService(repo="o/r", http_get=lambda *a, **k: None).check("ios")
    assert result["supported"] is False


def test_version_comparison_and_fix_suffix() -> None:
    service = _service(_release("v0.2.5fix"))

    newer = service.check("linux", version="v0.2.5")
    same = service.check("linux", version="v0.2.5fix")

    assert newer["has_update"] is True
    assert same["has_update"] is False
    assert newer["latest"] == "0.2.5fix"


def test_release_is_cached() -> None:
    calls: list[int] = []
    service = _service(_release(), calls)

    service.check("windows", version="0.2.4")
    service.check("linux", version="0.2.4")

    assert len(calls) == 1


def test_no_release_returns_not_ready() -> None:
    service = UpdateService(repo="o/r", http_get=lambda *a, **k: _Response({}, status=404))

    result = service.check("windows", version="0.2.4")

    assert result["ready"] is False
    assert result["latest"] is None


def test_unknown_platform_raises() -> None:
    service = _service(_release())
    with pytest.raises(ValueError):
        service.check("plan9")
