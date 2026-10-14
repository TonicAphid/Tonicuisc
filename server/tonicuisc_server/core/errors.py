"""领域错误。

只放「业务上讲得通」的异常；HTTP 状态码由 ``api`` 层负责翻译。
"""

from __future__ import annotations


class SongNotFound(KeyError):
    """歌曲 id 未知或已过期。"""


class DownloadFailed(RuntimeError):
    """musicdl 没能产出可用的音频文件。"""
