# 前端 Dockerfile
FROM python:3.12-slim

WORKDIR /app

# 复制前端文件
COPY frontend/ ./

ENV PYTHONUNBUFFERED=1

EXPOSE 2125

# 启动前端服务
CMD ["python", "server.py"]
