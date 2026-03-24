# All-in-one 生产镜像：nginx (前端) + uvicorn (后端)
# 一个容器 = 一个完整仓库，通过 PORT 环境变量配置对外端口
#
# 构建: docker build -t warehouse .
# 运行: docker run -p 1024:1024 -e PORT=1024 -v data:/data warehouse

# ---- Stage 1: 构建前端 ----
FROM node:20-alpine AS frontend-builder
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --prefer-offline 2>/dev/null || npm install
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: 运行时 ----
FROM python:3.12-slim

# 安装 nginx
RUN apt-get update && \
    apt-get install -y --no-install-recommends nginx && \
    rm -rf /var/lib/apt/lists/* && \
    # 清除默认配置
    rm -f /etc/nginx/sites-enabled/default

# 创建用户和数据目录
RUN useradd -m -u 1000 appuser && mkdir -p /data

WORKDIR /app

# 安装 Python 依赖
COPY pyproject.toml uv.lock ./
COPY backend/ ./backend/
COPY mcp/ ./mcp/
COPY run_backend.py ./
RUN pip install --no-cache-dir uv && \
    uv sync --frozen && \
    pip uninstall -y uv && \
    pip cache purge && \
    rm -rf /root/.cache

# 复制前端构建产物
COPY --from=frontend-builder /build/dist /usr/share/nginx/html

# 复制 nginx 配置模板和启动脚本
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf.template
COPY deploy/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# 设置权限
RUN chown -R appuser:appuser /data /app /var/log/nginx /var/lib/nginx /run /usr/share/nginx/html

USER appuser

# 可配置环境变量
ENV PORT=1024
ENV DATABASE_PATH=/data/warehouse.db
ENV PYTHONUNBUFFERED=1
ENV SQLITE_PRODUCTION_MODE=true
ENV INIT_MOCK_DATA=false
ENV BCRYPT_ENABLED=true
ENV ENABLE_AUDIT_LOG=true
ENV LOG_LEVEL=INFO

EXPOSE ${PORT}

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://localhost:{os.environ.get(\"PORT\",1024)}/api/dashboard/stats', timeout=5)" || exit 1

ENTRYPOINT ["/docker-entrypoint.sh"]
