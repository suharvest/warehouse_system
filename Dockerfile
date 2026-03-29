# All-in-one 生产镜像：uvicorn 同时 serve API + 前端静态文件
# 优化版：Alpine 多阶段构建，最小化镜像体积（适合树莓派等小存储设备）
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

# ---- Stage 2: 构建 Python 依赖 ----
FROM python:3.12-alpine AS python-builder

# 安装编译依赖（bcrypt、rapidfuzz 等 C 扩展需要）
RUN apk add --no-cache gcc musl-dev libffi-dev make

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY backend/ ./backend/
COPY mcp/ ./mcp/

# 用 uv 安装依赖，完成后 uv 不会留在最终镜像
RUN pip install --no-cache-dir uv && \
    uv sync --frozen && \
    # 清理 .venv 中不需要的文件，节省空间
    find .venv -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null; \
    find .venv -type f -name "*.pyc" -delete 2>/dev/null; \
    find .venv -type f -name "*.pyo" -delete 2>/dev/null; \
    find .venv -type d -name "*.dist-info" -exec sh -c 'for d; do find "$d" -not -name "METADATA" -not -name "RECORD" -not -name "top_level.txt" -not -name "entry_points.txt" -not -name "direct_url.json" -type f -delete; done' _ {} + 2>/dev/null; \
    find .venv -type d -name "tests" -exec rm -rf {} + 2>/dev/null; \
    find .venv -type d -name "test" -exec rm -rf {} + 2>/dev/null; \
    find .venv -type f -name "*.so" -exec strip -s {} + 2>/dev/null; \
    true

# ---- Stage 3: 最终运行时镜像 ----
FROM python:3.12-alpine

# libffi: cffi/cryptography 运行时需要
# libgcc: bcrypt 等 C 扩展需要
# ca-certificates: requests/websockets HTTPS 连接需要
RUN apk add --no-cache libffi libgcc ca-certificates

RUN adduser -D -u 1000 appuser && mkdir -p /data
WORKDIR /app

# 只复制运行时需要的文件
COPY --from=python-builder /app/.venv /app/.venv
COPY backend/ ./backend/
COPY mcp/ ./mcp/
COPY run_backend.py ./

# 复制前端构建产物
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
