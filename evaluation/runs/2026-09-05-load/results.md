# A2 边界压测 — 结果（2026-09-05）

目标设备：**harvest-pi**（Raspberry Pi 5，Tailscale，3.5GB 空闲盘）。
compose 覆盖文件、压测脚本先在 orin-nano 上验证过，中途按协调方指令改为
harvest-pi 后重新在目标设备上跑了一遍完整流程。Mac 本地数据作为脚本
验证用的参考数据保留在 `raw-mac-local/`，**不代表目标设备性能**。

commit: `94b7e1d088b7e93b1880150b9911cf8327164952`
镜像: `sensecraft-missionpack.seeed.cn/solution/warehouse:latest`
（harvest-pi 上缓存的 image id `c594a6be7abd`，与 orin-nano 上的
`04e26201e732` 是同一 tag 下不同 arch 的 manifest，符合 multi-arch 预期）
设备: Raspberry Pi 5（harvest-pi，arm64），DEPLOY_MODE=multi_tenant，
容器 `a2-warehouse-pi`（端口 18010:2125，避开已有的 `mcp_warehouse`
容器占用的 2125 端口），SQLite 后端，数据规模：50 个种子物料 + 1 个
warehouse + 1 个 tenant（部分用例另建了 tenant2 做跨租户测试）。
网络路径：Mac → Tailscale → harvest-pi（非本地环回，真实 WAN 抖动会
反映在数字里）。设备上同时运行着 10 个既有容器（含生产用的
`missionpack-industrial-gateway`），全程未触碰，压测结束后确认原样。

## 库存查询（GET /api/materials/list）— harvest-pi

| 并发 | p50 | p95 | p99 | 错误率 | 分级 |
|---|---|---|---|---|---|
| 1 | 38.8ms | 75.0ms | 767.9ms | 0% | 稳定 |
| 5 | 133.3ms | 199.0ms | 1859.5ms | 0% | 稳定 |
| 10 | 254.9ms | 404.1ms | 2681.7ms | 0% | 稳定（p95 仍 <500ms，但 p99 已明显拖尾） |
| 20 | 538.6ms | 824.4ms | 5253.9ms | 0% | **下降**（p95 首次越过 500ms 且相对上一级翻倍） |
| 50 | 1481.6ms | 5551.2ms | 8690.5ms | 0% | **下降/接近失败**（错误率仍 0%，但延迟已不可用：p95 达 5.5s） |

docker stats（`a2-warehouse-pi` 容器，query 测试期间抽样）：CPU 在
concurrency 10-50 阶段多次达到 44-73%（RPi5 四核，单容器吃满近一整核
是常态），空闲阶段 <1%；**MEM USAGE 恒为 0B/0B** —— `docker compose up`
时提示 "Your kernel does not support memory limit capabilities or the
cgroup is not mounted"，即这台设备内核未启用 memory cgroup，
`docker stats` 拿不到内存数字，这是设备限制不是压测脚本问题。

## 库存更新／"任务创建"（POST /api/materials/stock-in）— harvest-pi

| 并发 | p50 | p95 | p99 | 错误率 | 状态码分布 | 分级 |
|---|---|---|---|---|---|---|
| 1 | 64.7ms | 105.8ms | 844.4ms | 0% | 200×655 | 稳定 |
| 5 | 184.7ms | 292.8ms | 368.3ms | **77.4%** | 200×294, 409×1007 | **失败** |
| 10 | 367.1ms | 447.6ms | 4034.8ms | **100%** | 409×1349 | **失败** |
| 20 | 726.2ms | 966.2ms | 5061.5ms | **100%** | 409×1488 | **失败** |
| 50 | 1863.3ms | 4595.1ms | 6061.9ms | **100%** | 409×1455 | **失败** |

**关键发现（真实边界，非网络或限流噪音）**：并发 ≥5 时 stock-in 大量
返回 **409**（批次号冲突）。根因见 `backend/app.py:4611-4626`
附近——系统生成的 `batch_no`（格式 `YYYYMMDD-NNN`）撞
`(batch_no, warehouse_id)` 唯一约束时会 retry 最多 5 次重新生成再插入，
但重试预算是**固定 5 次**，与并发请求数无关。在 RPi5 上单请求耗时
64ms~1.9s（比 Mac 本地慢一个数量级），并发请求打到同一个
`material_id` 时，多个连接同时读到"当前最大序号"、都想抢占下一个
`batch_no`，5 次重试在高并发/高延迟组合下大概率全部撞车，最终对外
返回 409。**这是本次压测唯一一处"是否会崩"的真实系统边界**：并发
写同一物料的入库操作，在慢速设备上会以 409 形式大面积失败，而不是
优雅排队或线性降级。Mac 本地跑同样负载时该问题几乎不出现（本地网络
往返 <10ms，并发窗口太窄不容易撞车），说明**这个边界只有在目标设备
的真实延迟下才会暴露**，纯本地参考数据会完全遗漏它。

docker stats 抽样在这一段因 fleet exec 引号转义问题丢失（首次尝试的
`for` 循环命令被当成字面可执行文件名解析失败，`bash -c '...'` 包一层
后才恢复正常，详见 EVIDENCE），仅有一条空闲态快照
（`a2-warehouse-pi 0.09% 0B/0B`）可参考，不代表该阶段真实负载。

## 库存更新（POST /api/materials/stock-out）— harvest-pi

`backend/app.py` 的 `stock_out` 挂了 `@limiter.limit("60/minute")`
（`slowapi`，`key_func=get_remote_address`，按**来源 IP** 计数，与并发数
无关）：

| 并发 | 请求总数 | 200 成功数 | 429 数 | 其他 | 错误率 | p95 | p99 |
|---|---|---|---|---|---|---|---|
| 1 | 1823 | 60 | 1763 | - | 96.71% | 55.9ms | 74.2ms |
| 5 | 2005 | 65 | 1940 | - | 96.76% | 249.2ms | 2047.2ms |
| 10 | 2277 | 115 | 2162 | - | 94.95% | 538.9ms | 2625.3ms |
| 20 | 2411 | 60 | 2351 | - | 97.51% | 738.0ms | 3309.7ms |
| 50 | 1661 | 37 | 1574 | 50 EXC | 97.77% | 8142.4ms | 10051.9ms |

结论与 Mac 参考数据一致：出库接口从单一来源 IP 的有效吞吐固定卡在
**60 次/分钟（约 1 req/s）**，与并发数无关，这是限流生效不是资源
耗尽。但在并发=50 这一档，目标设备额外出现了 **50 个客户端异常
（EXC，连接超时/被拒）**且 p95 冲到 8.1s、p99 到 10s——这一部分是
真实的设备资源边界（RPi5 CPU 在高并发连接下的排队延迟），和限流的
429 是两个不同成因叠加在一起，报告里分开看：429 是设计内行为，
EXC + 8-10s 延迟是这台设备在 50 并发下的真实容量边界。

docker stats（stock_out 测试期间）：CPU 主要在 20-30% 区间（大部分
请求被限流直接拒绝，未走到完整业务逻辑，CPU 消耗低于 query/stock-in）。

## 权限隔离 — harvest-pi

- VIEW 角色 key 打 WRITE 接口（`/api/materials/stock-in`）→ **403**
- VIEW 角色 key 打 READ 接口（`/api/materials/list`）→ **200**
- 租户1 operate key 写入租户2 的 warehouse_id → **403 "无权访问该仓库"**
- 租户1 view key 读取租户2 的 warehouse_id 数据 → **403**

结论：角色/租户双重校验在目标设备上行为与 Mac/orin-nano 一致，
本轮测试未发现越权漏洞。

## 离线场景 — harvest-pi

**方法变更**：harvest-pi 上没有装 `iptables`（`command not found`），
`nft` 也没有，无法用防火墙规则模拟断网。改用任务书里给出的第二个选项
——**停掉依赖服务**：`docker stop a2-warehouse-pi` 30 秒后
`docker start` 恢复（只停自己起的测试容器，不影响其余 9 个容器）。

持续以约 10 req/s 打 `/api/materials/list`：

- 中断期（t≈15.8s 容器被 stop，到 t≈49.9s 容器 start 完成+应用重新
  启动，约 34s——比预期的 30s 略长，多出的时间是容器重启+FastAPI
  重新走一遍 alembic 迁移检查的开销）：**100% 请求失败**
  （`httpx.ConnectError`，端口直接拒绝连接），全程无一例外。
- 恢复：t=49.9s 那一秒过渡（6 ok / 7 fail 混合），t=50.9s 起完全恢复
  到 0% 错误、~10 req/s 稳定。

结论：与 Mac 本地（`docker network disconnect` 方式）及源码阅读结论
一致——REST API 层没有离线队列，中断期间请求直接失败，系统不做
缓存补发；恢复后无积压、无脏数据。目标设备上因为"停服务"比"断网线"
多了一段应用重启时间（migration 检查等），实际不可用窗口比协议
本身的 30 秒略长，这点在真实运维场景（容器重启/OOM-kill 后拉起）
里需要考虑。

## Mac 本地参考数据（非目标设备，仅供对照，见 `raw-mac-local/`）

Mac（Apple M4 + Docker Desktop，localhost 回环）上跑同样脚本得到的
数字明显更好（query p95 从 1207ms@c50 起降级，stock-in 全程 0 错误，
stock-out 429 模式一致）。两者对比说明：**限流行为（429）与权限隔离
是设备无关的系统设计，而延迟数字、409 批次冲突、EXC 连接失败是
设备相关的真实容量边界**，边界报告应以 harvest-pi 数据为准，Mac
数据仅用于验证压测方法学本身没有 bug。

## 清理

harvest-pi：`docker compose down` 移除 `a2-warehouse-pi` 容器和
`a2-warehouse_default` 网络，`rm -rf ~/a2-warehouse`；具名 volume
（`a2-warehouse_warehouse_data`/`_providers`）按护栏规则未删除（禁止
`down -v`），仅含测试数据。设备上原有 10 个容器验证后原样运行，磁盘
可用空间压测前后一致（3.5G，无净增长，因为镜像层与已有的 `mcp_warehouse`
镜像共享）。

orin-nano：中途放弃该设备前已 `docker compose down` + `rm -rf
~/a2-warehouse`，同样只清了自己起的 project，具名 volume 同理保留未删。
