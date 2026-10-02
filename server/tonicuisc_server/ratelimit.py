"""滑动窗口限速：防密码爆破 / 防刷接口。

单进程内存实现，不需要 Redis；窗口内超过次数就拒绝，并告诉调用方还要等几秒。
"""

from __future__ import annotations

import threading
import time
from collections import deque


class SlidingWindowLimiter:
    def __init__(self, limit: int, window: float) -> None:
        self.limit = limit
        self.window = window
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, int]:
        """只检查，不计数。返回 (是否放行, 还需等待秒数)。"""
        now = time.time()
        with self._lock:
            hits = self._hits.get(key)
            if not hits:
                return True, 0
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if not hits:
                return True, 0
            if len(hits) >= self.limit:
                return False, max(int(self.window - (now - hits[0])) + 1, 1)
            return True, 0

    def hit(self, key: str) -> None:
        """记一次（比如一次密码错误）。"""
        now = time.time()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            hits.append(now)
            if len(self._hits) > 2000:
                self._prune(now)

    def check(self, key: str) -> tuple[bool, int]:
        """检查并计数，用于「每个请求都算一次」的接口。"""
        allowed, retry_after = self.allow(key)
        if allowed:
            self.hit(key)
        return allowed, retry_after

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)

    def _prune(self, now: float) -> None:
        for existing in [k for k, hits in self._hits.items() if not hits or hits[-1] <= now - self.window]:
            self._hits.pop(existing, None)


class FailedLoginGuard:
    """密码错误计数：同一 (来源 IP + 用户名) 连续错太多次就暂时拒绝。"""

    def __init__(self, limit: int = 5, window: float = 300) -> None:
        self.limiter = SlidingWindowLimiter(limit=limit, window=window)

    @staticmethod
    def key(client_key: str, username: str) -> str:
        return f"{client_key}|{(username or '').strip().lower()}"

    def blocked_for(self, client_key: str, username: str) -> int:
        allowed, retry_after = self.limiter.allow(self.key(client_key, username))
        return 0 if allowed else retry_after

    def record_failure(self, client_key: str, username: str) -> None:
        self.limiter.hit(self.key(client_key, username))

    def clear(self, client_key: str, username: str) -> None:
        self.limiter.reset(self.key(client_key, username))
