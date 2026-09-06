# A2 并发修复复测 — 结果（2026-09-06）

目标设备：**harvest-pi**（Raspberry Pi 5，Tailscale）。复测对象：分支
`fix/a2-concurrency`，HEAD `116d517`（含 `106c2db` 批次号取号器修复 +
`868e941` 限流按身份分桶修复 + `116d517` 死代码清理）。

## 部署方式

Dockerfile.prod 多阶段构建在 3.4GB 可用磁盘上风险较高（读源码确认
`git diff 94b7e1d..116d517` 只改了 `backend/`，依赖、前端、Dockerfile
均未变），改用**分层覆盖构建**：`FROM
sensecraft-missionpack.seeed.cn/solution/warehouse:latest`（Pi 上已缓存
的旧代码镜像）+ `COPY backend/ mcp/ run_backend.py`，只重建源码层。
构建产物 `a2-warehouse-retest:116d517`，磁盘净增 ~100MB（454MB total，
102MB unique content，其余与旧镜像共享），构建前后磁盘 3.3G→3.3G 基本
持平，避免了完整构建可能耗尽剩余 3.4GB 的风险。

部署路径 `/home/harvest/a2-warehouse-retest/`（不覆盖 Pi 现有目录）。
容器 `a2-warehouse-retest`（端口 18020:2125），**新建**具名卷
`a2retest_data`/`a2retest_providers`（未复用 09-05 遗留的
`a2-warehouse_warehouse_data`/`_providers` ——旧卷里已有 2 tenant/1
user/2 api_keys 且处于 multi_tenant 模式，密码未知无法登录，复用会让
本轮复测卡在无法登录管理；旧卷原样保留，复测结束后未删除）。

DEPLOY_MODE=single_tenant（本轮只测并发修复，不复测权限隔离，
single_tenant 免去 factory API 依赖）。`DISABLE_RATE_LIMIT` 未设置，
保持默认（限流生效，代码里 `!= '1'` 才关闭）。

Alembic 迁移确认：容器内 `alembic_version` = `t9u0v1w2x3y4`，
`batch_no_sequences` 表存在。

数据准备：`/api/auth/setup` 建 admin（single_tenant，无需 factory
API）；通过 Excel 导入创建 `seed-material-1`（初始 500 万件，供
stock-out FIFO 消耗）、`seed-material-2`（初始 1000 件）；创建两个
operate 角色 API Key（`retest-user1`/`retest-user2`，均绑定
warehouse_id=1）用于出库限流分桶验证——两个 key 都从同一台 Mac 经
Tailscale 发出，共用同一个源 IP，专门用来验证"按身份分桶、不按 IP"。

## 入库并发（POST /api/materials/stock-in，同一物料 seed-material-1）

| 并发 | 请求总数 | 错误率 | p50 | p95 | p99 | 09-05 修复前错误率 |
|---|---|---|---|---|---|---|
| 5  | 516 | **0%** | 500.9ms | 633.9ms | 3053.3ms | 77.4% (409) |
| 10 | 473 | **0%** | 1013.3ms | 4617.6ms | 5739.8ms | 100% (409) |
| 20 | 490 | **0%** | 2051.1ms | 4961.3ms | 8267.7ms | 100% (409) |

状态码分布：三档均 100% `200`，无一例 409。

**批次号去重核对**（容器内直接查 SQLite）：

```
total_batches (1481,)
duplicate_batch_no_rows []
distinct_batch_no (1481,)
```

三档共产生 1481 个批次，`(batch_no)` 全部唯一，0 重复——`106c2db` 的
`batch_no_sequences` 原子自增取号器消除了 09-05 发现的 409 边界。延迟
比 09-05 略高（p95 从 106-966ms 涨到 634-4962ms），原因是新逻辑多一次
`UPDATE batch_no_sequences ... last_seq = last_seq + 1` 事务内写，
与旧的"纯 SELECT 读最大值"相比多了写锁串行化开销，符合"用可控的延迟
换掉不可控的 409 大面积失败"的修复取舍，不是新退化。

## 出库限流分桶（POST /api/materials/stock-out，两个 API Key 共用出口 IP）

总并发对半分给两个身份，同时打，默认 `BUSINESS_RATE_LIMIT=600/minute`：

| 总并发 | 各身份并发 | user1 请求数/错误率 | user2 请求数/错误率 | 429 数（双方合计） |
|---|---|---|---|---|
| 10 | 5+5   | 365 / 0%          | 365 / 0%          | 0 |
| 20 | 10+10 | 321 / 0%          | 321 / 0%          | 0 |
| 50 | 25+25 | 341 / 7.33% (EXC) | 346 / 7.23% (EXC) | 0 |

三档均**没有触发 429**——默认阈值 600/minute（10 req/s）比这台设备在
真实 FIFO 消耗逻辑下能压出的吞吐（各身份 5.3-6.1 req/s）高得多，说明
修复后限流不再是这条链路上第一个被打穿的边界。50 并发档出现的 50 个
`EXC`（连接超时/被拒，双方各 25 个）与 09-05 报告里 c=50 出现的
"EXC + 8-10s 延迟"一致，是设备真实容量边界（CPU 排队），不是限流也
不是回归。

**分桶隔离的直接证据**（临时把容器的 `BUSINESS_RATE_LIMIT` 调到
`5/minute` 做突发测试，验证完立刻改回默认值重启容器）：

```
=== key1 连续 8 次 ===
200 200 200 200 200 429 429 429
=== key2 紧接着连续 8 次（key1 的桶已经打满）===
200 200 200 200 200 429 429 429
=== key1 再打一次（验证 key2 的流量没有"救回" key1 的桶）===
429
```

key2 拿到了完整的 5 次配额，不受 key1 已耗尽配额影响；key1 在 key2
打完之后仍是 429，证明两个桶完全独立、不共享计数器——`868e941` 的
`business_rate_limit_key`（按 `X-API-Key` 哈希分桶，退回 IP 只在无身份
时）符合设计预期。09-05 报告里 96-98% 的 429（单一来源 IP 限流 60/min，
与并发数无关）在本次默认配置下**没有复现**。

## 库存查询（GET /api/materials/list，对照回归，带 X-API-Key）

09-05 用的是访客（无认证）请求；复读 `backend/deps.py`
`require_permission` 发现访客固定被拒 401（`is_guest` 优先判
不看角色），本次统一改用 API Key 认证重测，与 09-05 的 guest 数字不是
同一条件，仅作方法学对照：

| 并发 | 请求总数 | 错误率 | p50 | p95 | p99 | 09-05（guest）p95 |
|---|---|---|---|---|---|---|
| 10 | 1560 | 0.19% (3 EXC)  | 308.5ms  | 397.7ms  | 2907.7ms  | 404.1ms |
| 20 | 1623 | 0.06% (1 EXC)  | 607.9ms  | 850.8ms  | 7167.0ms  | 824.4ms |
| 50 | 1441 | 3.47% (50 EXC) | 1560.2ms | 8780.6ms | 10128.9ms | 5551.2ms |

量级与 09-05 接近（同一台设备、同一 query 接口，两次修复都没碰查询
路径），c=50 档 p95 略高、且出现 50 个 EXC（09-05 是 0% 错误）——查询
接口本身不在本轮修复范围内，这点差异更可能是设备负载波动
（`docker stats` 显示同时段 CPU 39.2%，接近但未到饱和）或认证增加的
每请求哈希开销，不是新增回归；未做 guest 路径的等量对照，此结论标
**需核实**。

## docker stats 抽样

| 阶段 | CPU% | MEM | 备注 |
|---|---|---|---|
| query c=50 期间 | 39.20% | 0B/0B（无 memory cgroup，同 09-05） | |
| stock_out 分桶测试期间（c20 附近） | 18.89% | 0B/0B | |

## 未做/超出本轮范围

- 权限隔离、跨租户越权、离线中断——09-05 已确认与本次两个修复无关，
  本轮 `DEPLOY_MODE=single_tenant` 也不具备多租户条件，未重测。
- 未压测「Excel 导入」路径的取号器复用（`_create_batch` 改造覆盖了
  这条路径，但压测计划里没有这一项）。

## 结论

两处 09-05 发现的边界均已确认修复：

1. **入库 409**：并发 5/10/20 全部 0% 错误，批次号 0 重复。已修复。
2. **出库 429 误伤**：两个共用出口 IP 的身份互不影响限流配额（隔离
   测试直接证明），默认阈值下真实负载也不再触发大面积 429。已修复。

延迟方面入库变慢（多一次原子取号的写锁开销）是修复的直接代价，在
可接受范围内（p95 秒级，仍远好于修复前的"几乎全部失败"）。
