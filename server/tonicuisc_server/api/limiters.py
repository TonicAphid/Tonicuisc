"""限速器的实例与重置（单进程内存实现，重启即清零）。"""

from __future__ import annotations

from ..auth import FailedLoginGuard, SlidingWindowLimiter

#: 每个来源每分钟最多提交 30 次 /login
login_limiter = SlidingWindowLimiter(limit=30, window=60)
#: 每 IP 每分钟最多发起 30 次设备码
device_start_limiter = SlidingWindowLimiter(limit=30, window=60)
#: 同一 IP + 用户名 5 分钟内错 5 次就锁（密码、恢复码共用）
failed_login_guard = FailedLoginGuard(limit=5, window=300)


def reset_limiters() -> None:
    """测试用：清空所有限速计数。"""
    login_limiter.reset()
    device_start_limiter.reset()
    failed_login_guard.limiter.reset()
