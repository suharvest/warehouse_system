# 测试文件说明

本目录包含仓库管理系统的所有测试脚本。

## 测试文件

### 1. test_mcp.py - MCP 工具测试

测试 MCP 工具的入库、出库、查询功能。

**运行方式：**
```bash
# 从项目根目录运行
python3 test/test_mcp.py

# 或从 test 目录运行
cd test
python3 test_mcp.py
```

**测试内容：**
- ✅ 查询库存
- ✅ 入库操作
- ✅ 验证入库结果
- ✅ 出库操作
- ✅ 验证出库结果
- ✅ 错误处理（库存不足）

**预期输出：**
```
仓库管理系统 MCP 工具测试
============================================================

1. 测试: 查询 watcher-xiaozhi(标准版) 库存
...
验证结果: ✅ 通过

测试完成！
```

### 2. test_mcp_statistics.py - MCP 统计接口测试

测试 MCP 新增的统计数据查询功能。

**运行方式：**
```bash
# 从项目根目录运行
python3 test/test_mcp_statistics.py

# 或从 test 目录运行
cd test
python3 test_mcp_statistics.py
```

**测试内容：**
- ✅ 查询今日入库数量
- ✅ 查询今日出库数量
- ✅ 查询当前库存总量
- ✅ 查询库存预警数量
- ✅ 统计数据变化验证

**预期输出：**
```
MCP 统计接口测试
============================================================

测试: 查询今日统计数据
------------------------------------------------------------
日期: 2025-11-07
今日入库: 255 件
今日出库: 137 件
净变化: 118 件
当前库存总量: 3350 件
库存预警数量: 0 种

✅ 统计数据查询成功！
```

### 3. test_api.py - API 接口测试

测试后端 Flask API 的所有接口。

**前置条件：**
后端服务必须运行（端口 2124）

**运行方式：**
```bash
# 从项目根目录运行
python3 test/test_api.py

# 或从 test 目录运行
cd test
python3 test_api.py
```

**测试内容：**
- ✅ 仪表盘统计数据
- ✅ 库存类型分布
- ✅ 近7天出入库趋势
- ✅ 库存TOP10
- ✅ 所有物料列表
- ✅ watcher-xiaozhi 相关物料
- ✅ 库存预警

**预期输出：**
```
仓库管理系统 API 测试
============================================================

✅ 后端服务运行正常

1. 测试: 获取仪表盘统计数据
...
所有接口测试通过 ✅
```

## 运行所有测试

```bash
# 从项目根目录
./test/run_all_tests.sh

# 或
cd test
./run_all_tests.sh
```

## 测试前准备

### 1. 确保数据库已初始化

```bash
cd backend
uv run python database.py
```

### 2. 启动后端服务（API 测试需要）

```bash
uv run python run_backend.py
```

### 3. 安装依赖（API 测试需要）

```bash
uv add requests
```

## 注意事项

1. **test_mcp.py**
   - 直接操作数据库，不需要后端服务运行
   - 会修改数据库数据（入库/出库）
   - 测试完成后库存会发生变化

2. **test_api.py**
   - 需要后端服务运行
   - 只读操作，不会修改数据
   - 如果后端未运行会提示错误

## 常见问题

### 数据库错误

如果遇到 "no such table" 错误：
```bash
cd backend
uv run python database.py
cd ..
```

### API 连接失败

如果 test_api.py 提示无法连接：
1. 检查后端服务是否运行
2. 确认端口 2124 未被占用
3. 启动后端：`uv run python run_backend.py`

### 路径错误

测试脚本使用绝对路径，可以从任何目录运行：
```bash
# 都可以正常工作
python3 test/test_mcp.py
cd test && python3 test_mcp.py
```

## 测试数据

测试使用的产品：
- **watcher-xiaozhi(标准版)**
- SKU: FG-WZ-STD
- 初始库存: 约 52 台
- 安全库存: 15 台

每次测试会：
1. 入库 10 台
2. 出库 5 台
3. 净增加 5 台

## 清理测试数据

如果想恢复初始状态：
```bash
rm backend/warehouse.db
cd backend
uv run python database.py
```

## 文件权限

确保测试脚本可执行：
```bash
chmod +x test/*.py
chmod +x test/run_all_tests.sh
```

## 集成到 CI/CD

```yaml
# 示例 GitHub Actions
- name: Run Tests
  run: |
    cd backend && uv run python database.py
    cd ..
    python3 test/test_mcp.py
    uv run python run_backend.py &
    sleep 3
    python3 test/test_api.py
```
