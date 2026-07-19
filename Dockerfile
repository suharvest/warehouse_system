# All-in-one 生产镜像：uvicorn 同时 serve API + 前端静态文件
# 优化版：Alpine 多阶段构建，最小化镜像体积（适合树莓派等小存储设备）
# 分层策略：依赖层(很少变) → 前端层(偶尔变) → 后端代码层(频繁变)
#
# 构建: docker build -t warehouse .
# 运行: docker run -p 2125:2125 -e PORT=2125 -v data:/data warehouse

# ---- Stage 1: 构建前端 ----
FROM node:20-alpine AS frontend-builder
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --prefer-offline 2>/dev/null || npm install
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: 构建 Python 依赖（不含业务代码） ----
FROM python:3.12-alpine AS python-builder

# 安装编译依赖（bcrypt、rapidfuzz 等 C 扩展需要）
RUN apk add --no-cache gcc musl-dev libffi-dev make

WORKDIR /app

# 只复制依赖声明文件（不含业务代码 → 改 .py 不会破坏此层缓存）
COPY pyproject.toml uv.lock ./

# 用 uv 安装依赖，--no-install-project 跳过项目本身（无需源码）
RUN pip install --no-cache-dir uv && \
    uv sync --frozen --no-install-project && \
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
# procps: pgrep/ps，MCPProcessManager 启动时清理孤儿 mcp_pipe.py 需要
RUN apk add --no-cache libffi libgcc ca-certificates procps

RUN adduser -D -u 1000 appuser && mkdir -p /data /app/logs
WORKDIR /app

# Layer 1: Python 依赖（最稳定，几乎不变）
COPY --chown=appuser:appuser --from=python-builder /app/.venv /app/.venv

# Layer 2: 前端构建产物（偶尔变）
COPY --chown=appuser:appuser --from=frontend-builder /build/dist /app/static

# Layer 3: 后端代码（最常变，放最后 → 只改 .py 时只推/拉这几层）
COPY --chown=appuser:appuser backend/ ./backend/
COPY --chown=appuser:appuser mcp/ ./mcp/
COPY --chown=appuser:appuser run_backend.py ./

RUN chown appuser:appuser /data /app/logs
USER appuser

# 环境变量
ENV PORT=2125
ENV STATIC_DIR=/app/static
ENV DATABASE_PATH=/data/warehouse.db
ENV DEPLOY_MODE=single_tenant
ENV PYTHONUNBUFFERED=1
ENV SQLITE_PRODUCTION_MODE=true
ENV INIT_MOCK_DATA=false
ENV BCRYPT_ENABLED=true
ENV ENABLE_AUDIT_LOG=true
ENV LOG_LEVEL=INFO

EXPOSE ${PORT}

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c 'import os,socket; s=socket.create_connection(("127.0.0.1", int(os.environ.get("PORT", 2125))), timeout=5); s.close()' || exit 1

CMD [".venv/bin/python", "run_backend.py"]
