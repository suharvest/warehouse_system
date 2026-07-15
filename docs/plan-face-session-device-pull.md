# 方案：session 模式身份改为「后端直连设备拉取」（消除提示注入面）

状态：待评审（codex）。作者：主线程。日期：2026-07-15。

## 1. 背景与问题

当前 face 校验有两档 `verify_mode`：

- **interface**（fail-closed）：出入库时 embedding 由 runtime 注入、对 LLM 不可见
  （`exclude_args`），后端自己重比对。填参数绕不过去，**安全**。
- **session**（advisory→现已改为 session 级拦截）：后端信任 LLM 转发的
  `speaker_subject_id` / `speaker_name` 参数来确定身份。

**核心缺陷（已核实，非理论）**：`speaker_subject_id` / `speaker_name` **不在**
`stock_in` / `stock_out` 的 `exclude_args` 里（`mcp/warehouse_mcp.py:775, 817`），
是 LLM 可见可填的普通参数。后端 `verify_mcp_face` 的 session 分支
（`backend/face/orchestrator.py:345-375`）只把它们拿去 `face_subjects` 查一下是不是
活跃人员，**无任何机制验证该身份真的来自一次人脸识别**。因此一段提示注入若让
LLM 跳过 `self.conversation.speaker` / `self.face.identify` 直接填
`speaker_name="张三"`，后端会 `pass / session_verified`，人脸环节被完全绕过。

目前唯一的"防线"是 P1-2 探针观测到官方 LLM 当前不会这么干
（`e2e_voice_mcp/test_face_speaker_injection.py`）——是模型行为良好，不是架构保证。

## 2. 目标

session 模式下，后端的身份来源从「信 LLM 转发的参数」改为「后端直连设备 HTTP 拉取
设备本地的人脸识别结果」。LLM 不再经手身份 → 提示注入无法伪造。

非目标：不改 interface 模式；不改 xiaozhi-server（官方云端，无权限）；不改识别算法/
模型；不改 local/cloud(lan) 两种设备侧识别路径本身。

## 3. 关键约束确认（已核实）

- **不需要改 xiaozhi-server**。后端→设备是局域网直连 HTTP，走设备固件自带的
  `RemoteDisplayHttpServer`（开机即起、独立于 MQTT 对话通道）。`push-faces` 已经在用
  这条路（后端直接 POST 设备 IP:80）。官方服务器只路由 MCP 工具调用，增删工具参数/
  后端内部鉴权它都不感知。
- **设备 IP 复用现有配置**，零新增 UI。链路：
  `verify-mcp 的 API Key → mcp_connections.api_key（同一行）→ tenant/warehouse +
  connection_id → mcp_agent_devices.ip/port`（`push-faces` 用 `_load_device_or_404`
  读的就是这张表）。
- **当前部署单连接单设备**（conn `4dff5680` → 1 台设备 192.168.3.41），绑定无歧义。
  多设备场景的消歧见 §6。
- **设备侧对话中可现场推理**：`BenchSingleShotFaceEmbedding` 会现场 `AT+FACE=1` 点亮
  Himax 推一帧（冷路径 ~1.2s setup，热路径跳过）；lan 模式走 `Capture()`+云端。两者在
  对话状态下有 `sscma_mutex_` / `single_shot_pending_` / `FaceEmbedBlockReason` 保护，
  已被现有 `/api/face/embed` 端点验证。

## 4. 设计

### 4.1 固件（xiaozhi-esp32，我方 fork，自行 build/flash）

**(a) 抽公共分派方法** `SscmaCamera::IdentifyOnce()`：把现在写在 `self.face.identify`
工具 lambda 里的「读 NVS `id_mode` → local(`BenchSingleShot`+`Match`) 或
lan(`Capture`+`RemoteRecognize`)」分派逻辑抽成一个方法，返回
`FaceRecognition::SpeakerIdentity`。MCP 工具和新 HTTP 端点共用，保证两条触发路径
走同一套模式选择，不分叉。

**(b) 新增 HTTP 端点** `GET /api/face/current-speaker?fresh=0|1`
（挂在 `RemoteDisplayHttpServer`，复用 `HandleFaceEmbed` 的状态门/限流骨架）：
- `fresh=0`：直接返回 `FaceRecognition::GetCurrentSpeaker()`（内存冻结身份，零硬件动作）。
- `fresh=1`：走 `FaceEmbedBlockReason()` 门（OTA/greeting/busy → 409/503）后调
  `IdentifyOnce()`，返回 `{valid,name,subject_id,similarity,mode}`。
- **鉴权**：请求头带 `X-Face-Token`，与 NVS 里已下发的 `id_token` 比对（见 4.2）。
  缺失/不符 → 401。防止同局域网内其它主机冒充后端拉身份。

### 4.2 后端（warehouse_system）

**(a) 设备寻址** `_resolve_device_for_request(current_user, device_id=None)`：
API Key 认证后拿 `current_user` → 查 `mcp_connections`（按 tenant+warehouse，或按
透传的 connection_id）→ `mcp_agent_devices` 取 ip/port。复用 `push-faces` 的
`_validate_device_fields` 做 SSRF 校验。多设备时用透传的 `device_id` 消歧；单设备直接取。

**(b) session 分支改造**（`backend/face/orchestrator.py`）：
```
if cfg.verify_mode == "session":
    # 不再信任 LLM 传入的 speaker_* 参数
    dev = resolve_device(...)                      # 拿不到设备 → deny(device_unreachable)
    ident = http_get(dev, "/api/face/current-speaker?fresh=0",
                     token=cfg.auth_token or pushed_token, timeout=1.5)
    if not ident.valid:                            # 冻结身份无效 → 现场拍一次
        ident = http_get(dev, "?fresh=1", timeout=6)
    matched = resolve_subject(ident.subject_id or ident.name)   # 复用 _resolve_speaker_subject
    # allow-list、matched is None 判定逻辑不变
```
- 设备不可达 / 超时 / 401 → `deny`（fail-closed，绝不放行）。
- `speaker_subject_id` / `speaker_name` 入参**降级为可选快路径**：若后端信任模型可关掉；
  默认忽略（安全优先）。这是消除注入的必要条件——保留为权威即等于洞没堵。

**(c) 设备鉴权 token**：复用现有 `push-faces` 下发通道，把 `tenant_face_config.auth_token`
（或专用随机 token）作为 `id_token` 一并下发到设备 NVS（已在 batch-update payload 里），
后端拉取时带同一个 token。一处配置，双向共用。

### 4.3 MCP 层（warehouse_mcp.py）

最小改动/减法：`stock_in` / `stock_out` 的 `speaker_subject_id` / `speaker_name` 参数
可移入 `exclude_args` 或直接删除（后端不再依赖）。docstring 去掉"先调
conversation.speaker 填参数"的要求（改为后端自动拉取，对 LLM 透明）。

## 5. 时序与体验

- 快路径（`fresh=0` 命中冻结身份）：一次局域网 GET，几十 ms，用户无感。
- 慢路径（`fresh=1` 现场拍）：Himax 冷启 ~1.2s + 推理，或 lan 云端往返；session 出库
  端到端 +1~3s。lan 模式此时走 `Capture()` → **屏幕有照片预览**（符合"显示效果好些"）。
- MCP 工具跑在固件主循环（`app.Schedule`），与状态机串行、不打架；但长调用会阻塞主循环
  → speaking 时 TTS 可能顿一下（现有 `take_photo` 同款行为，>500ms 有 `main loop
  blocked!` 告警）。B 的后端直拉走的是设备 httpd worker 线程，与主循环并发，靠上述
  硬件锁保护——不额外阻塞对话主循环，比走 MCP 工具更平滑。

## 6. 残留风险与处置

| 风险 | 处置 |
|---|---|
| 局域网内冒充设备返回伪造身份 | 4.1(b) 的 `X-Face-Token` 双向鉴权（复用 id_token） |
| 一个连接挂多台设备，拉错设备 | 透传 MCP 调用的 `device_id` 消歧；当前单设备无此问题 |
| 设备离线/超时 | fail-closed → deny，不回退到信任 LLM |
| LLM 根本不调 stock_out、凭空口播成功 | B 不覆盖（属 xiaozhi-server LLM 行为）；但此时 DB 未动，安全；靠提示词缓解 |

## 7. 落地顺序

1. 固件：抽 `IdentifyOnce()` + `GET /current-speaker` + token 校验 → build/flash 冒烟。
2. 后端：设备寻址 + session 分支改造 + token 下发；单测覆盖
   （设备不可达→deny、fresh 回退、token 不符→deny、注入参数被忽略）。
3. MCP：删/隐藏 speaker 参数 + docstring。
4. 端到端验证：正常出库(fresh=0)、侧身唤醒(fresh=1 现场拍)、伪造 speaker 参数应被忽略、
   设备断网应 deny。
5. 保留 interface 模式不动，作为最高安全档。

## 8b. Codex 评审修正（2026-07-15，已核实）

**必须修正（已并入设计）：**

1. **超时架构（已定：方案 B — 同步拉取 + 提超时）**：MCP→后端固定 `timeout=5s`
   （warehouse_mcp.py:254），fresh=1 现场拍 ~6s 塞不进去。**决定**：把 face 相关工具的
   MCP→后端 POST 超时单独提到 **8s**（仅 `_face_guard` 那一处，不动其它调用），
   verify-mcp 里**同步**拉设备。取舍：出库时对话停顿 ~3s，可接受；不做会话 nonce，
   接受"操作时镜头里有已识别的人"作为身份证明（见 §8b#6 的降级说明）。备选（前置异步
   预取 + nonce）暂不做，留待后续若要把 session 拉到 interface 等级再上。

2. **设备寻址必须绑 connection（解法已确定）**：`CurrentUser` 无 api_key/connection_id
   （deps.py:123-137）。**干净解**：创建连接时 `mcp_connections.api_key` 存的是**明文**
   key（mcp_admin.py:253-289），而 verify-mcp 请求头 `X-API-Key` 也是明文 →
   `SELECT id FROM mcp_connections WHERE api_key = <header 明文>` 精确唯一匹配拿
   connection_id（每连接独立 key，1:1，不靠 tenant+warehouse 猜）。在 deps.py 的 api_key
   分支顺带查出 connection_id 挂到 CurrentUser。device 由 connection → mcp_agent_devices
   唯一确定；同连接多设备时用 MCP 透传的可信 `device_id` 消歧，**绝不接受 LLM 传的
   device_id**。（补：给 mcp_connections.api_key 加索引，避免每次 verify 全表扫。）

3. **提供 ID 失败必须硬 deny**：`_resolve_speaker_subject`（orchestrator.py:137）当
   subject_id 给了但（跨租户/停用）查不到时，**不得再按 name 回退**（会误映射到本租户
   同名者）。给 ID 就只认 ID，失败即 deny。

4. **speaker_* 直接删除**（非仅 exclude_args）：保留即注入面未消失。后端 session 分支
   **无条件忽略**入参身份，只认设备拉取结果。

5. **token 每设备独立 + nonce**：不复用租户级 `identify_token`（它还兼远端识别 Bearer，
   复用扩大泄露面）。为每台设备下发独立、可轮换的 `pull_token`；后端拉取请求带
   `nonce + 时间戳`，设备侧校验时窗，防明文 HTTP 重放。

6. **说话人证明缺口（方案 B 下的降级，已接受）**：fresh=1 只证明"镜头里有人脸"≠
   "说话人"。方案 B 不做会话 nonce，接受此降级——安全等级=「出库操作发生时，镜头前有一个
   已识别的授权人」。仍要做的最小加固：fresh=0 读冻结身份必须带**年龄检查**（超过 N 秒
   视为过期 → 转 fresh=1 现场拍），避免采用上一轮对话的残留身份。会话 nonce 绑定列为
   后续可选增强（把 session 拉到接近 interface），本轮不做。

**建议改进：** fresh 推理做 single-flight + 限流 + 短路熔断，避免路由持数据库上下文
等异步校验、重复超时放大不可用（face.py:626）。

## 8c. 固件时序审结论（2026-07-16，已实读固件）

**必须修正（并入固件实现）：**

F1. **统一操作级互斥 `identify_op_mutex_`**：`Capture()` 只是普通写 `capture_in_progress_`
   （非 CAS），且只分段持 `sscma_mutex_`，之后无锁操作共享队列 + `jpeg_data_`；
   `RemoteRecognize()` 也无锁读该 JPEG（sscma_camera.cc:1256/1261/1270/1482）。HTTP 与
   MCP 两条路并发调 identify（lan 分支）会互相抢帧/覆盖图像。**修**：新增一把覆盖
   「Capture()+RemoteRecognize()」整段 + 单拍的操作锁，MCP `self.face.identify` 和
   HTTP `/current-speaker?fresh=1` 都经它仲裁，串行化。抽 `IdentifyOnce()` 时把这把锁
   包在最外层。

F2. **门禁 TOCTOU**：现有 HTTP 先 `FaceEmbedBlockReason()` 再进单拍，但
   `BenchSingleShotFaceEmbedding` 自身只 CAS `single_shot_pending_`，不复查
   upgrading/pause/capture（remote_display_http_server.cc:162；sscma_camera.cc:818/869）。
   检查后到拿锁前状态会被 MCP/监控任务改。**修**：在 F1 的操作锁内**重新检查**
   block-reason，再决定是否推理。

**建议改进（并入）：**

F3. **fresh=1 忙即拒，不排队**：现有 embed 的 1s 限流时间戳在操作开始时更新，单次最长
   等 3s，排队请求会连续占死 Himax；HTTP 允许 3 socket + LAN 还含远程 HTTP → 体验型
   DoS（remote_display_http_server.cc:176/579；sscma_camera.cc:942）。**修**：`fresh=1`
   若操作锁已被占用或门禁不允许，立即返回 **503/429**（不阻塞排队）。→ **后端契约**：
   收 429/503/409/401/超时一律 **fail-closed deny**，不重试或最多一次短退避。

F4. **httpd worker 不驱动预览 UI**：`Capture()` 会直接更新预览 UI（sscma_camera.cc:1358），
   从 httpd worker 调用无线程安全证明（application.h:181）。**修**：后端拉取（httpd
   worker）走的 `IdentifyOnce()` 路径**禁用预览上屏**。→ **注意**：这意味着「云端识别有
   照片预览」只在 **LLM 主动调 `self.face.identify`（主循环线程）** 时成立，**后端直拉
   （B）不带预览**。若要 B 也有预览，需把该拍照调度回主循环，本轮不做。

**可接受（无需改）：** fresh=0 只读 `current_speaker_` 全程持 `state_mutex_`，线程安全，
15s 窗口也在锁内算（face_recognition.cc:370/398/408）；本地单拍无死锁链，监控任务 capture
时让行，对话期间停被动识别（sscma_camera.cc:517/611）；HTTP worker 单拍不碰音频接口，不
破坏 TTS（反倒是 MCP 工具经 Schedule 在主循环跑，长操作会阻塞主循环，有日志告警）。

## 8d. 实现中发现：pull_token 必须独立于 id_token（本机模式致命）

固件首版 current-speaker 用 NVS `face.id_token` 校验 `X-Face-Token`
（remote_display_http_server.cc:499/511）。但 `id_token` = `identify_token`
**只在 lan 模式下有值**（远端识别 Bearer）；**本机模式 identify_token 为空 →
`expected.empty()` → 端点永远 401**，B 直拉在本机模式下完全不可用。这也正是 codex #4
（复用远端 Bearer 扩大权限）的实证。

**修（后端 + 固件都要补一版）：**
- 引入**独立** `pull_token`（每设备生成、可轮换），**两种模式都下发**（不依赖是否配了
  远端）。走现有 batch-update 通道，新增 payload 字段 `pull_token` → 固件写 NVS
  新键 `face.pull_token`。
- 固件 current-speaker 改为校验 `face.pull_token`（不再用 id_token）。
- 后端拉取时 `X-Face-Token = pull_token`。
- 存储：`mcp_agent_devices` 加列 `pull_token`（或复用一处租户级但每设备覆盖）。
  三处同步（metadata + alembic 迁移 + raw init_database 兜底），全新库验证整链。

## 9. 最终端点契约（锁定）

`GET /api/face/current-speaker?fresh=0|1`，头 `X-Face-Token: <每设备 pull_token>`
（+ `X-Face-Nonce`、`X-Face-Ts` 备用，本轮 B 不强制校验 nonce）：
- `200 {valid,name,subject_id,similarity,mode,age_ms}` — 唯一的"可用身份"出口
- `401` token 不符 · `409` 状态冲突(upgrading/greeting) · `429/503` 忙 · 超时
- **后端映射**：非 200 或 `valid=false` → **deny**（fail-closed）。fresh=0 若
  `age_ms > N`（过期）→ 后端改请 fresh=1。

## 8. 原始待审问题（已由 §8b 回答）

- session 分支「先 fresh=0 再 fresh=1」的回退是否会与设备被动问候 / 对话中主循环放手
  Himax 的时序冲突？（`himax_face_warm_` 语义、`FaceEmbedBlockReason` 覆盖是否足够）
- 设备寻址用 tenant+warehouse 是否足够唯一，还是必须透传 device_id？API Key ↔ connection
  的映射在多连接同租户时是否会串。
- token 复用 id_token 是否有权限层面的隐患（下发通道 vs 拉取通道共用一个 secret）。
- 把 speaker 参数直接删除 vs 保留为 exclude_args 快路径：后者是否仍留注入面。
