#!/usr/bin/env python3
"""
仓库管理系统后端启动脚本

使用 uvicorn 启动 FastAPI 应用
"""
import sys
import os
import uvicorn

# 将 backend 目录添加到 Python 路径
backend_dir = os.path.join(os.path.dirname(__file__), 'backend')
sys.path.insert(0, backend_dir)

# 更改工作目录到 backend
os.chdir(backend_dir)

# 导入 FastAPI 应用
from app import app

if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=2124)
