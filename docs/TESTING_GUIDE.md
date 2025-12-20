# 仓库管理系统 v2.0 测试指南

## 测试框架概述

本项目提供两种测试方式：

| 测试类型 | 文件 | 用途 |
|---------|------|------|
| 快速验证 | `test/backend/quick_test.py` | 手动快速验证，无需pytest |
| 完整测试 | `test/backend/test_v2_features.py` | pytest自动化测试套件 |

---

## 快速验证（推荐）

### 使用方式

```bash
cd backend

# 方式1: 先启动服务器，再运行测试
python app.py &
sleep 3
python ../test/backend/quick_test.py

# 方式2: 自动启动服务器（需要端口可用）
python ../test/backend/quick_test.py --auto-server

# 方式3: 指定端口
PORT=2124 python app.py &
sleep 3
PORT=2124 python ../test/backend/quick_test.py
```

### 测试输出示例

```
仓库管理系统 v2.0 功能验证
服务器: http://localhost:8000

==================================================
服务器连接测试
==================================================
  ✓ 服务器连接正常

==================================================
第一阶段：用户认证测试
==================================================
>>> 认证状态
  ✓ 获取认证状态
    系统初始化状态: 已初始化
>>> 管理员设置
  ✓ 管理员已存在，跳过创建
>>> 登录测试
  ✓ 管理员登录
  ✓ 获取当前用户信息
    当前用户: 测试管理员
...

==================================================
测试汇总
==================================================
  总计: 25 项测试
  通过: 25
  失败: 0

  通过率: 100.0%
```

---

## Pytest 自动化测试

### 安装依赖

```bash
pip install pytest pytest-asyncio httpx
```

### 运行测试

```bash
cd backend

# 运行所有测试
pytest tests/test_v2_features.py -v

# 运行指定测试类
pytest tests/test_v2_features.py -v -k "TestAuth"
pytest tests/test_v2_features.py -v -k "TestContacts"
pytest tests/test_v2_features.py -v -k "TestBatch"

# 显示详细错误信息
pytest tests/test_v2_features.py -v --tb=long

# 生成测试报告
pytest tests/test_v2_features.py --html=report.html
```

---

## 测试覆盖范围

### 第一阶段：用户认证

| 测试项 | 说明 | 验证点 |
|--------|------|--------|
| 认证状态 | GET /api/auth/status | 返回initialized字段 |
| 首次设置 | POST /api/auth/setup | 创建管理员账号 |
| 登录成功 | POST /api/auth/login | 返回success=true |
| 登录失败 | POST /api/auth/login | 错误密码返回success=false |
| 当前用户 | GET /api/auth/me | 返回用户信息 |
| 用户列表 | GET /api/users | admin可访问 |
| 创建用户 | POST /api/users | admin可创建 |
| 权限拒绝 | POST /api/users | 非admin被拒绝 |
| API密钥列表 | GET /api/api-keys | admin可访问 |
| 创建API密钥 | POST /api/api-keys | 返回wh_开头的密钥 |

### 第二阶段：联系方管理

| 测试项 | 说明 | 验证点 |
|--------|------|--------|
| 联系方列表 | GET /api/contacts | 返回分页数据 |
| 创建供应商 | POST /api/contacts | is_supplier=true |
| 创建客户 | POST /api/contacts | is_customer=true |
| 类型必选 | POST /api/contacts | 无类型返回400 |
| 供应商下拉 | GET /api/contacts/suppliers | 只返回供应商 |
| 客户下拉 | GET /api/contacts/customers | 只返回客户 |

### 第三阶段：批次管理

| 测试项 | 说明 | 验证点 |
|--------|------|--------|
| 批次号生成 | generate_batch_no() | 格式YYYYMMDD-XXX |
| 入库创建批次 | POST /api/materials/stock-in | 返回batch信息 |
| 出库FIFO | POST /api/materials/stock-out | 返回batch_consumptions |
| 记录含批次 | GET /api/inventory/records | 包含batch_no字段 |
| Excel导出批次 | GET /api/inventory/export-excel | 包含批次列 |

### 权限控制

| 测试项 | 说明 | 验证点 |
|--------|------|--------|
| 访客读取 | GET /api/dashboard/stats | 200 OK |
| 访客写入 | POST /api/materials/stock-in | 401/403 |
| 操作员入库 | POST /api/materials/stock-in | operate权限通过 |

---

## 手动测试清单

### 1. 用户认证测试

```bash
# 检查初始化状态
curl http://localhost:8000/api/auth/status

# 首次设置管理员（仅未初始化时）
curl -X POST http://localhost:8000/api/auth/setup \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123","display_name":"管理员"}'

# 登录
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}' \
  -c cookies.txt

# 获取当前用户
curl http://localhost:8000/api/auth/me -b cookies.txt

# 创建API密钥
curl -X POST http://localhost:8000/api/api-keys \
  -H "Content-Type: application/json" \
  -d '{"name":"测试终端","role":"operate"}' \
  -b cookies.txt
```

### 2. 联系方测试

```bash
# 创建供应商
curl -X POST http://localhost:8000/api/contacts \
  -H "Content-Type: application/json" \
  -d '{"name":"供应商A","is_supplier":true,"is_customer":false}' \
  -b cookies.txt

# 创建客户
curl -X POST http://localhost:8000/api/contacts \
  -H "Content-Type: application/json" \
  -d '{"name":"客户B","is_supplier":false,"is_customer":true}' \
  -b cookies.txt

# 获取供应商列表
curl http://localhost:8000/api/contacts/suppliers -b cookies.txt

# 获取客户列表
curl http://localhost:8000/api/contacts/customers -b cookies.txt
```

### 3. 批次管理测试

```bash
# 入库（自动创建批次）
curl -X POST http://localhost:8000/api/materials/stock-in \
  -H "Content-Type: application/json" \
  -d '{"product_name":"watcher-xiaozhi主控板","quantity":50,"reason":"测试入库"}' \
  -b cookies.txt

# 再入库一批
curl -X POST http://localhost:8000/api/materials/stock-in \
  -H "Content-Type: application/json" \
  -d '{"product_name":"watcher-xiaozhi主控板","quantity":30,"reason":"第二批入库"}' \
  -b cookies.txt

# 出库（FIFO消耗）
curl -X POST http://localhost:8000/api/materials/stock-out \
  -H "Content-Type: application/json" \
  -d '{"product_name":"watcher-xiaozhi主控板","quantity":60,"reason":"测试出库"}' \
  -b cookies.txt

# 查看记录（含批次信息）
curl "http://localhost:8000/api/inventory/records?page_size=5" -b cookies.txt
```

### 4. API密钥认证测试

```bash
# 使用API密钥入库（替换为实际密钥）
curl -X POST http://localhost:8000/api/materials/stock-in \
  -H "Content-Type: application/json" \
  -H "X-API-Key: wh_your_api_key_here" \
  -d '{"product_name":"watcher-xiaozhi主控板","quantity":10,"reason":"API入库"}'
```

---

## 前端手动测试

### 1. 用户认证

1. 访问首页，检查是否显示登录模态框（首次使用）
2. 创建管理员账号
3. 登录后检查头部显示用户名
4. 检查"用户管理"TAB是否可见（仅admin）
5. 创建操作员用户，登录验证权限

### 2. 联系方管理

1. 点击"联系方"TAB
2. 添加供应商，验证类型标记
3. 添加客户，验证类型标记
4. 尝试不选类型创建，应提示错误

### 3. 批次管理

1. 点击"新增记录"
2. 选择入库，执行入库操作
3. 检查成功提示是否包含批次号
4. 查看进出库记录，验证批次列
5. 执行出库，检查批次消耗详情
6. 导出Excel，验证批次列存在

### 4. 权限验证

1. 登出后以访客身份浏览
2. 验证无法看到"用户管理"、"联系方"TAB
3. 验证无法执行入库/出库操作

---

## 常见问题

### Q: 测试失败 "无法连接到服务器"
**A:** 确保后端服务已启动，检查端口是否正确。

### Q: 测试失败 "没有可用的测试物料"
**A:** 数据库为空，需要先生成模拟数据或手动添加物料。

### Q: pytest import 报错
**A:** 确保在 backend 目录下运行，并已安装依赖：
```bash
pip install pytest httpx
```

### Q: 批次信息为空
**A:** 只有v2.0后新增的入库记录才会有批次信息，历史数据无批次。

---

## 测试数据清理

```bash
# 删除测试数据库（慎用）
rm backend/warehouse.db

# 重新初始化
cd backend
python -c "from database import init_database, generate_mock_data; init_database(); generate_mock_data()"
```
