"""musicdl 的 rich 进度条要被换掉，否则下载日志会被那一堆 ``━━━`` 冲掉。"""

from __future__ import annotations

import pytest

from tonicuisc_server.services.sources import _SilentProgress, silence_musicdl_progress


def test_silent_progress_implements_what_musicdl_uses() -> None:
    progress = _SilentProgress()
    with progress as active:
        task = active.add_task("KuwoMusicClient.download >>> Completed (0/1) SongInfos", total=1, kind="overall")
        assert active.tasks[task].total == 1
        assert active.tasks[task].completed == 0

        active.advance(task)
        active.update(task, description="done", completed=1)

    assert active.tasks[task].completed == 1
    assert active.tasks[task].description == "done"


def test_silence_musicdl_progress_patches_loaded_modules() -> None:
    base = pytest.importorskip("musicdl.modules.sources.base")

    silence_musicdl_progress()

    assert base.Progress is _SilentProgress
    # 再调一次也不能把已经换好的符号弄坏（下载前会重复调用）
    silence_musicdl_progress()
    assert base.Progress is _SilentProgress
