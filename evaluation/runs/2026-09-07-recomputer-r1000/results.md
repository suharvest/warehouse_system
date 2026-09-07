# reComputer R1000 平台（CM4，2 GB 配置）实测 — 2026-09-07

## 设备与部署

- 台架：fleet `seeed-pi4`。Raspberry Pi 4B，**2 GB 内存**，4 × Cortex-A72，
  Debian 12（内核 6.12.75），aarch64，Docker 29.4.1。
  页面口径：reComputer R1000 同款 CM4 平台的 2 GB 配置参考值。
  **R1000 出货为 4 GB / 8 GB，本轮未在出货配置上复测。**
- 部署：直接用方案自带的
  `sensecraft-solutions/solutions/smart_warehouse/assets/docker/docker-compose.yml`，
  compose project 名 `bench-warehouse`，镜像按 digest 固定
  `sensecraft-missionpack.seeed.cn/solution/warehouse@sha256:04e26201e732d11fb5a14cf1e193456386210d5fd8a4e864cc8b6d2b7a10d19d`
  （与页面引用的同一枚 digest）。端口 2125，SQLite 后端，
  新建卷 `bench-warehouse_warehouse_data` / `_providers`，未复用任何既有卷。
- 部署前 `free -m`：total 1845 / available 1511；`df -h /`：29G，39% 已用。
  部署后：available 1311，40% 已用，温度 57.9 °C。
- 同机既有的 `vision-cm4` 容器（3 个月前退出）全程未动。
- 起容器后 30s，`/health` 返回 200，compose 状态 `Up (health: starting)` →
  随后 healthy。

## 已测：重启恢复时间

`docker compose -p bench-warehouse restart warehouse` 到 `/health` 返回 200
的墙钟时间（Pi 本地回环轮询，250 ms 间隔）：

| 轮次 | restart → /health 200 |
|---|---:|
| 1 | 9.48 s |
| 2 | 9.04 s |

n=2。容器稳定后 `docker stats` CPU 13.56%。

这一项与页面现有的「服务中断 ~34 s」行不是同一个量：页面那行是 stop + start
的完整中断窗口（含启动时的迁移检查），本轮测的是 `restart` 到健康检查通过。
两者不可直接相减比较，仅作为本平台的启动侧参考。

## 已测：未鉴权请求路径

`evaluation/loadtest.py --scenario query`，Mac 客户端经 Tailscale 打到
`http://100.119.146.84:2125`，每档 30 s：

| 并发 | 请求总数 | 吞吐 | p50 | p95 | p99 | 状态码 |
|---|---:|---:|---:|---:|---:|---|
| 5 | 1301 | 43.37 req/s | 103.4 ms | 172.9 ms | 431.7 ms | 100% `401` |
| 10 | 2525 | 84.17 req/s | 111.9 ms | 162.6 ms | 278.2 ms | 100% `401` |

**这不是页面「库存查询」那一行的可比数字。** 全部请求在鉴权层就被拒，没有
碰到数据库和业务逻辑，只反映 HTTP 栈 + 鉴权中间件的时延。页面上的
p95 404 ms（并发 10）走的是完整业务路径，两者不可比，不得混用。

原始输出：`raw/query_summary.json`、`raw/query_log.txt`。

## 未测：全部鉴权后的业务负载

页面「实测边界」表里除服务中断外的每一行——库存查询 p95/p99、入库并发
（含批次号去重）、出库限流分桶、角色与租户隔离——都要求先通过
`/api/auth/setup` 创建管理员账号，再建物料与两个 operate 角色 API Key。

执行者不创建账号、不代填密码，因此本轮没有跑这些用例。要补齐，需要由有权限
的人在这台台架上完成建号与建 Key，之后按 `evaluation/runs/2026-09-06-a2-retest/results.md`
里记录的同一套步骤跑 `loadtest.py` 的 query / stock_in / stock_out 三个
scenario 与 `offline_test.py`。

## 未测：控制台截图

控制台所有页面在未登录时跳转到首次设置页，同样受上述限制，本轮未采集。

## 收尾

`docker compose -p bench-warehouse down` 已执行；`bench-warehouse` 的两个具名卷
按本次 run 的记录保留（未 `-v`），同机其它 project 与镜像未动。
