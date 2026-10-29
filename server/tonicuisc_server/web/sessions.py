"""网页会话：HttpOnly Cookie ↔ 服务端内存里的设备 API Key。

浏览器只拿到一个随机的会话 ID（``tuisc_session``），真正的设备 key 留在进程内存里，
**永远不下发到前端**——和以前 Node 入口 ``webapp/server.js`` 的做法一致，只是现在
会话和 API 在同一个进程里，省掉了「Node 反代 Python 时再注入 X-API-Key」这一跳。

代价也一致：**进程重启后内存会话清空**，浏览器要重新配对一次。这是有意的，
key 不落盘，机器被翻也拿不到能用的凭证。

会话状态机（和前端 ``music.js`` 的 ``applySession`` 一一对应）：

* ``api_key`` 有值 → ``ready``
* ``pending`` 有值 → ``pending``（等用户在 ``/login`` 批准）
* 两者都没有 → ``anonymous``
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from ..core.config import SETTINGS

#: 会话 Cookie 名。**别改**：改了等于让所有已配对浏览器重新登录一次。
SESSION_COOKIE = "tuisc_session"

#: 浏览器指纹 Cookie 名。这是一枚**长期**（默认 1 年）的随机 ID，作用相当于「网页播放器
#: 的 MAC 地址」：同一台机器的同一个浏览器永远带同一个值，服务端靠它认人。
#:
#: 为什么不用真的 MAC：浏览器拿不到网卡地址（隐私限制，WebRTC 拿到的还是 mDNS 混淆过的
#: 假地址），所以只能用「服务端发的随机 ID + 长期 Cookie」模拟一个稳定硬件标识。
WEB_ID_COOKIE = "tuisc_web_id"

#: 浏览器指纹 Cookie 的有效期（秒）：一年。它本身不是凭证，只是「我是哪台设备」的编号，
#: 真正的凭证是绑定记录里那条设备指针（见 ``storage/web_logins.py``）。
WEB_ID_TTL = 365 * 24 * 3600

#: 默认会话有效期（秒）：7 天
DEFAULT_TTL = 7 * 24 * 3600


@dataclass
class WebSession:
    """一个浏览器会话。字段名和 Node 版保持一致，方便对照。"""

    id: str
    api_key: str | None = None
    #: ``/api/me`` 的形状，前端「我的」页直接用
    me: dict[str, Any] | None = None
    #: 设备码流程的中间态：``user_code`` / ``poll_secret`` / ``expires_at``
    pending: dict[str, Any] | None = None
    created_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)


class SessionStore:
    """进程内会话表（单进程单事件循环，字典操作足够）。

    ``ttl`` 只用来判过期和定期清扫；每次 :meth:`get` 都会顺手续期
    （和 Node 版「最后一次访问 + TTL」的语义一致）。
    """

    def __init__(self, ttl: float = DEFAULT_TTL) -> None:
        self.ttl = float(ttl)
        self._items: dict[str, WebSession] = {}

    def __len__(self) -> int:
        return len(self._items)

    def create(self) -> WebSession:
        session = WebSession(id=secrets.token_urlsafe(32))
        self._items[session.id] = session
        return session

    def get(self, session_id: str | None) -> WebSession | None:
        """取会话并续期；过期了就顺手删掉。"""
        if not session_id:
            return None
        session = self._items.get(session_id)
        if session is None:
            return None
        now = time.time()
        if now - session.last_seen > self.ttl:
            self._items.pop(session_id, None)
            return None
        session.last_seen = now
        return session

    def get_or_create(self, session_id: str | None) -> WebSession:
        return self.get(session_id) or self.create()

    def drop(self, session_id: str | None) -> None:
        if session_id:
            self._items.pop(session_id, None)

    def sweep(self) -> int:
        """清掉过期会话，返回清掉的条数（长时间运行避免字典越积越大）。"""
        deadline = time.time() - self.ttl
        stale = [key for key, session in self._items.items() if session.last_seen < deadline]
        for key in stale:
            self._items.pop(key, None)
        return len(stale)

    def clear(self) -> None:
        """测试用：清空所有会话。"""
        self._items.clear()


#: 进程级单例。``api.middleware`` / ``api.routes.site`` 都从这里取。
SESSIONS = SessionStore(float(getattr(SETTINGS, "web_session_ttl", DEFAULT_TTL) or DEFAULT_TTL))
