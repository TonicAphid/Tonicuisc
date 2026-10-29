"""鉴权相关的常量与正则。

这里只放「数据」，不放逻辑，方便 ``cli`` / ``web`` / ``api`` 都引用同一份定义。
"""

from __future__ import annotations

import re
import string

#: 请求头里带设备 key
API_KEY_HEADER = "x-api-key"

#: 不需要 API Key 的路径（**只对 ``/api`` 前缀生效**，见 ``api/middleware.py``）。
#: ``/login`` 和 ``/health`` 不在 ``/api`` 下，本来就不经过鉴权中间件，别往这里加。
#: ``/api/update`` 只返回版本号和安装包地址（都是公开的 Release 信息），
#: 落地页要在登录前显示版本号，所以放行。
PUBLIC_PATHS = frozenset({"/api/health", "/api/device/start", "/api/device/status", "/api/update"})

#: 设备码格式：A1B1-C1D1（8 位大写字母数字 + 中间一个横杠）
USER_CODE_RE = re.compile(r"^[A-Z0-9]{4}-[A-Z0-9]{4}$")

#: App 重新配对时认领的旧设备 ID：uuid4().hex 的形状，不匹配一律当没带
DEVICE_ID_RE = re.compile(r"[0-9a-f]{32}")

#: 设备码 / 恢复码共用的字母表：36 个字符里剔掉看混的 0/O/1/I → 32 个（每位 5 bit）
CODE_ALPHABET = "".join(c for c in string.ascii_uppercase + string.digits if c not in "0O1I")

#: 恢复码：4 组 × 5 位 = 100 bit，抄写友好
RECOVERY_CODE_RE = re.compile(r"^[A-Z0-9]{5}(?:-[A-Z0-9]{5}){3}$")

#: PBKDF2 迭代次数（密码与恢复码共用）
PASSWORD_ITERATIONS = 200_000


class DeviceStatus:
    """设备码请求的状态机。

    服务端和客户端共用这几个字面量：**别改成别的拼写**，客户端按字符串比对。
    """

    #: 已登记，等用户在浏览器里批准
    PENDING = "pending"
    #: 已批准，明文 key 还没被取走（取走后变 CLAIMED）
    APPROVED = "approved"
    #: 用户在网页上拒绝
    DENIED = "denied"
    #: 码不存在 / 已过期 / poll_secret 不对（不区分，防探测）
    EXPIRED = "expired"
    #: 已批准但 key 已经被取走过一次 —— 这一轮登录结束了，别再轮询
    CLAIMED = "claimed"
