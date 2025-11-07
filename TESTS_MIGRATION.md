# 测试文件迁移说明

## 变更内容

所有测试文件已统一移动到 `test/` 目录。

### 文件变更

**之前的位置：**
```
warehouse_system/
├── test_mcp.py          # 根目录
├── test_api.sh          # 根目录
└── ...
```

**现在的位置：**
```
warehouse_system/
├── test/
│   ├── test_mcp.py         # MCP 工具测试
│   ├── test_api.py         # API 接口测试（重写为 Python）
│   ├── run_all_tests.sh    # 运行所有测试脚本
│   └── README.md           # 测试文档
└── ...
```

## 改进内容

### 1. 统一测试目录结构
- 所有测试文件集中在 `test/` 目录
- 更清晰的项目结构
- 符合 Python 项目最佳实践

### 2. 路径独立性
- 测试脚本使用绝对路径
- 可以从任何目录运行
- 自动定位项目根目录

### 3. 新增功能

#### test_api.py（新增）
- 重写为 Python 脚本
- 更好的错误处理
- 自动检测后端服务状态
- 格式化的 JSON 输出

#### run_all_tests.sh（新增）
- 一键运行所有测试
- 自动检查并启动后端服务
- 测试结果汇总
- 自动清理

#### test/README.md（新增）
- 完整的测试文档
- 运行说明
- 常见问题解答

## 运行方式

### 从项目根目录运行

```bash
# 运行所有测试
./test/run_all_tests.sh

# 单独运行 MCP 测试
python3 test/test_mcp.py

# 单独运行 API 测试
python3 test/test_api.py
```

### 从 test 目录运行

```bash
cd test

# 运行所有测试
./run_all_tests.sh

# 单独运行测试
python3 test_mcp.py
python3 test_api.py
```

### 从任意目录运行

```bash
# 只要在项目内，都可以运行
cd frontend
python3 ../test/test_mcp.py

cd backend
python3 ../test/test_api.py
```

## 测试保证

### ✅ 路径问题已解决

**问题：** 之前 `test_mcp.py` 使用相对路径，需要从特定目录运行

**解决：**
```python
# 获取项目根目录
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
backend_dir = os.path.join(project_root, 'backend')

# 切换到正确的目录
os.chdir(backend_dir)
```

### ✅ 数据库路径已修复

**问题：** 数据库文件 `warehouse.db` 在 `backend/` 目录，路径引用容易出错

**解决：**
- 测试脚本自动切换到 backend 目录
- 使用绝对路径定位
- 无论从哪里运行都能找到数据库

### ✅ 依赖管理

**新增依赖：**
```bash
uv add requests  # test_api.py 需要
```

## 验证测试

### 测试 1：从根目录运行
```bash
python3 test/test_mcp.py
```

**预期结果：** ✅ 通过

### 测试 2：从 test 目录运行
```bash
cd test
python3 test_mcp.py
```

**预期结果：** ✅ 通过

### 测试 3：运行所有测试
```bash
./test/run_all_tests.sh
```

**预期结果：**
```
🎉 所有测试通过！

测试结果：
  ✅ MCP 工具测试
  ✅ API 接口测试
```

## 文档更新

以下文档已更新测试路径引用：

- ✅ `README.md` - 添加测试章节
- ✅ `MCP_README.md` - 更新测试方法
- ✅ `CLAUDE_DESKTOP_CONFIG.md` - 更新测试命令
- ✅ `PROJECT_SUMMARY.md` - 更新项目结构
- ✅ 新增 `test/README.md` - 测试专用文档

## 兼容性

### 向后兼容
- 旧的测试脚本已删除
- 如果有自定义脚本引用旧路径，需要更新

### 迁移步骤

如果你有自定义脚本：

**旧的调用：**
```bash
python3 test_mcp.py
```

**新的调用：**
```bash
python3 test/test_mcp.py
```

## 好处

1. **更清晰的结构** - 测试文件集中管理
2. **更好的可维护性** - 测试代码独立
3. **更容易扩展** - 添加新测试更方便
4. **符合规范** - 遵循 Python 项目最佳实践
5. **路径无关** - 从任何位置运行都正常

## 测试覆盖

### test_mcp.py
- ✅ 查询库存
- ✅ 入库操作
- ✅ 出库操作
- ✅ 库存验证
- ✅ 错误处理

### test_api.py
- ✅ 仪表盘统计
- ✅ 类型分布
- ✅ 近7天趋势
- ✅ 库存TOP10
- ✅ 所有物料
- ✅ xiaozhi产品
- ✅ 库存预警

### run_all_tests.sh
- ✅ 数据库初始化检查
- ✅ 后端服务检查
- ✅ 自动启动后端
- ✅ 测试结果汇总
- ✅ 自动清理

## 常见问题

### Q: 测试失败怎么办？

A: 查看 `test/README.md` 的常见问题章节

### Q: 需要安装新的依赖吗？

A: 是的，需要安装 `requests`：
```bash
uv add requests
```

### Q: 可以同时运行多个测试吗？

A: 使用 `run_all_tests.sh` 会按顺序运行所有测试

### Q: 测试会修改数据库吗？

A: `test_mcp.py` 会修改数据（入库/出库），`test_api.py` 只读

## 总结

✅ 测试文件已成功迁移到 `test/` 目录
✅ 所有测试脚本路径无关，可从任何位置运行
✅ 添加了完整的测试文档
✅ 提供了一键运行所有测试的脚本
✅ 所有相关文档已更新

现在你可以使用：
```bash
./test/run_all_tests.sh
```
来运行所有测试！
