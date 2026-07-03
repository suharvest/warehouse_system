# 语音设备人脸识别对接 — 架构与实现

> 本文反映**已落地**的实现(替代早期的 push/pull + 改 server 设计)。面向接手的工程师/agent。
> 三库:仓库后端 `warehouse_system`(FastAPI + SQLAlchemy Core/Alembic)、语音 server `xiaozhi-esp32-server`、设备固件 `xiaozhi-esp32`(SenseCAP Watcher,esp32s3,Himax/ONNX NPU)。

## 0. 两个版本

| | 云端版(已实现) | 本地部署版(未来) |
|---|---|---|
| 鉴权强度 `verify_mode` | **session**(信任设备本地匹配,advisory) | interface(每次操作 warehouse 重比对 embedding,fail-closed) |
| 改不改 xiaozhi server | **不改**(纯透传/MCP 路由器) | 需要改(server 注入 embedding) |
| 人脸库下发 | warehouse **直推**设备 HTTP(绕开 server) | 同左 / 或受控 server 同步 |
| 适用 | 任意 server(含改不了的别人/用户自部署的) | 自己可控 server |

开关:`tenant_face_config.verify_mode`(`backend/metadata.py:387`,CheckConstraint `:392`)。默认 `interface`(保留 fail-closed 存量行为)。与推理拓扑 `mode`(local/lan)**正交**。

## 1. 三方角色与数据流(云端版)

```
[warehouse] 录入人脸/管理库      [xiaozhi server] 纯透传 + LLM 编排     [设备] 本地 NPU 识别
     │  ① 直推库(HTTP, 绕开 server)                                        │
     ├─────────────────────────────────────────────────────────────────►  本地人脸库(NVS)
     │                                                                      │ ② 对话时识别说话人
     │                          ③ self.conversation.speaker (MCP)  ◄────────┤
     │                          ④ LLM 把 speaker 填进 stock_in/out          │
     │  ⑤ verify-mcp(session: advisory + allow-list)  ◄────────────────────┘
```
server 在云端版**不持有任何人脸逻辑**,只路由 MCP 工具调用 + 跑 LLM。

## 2. 人脸库下发管线(核心,直推绕开 server)

1. **录入**:warehouse UI 上传图片 → 推理端点出 embedding(float32, 512B/128维)→ 存 `face_enrollments.embedding`。⚠️ **录入存的 `model_tag` 必须 = `we2-mfn128-v1`**,否则下发过滤为空。
2. **设备寻址**:`mcp_agent_devices` 子表(`backend/metadata.py:365`,迁移 `k0l1m2n3o4p5_add_mcp_agent_devices.py`)——智能体(mcp_connections)1:N 物理设备,每行存 `ip/port/face_enabled`(model_tag 列保留但写死、UI 不暴露)。前端在 `frontend/src/modules/features/mcp.js` 的设备二级区,「下发人脸」按钮仅 `face_enabled` 行显示。
3. **下发端点**:`POST /api/mcp/connections/{conn_id}/devices/{dev_id}/push-faces`(`backend/routers/mcp_admin.py`)——读设备 ip/port → 取本租户库(`build_face_library`,`backend/routers/face.py`,按 `DEVICE_FACE_MODEL_TAG="we2-mfn128-v1"` `mcp_admin.py:873` 过滤)→ **fp16 量化**(见 §5)→ POST 到 `http://<ip>:<port>/api/face/batch-update`。`httpx` 带 `trust_env=False`(否则系统代理拦 LAN IP 误报 502)。不可达/超时/4xx-5xx 一律 fail loud。
4. **设备接收**:`POST /api/face/batch-update`(`remote_display_http_server.cc`)——按 `embedding_format` 分派解码 → `FaceDatabase::ReplaceAll`(`face_database.cc:407`)。

## 3. 鉴权决定:本期无 token

- 设备 batch-update 端点**无鉴权**:同 LAN 信任 + 设备 IP 手填(opt-in,只为人脸)。
- 风险已知:同 LAN 任意主机可改/抹库(授权+审计+DoS)——可接受前提是可信内网/网络层隔离。
- **未来无感鉴权**:走「设备自注册悄悄带 token」——设备开机 POST 注册(device 自生成 token、随注册交给 warehouse、存 NVS),HMAC+ts+nonce 签 push 请求。用户零手输,且同时解决寻址。**不改现有 UX**,故现在不做也不堵后路。

## 4. 说话人身份链路(session)

- **设备**:`self.conversation.speaker` MCP 工具(`sscma_camera.cc` InitializeFaceMcpTools)返回 `{valid, name, subject_id, similarity}`。身份在对话上升沿冻结、**15s 新鲜度**(`face_recognition.cc` `kSpeakerMaxAgeUs`)、结束清除;`is_voice_busy` 边沿驱动(`sscma_camera.cc:597-628`)。
- **server(不改)**:LLM 看到设备 `self.*` 工具 + warehouse `stock_in/out`,**自行编排**:先调 `self.conversation.speaker` → 把 `subject_id`/`name` 填进写操作。引导写在**工具描述**里(随 schema 走,不可控 server 也到 LLM),speaker 参数对 LLM **可见**(从 exclude_args 移出,`mcp/warehouse_mcp.py`)。
- **warehouse 决策**:`/api/face/verify-mcp` → `verify_mcp_face`(`backend/face/orchestrator.py:287`)按 `verify_mode` 权威分叉——session(`:340`)用 speaker 解析 subject、记 advisory log、放行,**allow-list 仍强制**;interface(`:371`)无视 speaker、查 embedding、fail-closed。
- **安全**:`_enforce_face`(`mcp/warehouse_mcp.py`)改为**单一权威 gate**——永远把 embedding+speaker 全发后端、听其裁决,不按参数存在与否分叉 → 填假 `speaker_subject_id` **绕不过** interface 硬校验(删了旧的 `_face_advisory` 捷径)。

## 5. fp16 量化(canonical=float32,fp16 只在两个边界)

- **真相源**:warehouse DB(`face_enrollments.embedding`)+ 设备内存 `faces_` 始终 **float32**。
- **fp16 只出现在**:线缆(push 时量化)+ 设备 NVS blob。`fp16 = 256B = 128×IEEE-754 binary16 LE`。
- **线缆契约**:`{"model_tag", "embedding_format":"fp16", "faces":[{name,subject_id,embedding_b64}]}`。设备按 `embedding_format` 分派(fp16=256B / float32=512B 兼容 / 未知→409)。
- **warehouse 侧**:`quantize_embedding(f32_bytes, fmt)` dispatch + 常量 `DEVICE_EMBEDDING_FORMAT="fp16"`(`backend/routers/mcp_admin.py`);numpy `astype('<f2')`。
- **设备侧**:NVS 存 256B(`FACE_EMBEDDING_NVS_SIZE`,`face_database.h`),内存仍 float32;`Float32ToHalf`/`HalfToFloat32` 软件 IEEE-754 转换;`db_ver` 升 **3**(`face_database.cc:163`),旧 512B(db_ver<3)记录**作废当空库**(等重推,绝不误读)。
- **误差**:fp16 近乎无损(cosine 影响 <1e-3)。**int8 扩展点已留**:`quantize_embedding` 的 `"int8"` 分支占位(`NotImplementedError`),未来需 per-vector scale 且线缆 `embedding_format="int8"` 带 scale 字段。
- **空间**:20 张人脸 float32 ~11KB(16KB NVS 紧)→ fp16 ~5.5KB(宽裕,给 WiFi/设置留够)。

## 6. ReplaceAll / NVS 设计

- `FaceDatabase::ReplaceAll`(`face_database.cc:407`):`mutex_`(`face_database.h:81`)下整体替换——新代在局部构建/校验,末尾**一次** NVS commit 后才 swap `faces_`,`Match()` 只见旧或新、**无半更新窗口**。
- **崩溃安全(退化版,非双槽)**:16KB NVS 放不下两代(双槽峰值 ~24KB)且 NVS 非事务 → 单槽 + **`count` 先清零提交**:掉电中途只会留**空库**(服务端重推即恢复),绝不损坏/错认。
- **隔离**:FaceDatabase 用独立命名空间 `"face_db"`(`face_database.cc:8`),清空用**按 key 删**(`nvs_erase_key`),无 `nvs_erase_all` → 永不动 WiFi/设置等其它 NVS 数据。
- **model_tag 两侧写死一致**:warehouse `mcp_admin.py:873` ↔ 固件 `remote_display_http_server.cc:34` 都是 `"we2-mfn128-v1"`;不符整批 409。
- 写入用 `PauseInference()/ResumeInference()`(`sscma_camera.cc:1844-1886`)包裹,finally 保证 Resume。

## 7. 字段命名契约(三库统一)

| 用途 | 字段 |
|---|---|
| 会话说话人主键 / 显示名 | `speaker_subject_id` / `speaker_name`(设备工具返回键 `subject_id`/`name`) |
| 仓库 face_subjects 主键 | `subject_id` |
| embedding(base64) | `embedding_b64` |
| embedding 量化格式 | `embedding_format`(`float32`/`fp16`/未来 `int8`) |
| 模型标签(写死) | `model_tag` = `we2-mfn128-v1` |
| 鉴权强度 / 推理拓扑 | `verify_mode`(session/interface) / `mode`(local/lan)——**正交** |

## 8. 已完成 commit

**warehouse `feat/face-verify-mode`**:`d266b0c` verify_mode 列 · `043b464` 前端 select · `c7ae2b6` greeting raw-init 修复 · `507dfec` B回调(speaker 可见 + 权威 gate) · `91edc39` mcp_agent_devices 子表+CRUD · `6410968` 设备管理 UI · `85f1cca` push-faces · `00539b2` 下发按钮 · `7e0e36e` fp16 量化。

**固件 xiaozhi-esp32**:`19533f1` subject_id 入 NVS · `d983756` 说话人身份 + `self.conversation.speaker` · `fe53f3f` batch-update + fp16/db_ver=3。

## 9. 遗留 / 未来项

- **int8 量化**(+ 救回双槽原子):扩展点已留,需 per-vector scale + 线缆带 scale,且真机验匹配率。
- **无感鉴权**:设备自注册悄悄带 token(HMAC),解决"无 token"风险且不改 UX。
- **真机验证**:烧固件 → 录入(model_tag=we2-mfn128-v1)→ 填设备 IP/开人脸下发 → 下发 → 对话验 speaker;并验 20 张 + WiFi/设置共存不爆 NVS。
- **录入 model_tag 对齐**:务必 = `we2-mfn128-v1`,否则 push 过滤为空、下发 0 条。
