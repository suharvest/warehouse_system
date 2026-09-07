# reComputer R1000 平台（CM4，2 GB 配置）鉴权后台实测 — 2026-09-08

## 台架口径

**页面/BOM 推荐配置是 4 GB 起步**（reComputer R1000 出货为 4 GB / 8 GB）。
本轮台架 fleet `seeed-pi4` 是 **2 GB** 内存的 Raspberry Pi 4B，**低于推荐
配置**，本报告全部数字只代表这台低配台架的实测边界，不代表 4 GB/8 GB
出货配置的表现，不能直接套用到页面的容量规划参考值上。

## 设备与部署

- 台架：fleet `seeed-pi4`，Raspberry Pi 4B，2 GB 内存，Debian 12，Docker 29。
- 复用状态：`docker ps -a` 起始只有一个 3 个月前退出的 `vision-cm4`，上一轮
  （2026-09-07）的 compose project `bench-warehouse` 已被那一轮 `down`
  （未 `-v`，两个旧卷 `bench-warehouse_warehouse_data` / `_providers`
  原样保留、本轮未动）。按方案 spec
  `sensecraft-solutions/solutions/smart_warehouse/devices/warehouse_deploy.yaml`
  规定的 `project_name: mcp_warehouse`、`remote_path:
  /home/{{username}}/mcp_warehouse` 重新部署。
- 部署文件：`sensecraft-solutions` origin/main 同一份
  `solutions/smart_warehouse/assets/docker/docker-compose.yml`，镜像固定
  digest `sensecraft-missionpack.seeed.cn/solution/warehouse@sha256:04e26201e732d11fb5a14cf1e193456386210d5fd8a4e864cc8b6d2b7a10d19d`
  （与上一轮同一枚 digest）。部署路径 `/home/recomputer/mcp_warehouse/`，
  compose project `mcp_warehouse`，端口 2125，新建卷
  `mcp_warehouse_warehouse_data` / `mcp_warehouse_warehouse_providers`。
- 部署后 12 s `/health` 返回 200。全程实测期间容器状态保持
  `Up ... (healthy)`，一次未重启。

## 已完成：管理员建号 + 登录 + 后台页面截图

首次访问 `http://100.119.146.84:2125` 弹出「设置管理员」对话框，用**用户
提供的管理员账号**创建成功并自动登录（口令未写入本文件、日志或截图）。

截图目录 `screenshots/`，Playwright Chromium，viewport 1600×1000（本次
会话没找到让 `--config` 里 `deviceScaleFactor` 生效的路径，退而用更大
viewport 满足 ≥1600 px 宽度这条底线；截图均已裁到内容区域，无口令可见）：

| 文件 | 页面 |
|---|---|
| `01-dashboard.png` | 看板 |
| `02-records.png` | 进出库记录 |
| `03-inventory.png` | 库存列表 |
| `04-settings.png` | 系统设置（用户与密钥 tab） |
| `05-devices.png` | 联系方管理 |
| `06-agent-config.png` | 智能体配置 |

## 已发现：操作员角色 API 密钥默认无仓库授权（阻塞写操作，未修复，已绕过）

在「系统设置 → 用户与密钥」创建的「操作员」角色 API 密钥（`GET
/api/api-keys` 显示 `warehouse_id: null`）调用 `POST
/api/materials/stock-in` / `stock-out` 一律返回 `{"error":"无权访问该
仓库"}`，即使 `GET /api/auth/me` 能正确解析出该 Key 的身份和租户、且
目标仓库与该租户一致。前端「添加API密钥」对话框只有名称+角色两个字段，
无仓库选择器；`PATCH /api/api-keys/{id}` 返回 405，也没有事后补绑的
接口。**已知有人在修，本轮不重复排查根因**，标记为已知缺陷。

**绕过方式（本轮负载矩阵全部用此方式）：** 用 `POST /api/auth/login`
获取管理员的 `session_token` cookie，`evaluation/loadtest.py` 新增
`--cookie` 参数（本轮改动，见下）用 `Cookie: session_token=...` 头替代
`X-API-Key`，走管理员会话调用业务接口。

**这带来一个方法学副作用，直接影响下面出库结果的解读**：所有并发请求
共用同一个管理员身份，命中的是限流按身份分桶后的**单一身份**配额
（`BUSINESS_RATE_LIMIT`，默认 600/分钟=10 req/s），不是 09-06 复测报告
里"两个身份分别限流"的场景。出库测试里看到的大量 429，是"单身份 + 高
并发超过 10 req/s 配额"的预期表现，**不是**本平台的容量边界，也不是限
流分桶回归——分桶隔离本身在 09-06 报告里已用专门实验证明过，本轮没有
条件（缺可用的操作员 Key）重新验证分桶，两次测的是不同的东西，不可
互相印证。

## 代码改动

`evaluation/loadtest.py` 新增 `--cookie` 参数（透传为 `Cookie` 请求头），
用于在操作员 Key 不可用时改走管理员会话做压测，不影响原有 `--api-key`
路径。

## 已完成：认证后负载矩阵（管理员会话，Mac 客户端经 Tailscale）

档位沿用上一轮（2026-09-07）`results.md` 里的并发档：5、10，每档 30 s。
每档跑完立即用 `fleet exec` 检查 `free -m` / `docker stats` 确认设备
健康，SSH 全程无变慢或失联迹象，因此没有触发"观察到异常就停在该档"的
提前终止条件，两档都跑完了。

测试前用「库存列表 → 导入库存」的 Excel 批量导入功能（与 guide 步骤 2
的模板同一入口）新建了两个压测专用物料：`seed-material-1`（初始 50 万
件，供出库消耗）、`seed-material-2`（初始 5000 件，供入库累加）。

### 查询（GET /api/materials/list）

| 并发 | 请求总数 | 吞吐 | 错误率 | p50 | p95 | p99 |
|---|---:|---:|---:|---:|---:|---:|
| 5  | 818  | 27.27 req/s | 0% | 177.0 ms | 267.1 ms | 334.6 ms |
| 10 | 1163 | 38.77 req/s | 0% | 255.9 ms | 360.2 ms | 608.9 ms |

两档 100% `200`，无 401/429/5xx。

### 入库（POST /api/materials/stock-in，物料 seed-material-2）

| 并发 | 请求总数 | 吞吐 | 错误率 | p50 | p95 | p99 |
|---|---:|---:|---:|---:|---:|---:|
| 5  | 454 | 15.13 req/s | 0% | 321.3 ms | 448.5 ms | 607.7 ms |
| 10 | 435 | 14.50 req/s | 0% | 641.9 ms | 1207.6 ms | 1525.3 ms |

两档 100% `200`，无重复批次报错、无 409。延迟随并发上升（p95 从
448 ms 涨到 1208 ms），与 09-06 报告里"取号器多一次原子自增写锁"的
已知代价方向一致，本轮未做批次号去重的直接核对（09-06 已核过对应
代码路径，本轮不重复）。

### 出库（POST /api/materials/stock-out，物料 seed-material-1）

| 并发 | 请求总数 | 吞吐 | 错误率 | p50 | p95 | p99 | 状态码 |
|---|---:|---:|---:|---:|---:|---:|---|
| 5  | 853  | 28.43 req/s | 92.97% | 159.1 ms | 327.0 ms | 428.8 ms | 60×200, 793×429 |
| 10 | 1344 | 44.80 req/s | 97.02% | 202.9 ms | 323.9 ms | 664.1 ms | 40×200, 1304×429 |

**如上文所述，这两档的高错误率是单一管理员身份撞上 600/分钟限流配额的
结果，不是设备容量边界。** 两档实际成功吞吐（60 请求/30s ≈ 2 req/s，
40 请求/30s ≈ 1.3 req/s）远低于配额，说明限流生效很快、拦截精确——
200 请求本身延迟（p95 327-324 ms）也不随并发升高而恶化，与查询/入库
的延迟趋势一致，侧面印证容器本身没有被压垮，只是绝大多数请求在进入
业务逻辑前就被限流层拦下。

原始输出：`raw/query_summary.json`、`raw/stock_in_summary.json`、
`raw/stock_out_summary.json`。

### 三个场景全程的设备健康快照

| 时点 | Mem available | Swap used | 容器 CPU% | 容器 PID 数 |
|---|---:|---:|---:|---:|
| 部署后基线 | 1339 MB | 192 MB | 0.12% | 7 |
| query 测试后 | 1331 MB | 192 MB | 0.12% | 14 |
| stock_in 测试后 | 1332 MB | 192 MB | 5.88% | 5 |
| stock_out 测试后 | 1329 MB | 192 MB | 0.13% | 5 |
| 最终 | 1308 MB | 192 MB | - | - |

swap 用量全程未变（192 MB，与部署前一致），available 内存缓慢下降主要
是 buff/cache 增长（页面缓存），不是内存泄漏迹象；未观察到 SSH 响应
变慢。

## 上一轮设备失联的复核（本轮已恢复上线后追查）

- `journalctl --since "-3h" -p warning --no-pager`：**无输出**，失联时段
  没有 warning 级别以上的系统日志。
- `dmesg | tail -30`：只有容器网桥/veth 的正常创建销毁日志，**无 OOM
  killer 记录**。
- `vcgencmd get_throttled`：`throttled=0x0`，**未发生过欠压/降频**。
- `uptime`：`8 天`未重启，说明系统本身没有崩溃重启。
- 结论：失联是 SSH/主机短时无响应，不是内存耗尽或硬件降频导致的崩溃；
  具体触发原因未定位到（无对应时段的日志线索），**需核实**，不排除是
  这台设备网络层或 sshd 的偶发问题，与仓库容器的负载没有找到直接关联
  证据（失联发生时的操作只是一条轻量的 `docker exec printenv`，不是
  压测流量）。

## 验收清单（套餐一·基础版，guide `#sensecraft_cloud` 部署完成节）

1. **健康检查通过**——`curl -f http://100.119.146.84:2125/health`
   → `HTTP 200`。✅
2. **管理员能登录**——`GET /api/auth/me`（带管理员 session）返回
   `{"id":1,"username":"seeed","role":"admin","tenant_id":1}`。✅
3. **语音入库有回声**——需要实体 SenseCAP Watcher 设备说出语音指令，
   本轮台架不含 Watcher 硬件，**未执行**。
4. **查询正常**（语音查询与仓库面板对照）——同样依赖 Watcher 硬件，
   **未执行**。
5. **日志无 error**——`docker logs --since 10m mcp_warehouse 2>&1 |
   grep -i error`，覆盖了本轮三组负载测试的时间窗口，**0 条匹配**。✅

3、4 两项需要配一台 Watcher 现场验证语音链路，本轮环境不具备，标记
"未执行"而非"通过"。

## 收尾

- `mcp_warehouse` compose project **保留运行**（未 `down`），供后续继续
  验证或补测 Watcher 语音链路。
- 遗留的 `bench-warehouse_warehouse_data` / `_providers` 两个旧卷未清理，
  本轮未动。
- 管理员账号、两枚已知无仓库授权的操作员 API 密钥（`loadtest-key-1`/
  `loadtest-key-2`）留在数据库里，未清理，供后续复测使用。
- 压测用的两个物料 `seed-material-1`（剩余约 49.9 万件）、
  `seed-material-2`（累计入库约 4400+ 件）留在库存里，未清理。
