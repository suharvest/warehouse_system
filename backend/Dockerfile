# 后端 Dockerfile
FROM python:3.12-slim

WORKDIR /app

# 安装 uv
RUN pip install uv

# 复制项目文件
COPY pyproject.toml uv.lock ./
COPY backend/ ./backend/
COPY run_backend.py ./

# 安装依赖
RUN uv sync --frozen

# 创建数据目录
RUN mkdir -p /data

# 设置环境变量
ENV DATABASE_PATH=/data/warehouse.db
ENV PYTHONUNBUFFERED=1

EXPOSE 2124

# 启动后端服务
CMD ["uv", "run", "python", "run_backend.py"]
