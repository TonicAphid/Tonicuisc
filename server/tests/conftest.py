"""测试公共夹具：把服务和认证都指到临时目录，避免动到真实 .cache。"""

from __future__ import annotations

import dataclasses
import shutil
import uuid
from pathlib import Path

import pytest

import tonicuisc_server.main as main_module
import tonicuisc_server.service as service_module
from tonicuisc_server.auth import AuthManager
from tonicuisc_server.service import MusicService
from tonicuisc_server.storage import Storage

SCRATCH = Path(__file__).resolve().parent / "_scratch"


@pytest.fixture(autouse=True)
def _no_network_cover(monkeypatch):
    """默认关掉 QQ 封面查询：测试不该联网，需要用的用例自己打开。"""
    monkeypatch.setattr(service_module, "SETTINGS", dataclasses.replace(service_module.SETTINGS, qq_cover=False))


@pytest.fixture()
def app_env():
    """临时 SQLite + AuthManager + 临时的 MusicService。"""
    root = SCRATCH / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    store = Storage(root / "test.db")

    service = MusicService()
    service.storage.close()
    service.storage = store
    service.work_dir = root / "music"
    service.files_dir = root / "files"
    service.work_dir.mkdir(parents=True, exist_ok=True)
    service.files_dir.mkdir(parents=True, exist_ok=True)

    auth = AuthManager(store, ttl=300)
    main_module._service = service
    main_module._auth = auth
    main_module.reset_limiters()  # 每个用例都从干净的限速计数开始
    try:
        yield {"service": service, "auth": auth, "store": store, "root": root}
    finally:
        main_module._service = None
        main_module._auth = None
        main_module.reset_limiters()
        store.close()
        shutil.rmtree(root, ignore_errors=True)
