# All-in-one 生产镜像：uvicorn 同时 serve API + 前端静态文件
# 无 nginx，单进程，简单可靠
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

# 复制前端构建产物到 /app/static
COPY --from=frontend-builder /build/dist /app/static

RUN chown -R appuser:appuser /data /app
USER appuser

# 环境变量
ENV PORT=1024
ENV STATIC_DIR=/app/static
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

CMD .venv/bin/python run_backend.py
