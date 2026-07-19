#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
APP_MODULE="${APP_MODULE:-backend.app.main:app}"

export APP_ENV="${APP_ENV:-development}"

if [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "创建虚拟环境: $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

if [ ! -x "$VENV_DIR/bin/pip" ]; then
  echo "未找到 pip，请检查虚拟环境: $VENV_DIR" >&2
  exit 1
fi

if [ ! -f "$ROOT_DIR/.env" ]; then
  cat >&2 <<'EOF'
警告: 未找到 .env 文件。
后端启动时会读取 .env；如果缺少数据库或模型配置，服务可能启动失败。
EOF
fi

if [ "${SKIP_INSTALL:-0}" != "1" ]; then
  echo "安装/更新依赖..."
  "$VENV_DIR/bin/pip" install -r "$ROOT_DIR/requirements.txt"
fi

echo "启动后端服务: http://$HOST:$PORT"
exec "$VENV_DIR/bin/python" -m uvicorn "$APP_MODULE" --reload --host "$HOST" --port "$PORT"
