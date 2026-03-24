#!/bin/sh
# All-in-one entrypoint: nginx (前端) + uvicorn (后端)
# nginx 监听 $PORT，代理 /api/ 到 uvicorn (127.0.0.1:2124)

set -e

PORT=${PORT:-1024}
BACKEND_PORT=2124

echo "=== Warehouse System ==="
echo "  Port: ${PORT}"
echo "  Database: ${DATABASE_PATH:-/data/warehouse.db}"
echo "========================"

# 用 envsubst 生成 nginx 配置（替换 PORT 和后端地址）
export LISTEN_PORT=$PORT
export BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}"
envsubst '${LISTEN_PORT} ${BACKEND_URL}' \
    < /etc/nginx/conf.d/default.conf.template \
    > /etc/nginx/conf.d/default.conf

# 启动 nginx（后台）
nginx -g 'daemon on;'

# 启动 uvicorn（前台，接收信号）
exec /app/.venv/bin/python -c "
import sys, os
sys.path.insert(0, '/app/backend')
os.chdir('/app/backend')
import uvicorn
from app import app
uvicorn.run(app, host='127.0.0.1', port=${BACKEND_PORT})
"
