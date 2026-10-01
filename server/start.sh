#!/usr/bin/env bash
# 启动 Tonicuisc 后端（在 server/ 目录下运行）
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec python -m tonicuisc_server
