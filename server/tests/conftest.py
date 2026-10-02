"""测试公共夹具：把服务和认证都指到临时目录，避免动到真实 .cache。"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

import tonicuisc_server.main as main_module
from tonicuisc_server.auth import AuthManager
from tonicuisc_server.service import MusicService
from tonicuisc_server.storage import Storage

SCRATCH = Path(__file__).resolve().parent / "_scratch"


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
    try:
        yield {"service": service, "auth": auth, "store": store, "root": root}
    finally:
        main_module._service = None
        main_module._auth = None
        store.close()
        shutil.rmtree(root, ignore_errors=True)
