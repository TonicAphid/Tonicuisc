"""``python -m tonicuisc_server`` 启动服务。"""

from __future__ import annotations

import uvicorn

from .config import SETTINGS


def main() -> None:
    uvicorn.run(
        "tonicuisc_server.main:app",
        host=SETTINGS.host,
        port=SETTINGS.port,
        reload=SETTINGS.reload,
    )


if __name__ == "__main__":
    main()
