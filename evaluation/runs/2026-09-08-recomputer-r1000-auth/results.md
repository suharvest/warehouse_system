# reComputer R1000 平台（CM4，2 GB 配置）鉴权后台实测 — 2026-09-08（部分完成）

## 状态

**本轮在设备失联后中止。** 已完成：管理员建号、登录、后台六个页面的渲染截图、
API 密钥创建。**未完成**：完整鉴权负载矩阵（query / stock-in / stock-out）
一条都没有跑——先卡在 API 密钥的仓库授权问题上，还没排完就遇到设备失联。
「验收清单」五条逐条执行也未开始。

## 设备与部署

- 台架：fleet `seeed-pi4`，Raspberry Pi 4B，2 GB 内存，Debian 12，Docker 29。
- 复用状态：上一轮（2026-09-07）用的 compose project `bench-warehouse` 已在
  当时的 run 里 `down`（未 `-v`），本轮开始时 `docker ps -a` 只有一个 3 个月前
  退出的 `vision-cm4`，没有活着的仓库容器。按方案 spec
  `sensecraft-solutions/solutions/smart_warehouse/devices/warehouse_deploy.yaml`
  规定的 `project_name: mcp_warehouse`、`remote_path: /home/{{username}}/mcp_warehouse`
  重新部署，不是复用旧的 `bench-warehouse`（两者卷名不同，互不影响，
  `bench-warehouse_warehouse_data` / `_providers` 两个旧卷原样保留在设备上）。
- 部署文件：`sensecraft-solutions` origin/main 同一份
  `solutions/smart_warehouse/assets/docker/docker-compose.yml`，镜像固定
  digest `sensecraft-missionpack.seeed.cn/solution/warehouse@sha256:04e26201e732d11fb5a14cf1e193456386210d5fd8a4e864cc8b6d2b7a10d19d`
  （与上一轮同一枚 digest）。部署路径 `/home/recomputer/mcp_warehouse/`，
  compose project `mcp_warehouse`，端口 2125，新建卷
  `mcp_warehouse_warehouse_data` / `mcp_warehouse_warehouse_providers`。
- `docker compose -p mcp_warehouse up -d` 后 12 s，`docker ps` 显示
  `health: starting`，`curl /health` 返回 200。
- 部署后 `free -m`：total 1845 / available 1274（含 buff/cache 1209 MB）；
  swap 已用 192 MB（部署前也已是 192 MB，非本次部署新增）。
  `df -h /`：29G，39% 已用，17G 可用。

## 已完成：管理员建号 + 登录 + 后台页面截图

首次访问 `http://100.119.146.84:2125` 弹出「设置管理员」对话框，用**用户
提供的管理员账号**创建成功并自动登录（口令未写入本文件或任何日志/截图）。

截图目录 `screenshots/`，Playwright Chromium，viewport 1600×1000（未启用
DPR 2——工具在这次会话里没找到让 `--config` 的 `deviceScaleFactor` 生效的
路径，退而用更大 viewport 满足 ≥1600 px 宽度这条底线；截图均已裁到内容
区域，无口令可见）：

| 文件 | 页面 |
|---|---|
| `01-dashboard.png` | 看板 |
| `02-records.png` | 进出库记录 |
| `03-inventory.png` | 库存列表 |
| `04-settings.png` | 系统设置（用户与密钥 tab） |
| `05-devices.png` | 联系方管理 |
| `06-agent-config.png` | 智能体配置 |

## 已发现：操作员角色 API 密钥默认无仓库授权（阻塞写操作，与本轮失联无关）

在「系统设置 → 用户与密钥」创建了两个「操作员」角色 API 密钥
（`loadtest-key-1`、`loadtest-key-2`，值均已在会话中使用过、未落盘保存，
本文件不记录明文）。用其中一个调用
`POST /api/materials/stock-in`（`warehouse_id: 1`，即页面显示的「默认仓库」
`default`，`GET /api/warehouses` 确认该仓库 `id=1, tenant_id=1` 与管理员
同租户）返回 `{"error":"无权访问该仓库"}`。

排查过程：
- `GET /api/auth/me`（带同一枚 API Key）能正确解析身份
  （`role: "operate"`, `tenant_id: 1`），说明 Key 本身有效、租户匹配。
- `GET /api/api-keys`（管理员 cookie 会话）显示两枚新建 Key 的
  `warehouse_id` 字段均为 `null`——即页面的「添加API密钥」对话框（仅
  名称 + 角色两个字段，无仓库选择器）创建出来的操作员 Key 没有绑定任何
  仓库，因此对写接口一律判定无权限。
- 用管理员 cookie 会话直接调用同一 `stock-in` 接口（`warehouse_id:1`）
  能正常进入业务逻辑（返回“产品不存在”而不是权限错误），证明问题在
  API Key 的仓库授权字段，不在仓库 ID 或路径本身。
- 尝试 `PATCH /api/api-keys/1` 补绑仓库，返回 405，说明前端未暴露、后端
  也没有事后修改仓库绑定的接口。

**结论（需核实是否为已知设计）：** 单仓库（`single_tenant`）部署下，通过
现有后台 UI 创建的操作员角色 API Key 无法用于任何写接口（入库/出库），
只能用于不校验仓库归属的只读接口。这会直接挡掉 guide 里「入库并发」
「出库限流分桶」两项验收/压测，需要方案侧确认是否有遗漏的仓库绑定步骤，
或是否需要修复。本轮未继续深挖后端源码去确认根因（时间被设备失联打断），
标"需核实"。

## 未完成：完整负载矩阵

未跑成任何一档 `query` / `stock_in` / `stock_out` 的 `loadtest.py`——
`stock_in`/`stock_out` 卡在上面的仓库授权问题上；`query`（带 API Key 的
鉴权查询）本可以独立跑，但排查授权问题与设备失联的时间窗口重叠，未来得
及执行。

## 中止原因：设备在排查中途失联

时间线（均为设备本地时间/UTC 记录不明确，以 fleet 侧观测为准）：

1. 完成截图与两枚 API 密钥创建后，尝试 `fleet exec seeed-pi4 -- docker exec
   mcp_warehouse printenv ...` 排查仓库授权问题，命令超时转后台。
2. 后台命令报 `Error: authentication failed for recomputer@100.119.146.84:22`。
3. `fleet status seeed-pi4` 显示 `online: false`。
4. `ping 100.119.146.84` 仍通（3/3 包，RTT ~70ms，Tailscale 链路本身没断），
   但 SSH 端口不可用——判断是设备上的 SSH 服务/系统本身卡住或重启，不是
   网络链路问题。
5. 前台阻塞轮询 `fleet status seeed-pi4` 每 60 s 一次，连续 9 次
   （约 07:00:16 → 07:10:48，跨度 10.5 分钟）全部 `online: false`，未恢复。

**未能采集 OOM 证据**——`dmesg | grep -i oom` 与 `free -m` 都需要 SSH，
设备失联期间两者都拿不到。已知背景：这台 Pi 只有 2 GB 内存，部署完
仓库容器后 swap 已用 192 MB（部署前后一致，说明不是仓库容器把 swap 打
满的直接证据，但也不能排除失联瞬间发生了突增）；本轮触发失联前的操作
是一条普通的 `docker exec printenv`/`sqlite3` 查询命令，负载极轻，
**不像是本轮压测行为导致的 OOM**——但由于没有 dmesg 证据，这只是推测，
标"需核实"，不作为结论。

**处置建议：** 需要有物理/带外访问权限的人重启这台 Pi 4B，重启后先看
`dmesg -T | grep -iE 'oom|killed process'`、`journalctl -u ssh --since
"1 hour ago"` 确认失联根因，再决定是否需要把本方案在 2 GB 机型上的负载
上限下调（页面当前给的是 Raspberry Pi 5 上的容量规划数字，Pi 4B 2GB 未
系统测过任何一档并发）。

## 保留状态

- `mcp_warehouse` compose project 在设备失联前是健康运行的（部署时确认
  `/health` 200）；本轮未主动停止过，失联后无法确认当前状态，需要设备
  恢复后用 `docker ps` 复核。
- 遗留的 `bench-warehouse_warehouse_data` / `bench-warehouse_warehouse_providers`
  两个旧卷未清理，本轮未动它们。
- 管理员账号（用户提供的用户名/口令）与两枚操作员 API 密钥已在设备上
  持久化（若容器数据卷完好）。
