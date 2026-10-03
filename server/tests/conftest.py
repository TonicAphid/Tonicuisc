"""测试公共夹具：把服务和认证都指到临时目录 + fakeredis，避免动到真实 Redis / .cache。"""

from __future__ import annotations

import dataclasses
import shutil
import uuid
from pathlib import Path

import fakeredis
import pytest

import tonicuisc_server.artwork as artwork_module
import tonicuisc_server.main as main_module
import tonicuisc_server.service as service_module
from tonicuisc_server.auth import AuthManager
from tonicuisc_server.service import MusicService
from tonicuisc_server.storage import Storage

SCRATCH = Path(__file__).resolve().parent / "_scratch"


def make_storage(*, namespace: str = "tonicuisc-test", client=None) -> Storage:
    """每个用例一个干净的 fakeredis（不联网、不依赖本机装没装 Redis）。"""
    return Storage(client or fakeredis.FakeStrictRedis(decode_responses=True), namespace=namespace)


@pytest.fixture(autouse=True)
def _no_real_redis(monkeypatch):
    """兜底：测试里 `MusicService()` / `Storage()` 默认拿到 fakeredis。

    不然构造 MusicService 会去连（甚至拉起/下载）真的 Redis，CI 上必挂。
    用例自己注入的 Storage 不受影响。
    """
    import tonicuisc_server.redis_runtime as redis_runtime

    fake = fakeredis.FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr(redis_runtime, "ensure_redis_client", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _no_network_cover(monkeypatch):
    """默认关掉 QQ 封面查询：测试不该联网，需要用的用例自己打开。"""
    # 封面逻辑在 artwork 模块里，SETTINGS 要在那儿打补丁才生效
    monkeypatch.setattr(artwork_module, "SETTINGS", dataclasses.replace(artwork_module.SETTINGS, qq_cover=False))


@pytest.fixture()
def redis_client():
    """一个干净的 fakeredis 客户端（同一个用例里多次取到的是同一个）。"""
    return fakeredis.FakeStrictRedis(decode_responses=True)


@pytest.fixture()
def app_env(redis_client):
    """临时 Redis + AuthManager + 临时的 MusicService。"""
    root = SCRATCH / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    store = Storage(redis_client, namespace=f"t{uuid.uuid4().hex[:8]}")

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
        yield {"service": service, "auth": auth, "store": store, "root": root, "redis": redis_client}
    finally:
        main_module._service = None
        main_module._auth = None
        main_module.reset_limiters()
        store.close()
        shutil.rmtree(root, ignore_errors=True)

