# A2 边界压测计划 — 2026-09-05

commit: `94b7e1d088b7e93b1880150b9911cf8327164952`（2026-09-03 09:44:39 +0800）

## 系统事实（读源码确认，非猜测）

- 启动方式：`docker compose -f docker-compose.prod.yml up -d`，镜像
  `sensecraft-missionpack.seeed.cn/solution/warehouse:latest`（multi-arch
  amd64+arm64），容器内固定监听 **2125**，宿主端口在 compose `ports` 里改
  （模板默认 `1024:2125`）。数据库默认 SQLite，落 `/data`（named volume
  `warehouse_data`）；`DATABASE_URL` 非空则转 MySQL。
- 启动期不变式：alembic 迁移 + schema 校验 + `DEPLOY_MODE` 校验，任一失败
  容器直接退出（DEPLOY.md「启动期不变式」一节）。
- 鉴权/权限模型（`backend/deps.py`）：`get_current_user()` 优先级
  `X-API-Key` header > `session_token` cookie > 访客（guest, VIEW 权限）。
  三级角色 `VIEW(1) < OPERATE(2) < ADMIN(3)`（`Role`/`RoleName`），
  `require_permission(Resource, Action)` 按 Action(READ/WRITE/ADMIN) 映射
  最低角色。API Key 可绑定 `warehouse_id`（MCP/Agent 场景用，越权仓库会
  被 `can_access_warehouse()` 拒绝）。多租户下 tenant_id 隔离在
  `get_authorized_warehouses`/`can_access_warehouse` 里做二次过滤。
- 离线/重试逻辑：`mcp/mcp_pipe.py` 和 `backend/mcp_shared_runtime.py` 里
  的 reconnect/backoff **只用于 MCP 语音 WebSocket 通道**（连云端语音网
  关掉线重连），不是 HTTP REST API 层的重试队列。REST 接口（stock-in /
  stock-out / 查询类）是同步请求-响应，**没有找到离线队列或写入缓冲**
  ——网络中断期间发出的 HTTP 请求会直接失败/超时，不会被系统排队补发
  （已用 `grep -rniE "retry|offline|queue"` 通读 backend/*.py 和 mcp/*.py
  确认，仅 websocket 重连逻辑命中）。
- `/api/materials/stock-out` 有 slowapi 限流 `@limiter.limit("60/minute")`
  （`backend/app.py:4689` 附近），`stock-in` 未见相同装饰器 —— 高并发下
  stock-out 大概率先触发 429，这是压测预期会看到的边界现象之一，不是
  bug。
- 系统里**没有独立的「任务」资源**（没有 `/api/tasks`、无 task 表/model，
  `grep -rniE "class.*Task|task_type|/api/tasks|create_task"` 只命中
  asyncio.create_task 之类的运行时任务，与业务无关）。压测计划里的
  「任务创建」用 **`/api/materials/stock-in`**（创建入库记录，业务上等价
  于「登记一个入库任务」）代替，这是本次压测对任务书用词的映射假设，
  非系统固有概念，报告里会标注清楚。

## 待测接口清单

| 用途 | 方法+路径 | 权限要求 | 备注 |
|---|---|---|---|
| 库存查询 | `GET /api/materials/list` | READ (VIEW+) | 分页物料列表，压测主查询接口 |
| 库存查询（辅助） | `GET /api/dashboard/stats` | READ | 轻量只读，作为查询基线对照 |
| 库存更新 | `POST /api/materials/stock-in` | WRITE (OPERATE+) | 视为「任务创建」 |
| 库存更新 | `POST /api/materials/stock-out` | WRITE (OPERATE+) | 有 60/min 限流，FIFO 批次消耗 |
| 健康检查 | `GET /health` | 无 | 用于离线场景探测服务是否存活 |
| 权限隔离测试 | 上表 WRITE 接口 + `X-API-Key` | 用 VIEW 角色 key 打 WRITE 接口，预期 403 | |
| 权限隔离测试 | 跨租户 warehouse_id | 用租户 A 的 key 访问租户 B 的 warehouse_id，预期 403/404 | |

## 压测分级定义

- **稳定**：p95 < 500ms 且错误率 0%
- **下降**：p95 相对上一级翻倍，或错误率 > 1%（含限流 429）
- **失败**：错误率 > 10%，或容器崩溃/健康检查失败

## 环境

- 设备：待第二步 `fleet status` 后选定的 1 台 RK3576（优先 rk3576-armbian）
  + 1 台 Orin（优先 orin-nano）
- 部署路径：`~/a2-warehouse/`，用 `docker-compose.prod.yml`（从
  `.example` 复制并改宿主端口避开已有容器占用）
- 压测脚本：Python + httpx，`uv run --with httpx,...`，产物放
  `evaluation/`，原始输出落 `evaluation/runs/2026-09-05-load/raw/`
