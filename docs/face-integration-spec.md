# 语音设备人脸识别对接 — 实施 Spec

> 跨三库：设备固件 `xiaozhi-esp32` / 语音 server `xiaozhi-esp32-server` / 仓库后端 `warehouse_system`。
> 本 spec 已根据**固件实际改动**修订（说话人身份已下沉到 watcher 板级并改为 MCP 工具 pull 范式）。

## 0. 两个正交维度（勿混淆）

| 维度 | 字段 | 取值 | 含义 |
|---|---|---|---|
| 推理拓扑 | `tenant_face_config.mode` | `local` / `lan` | 本机推理 vs LAN 设备（如 WE2） |
| 鉴权强度 | `tenant_face_config.verify_mode`（**新增**） | `session` / `interface` | 软透传（信任设备本地匹配）vs 硬强制（warehouse 重比对 embedding，fail-closed） |

二者独立，互不影响。`verify_mode` 默认 **`interface`**（保留现有 fail-closed 行为，存量租户不被悄悄降级；如需开箱即用可改默认 `session`）。

## 1. 范式：PULL（已由固件确定）

设备**不**把 speaker_id 推进 `listen` 消息。说话人身份在板级 `FaceRecognition` 维护，暴露为 MCP 工具供查询：

- 工具：`self.conversation.speaker` → `{"valid":bool,"name":string,"similarity":float}`
  （`xiaozhi-esp32/main/boards/sensecap-watcher/sscma_camera.cc` `InitializeFaceMcpTools` 内）
- 身份在**对话上升沿冻结**、**15s 新鲜度**（`kSpeakerMaxAgeUs`）、**对话结束清除**
  （`sscma_camera.cc` 相机任务 `is_voice_busy` 边沿触发 `CaptureCurrentSpeaker/ClearCurrentSpeaker`；`face_recognition.cc:332+`）
- 旧的 `Application::CurrentSpeaker/SpeakerProvider` 通用框架**已删除**，不再引用。
- 设备保持**模式无关**：不需要知道 warehouse 的 verify_mode，也无设备侧 NVS 开关。

## 2. 端到端数据流

**Session 模式**：设备 NPU 本地匹配 → 工具 `self.conversation.speaker` 返回 `{valid,name,subject_id,similarity}` → server 在转发 face-gated 工具调用时主动 call 该工具，把身份作为隐藏参注入 → warehouse 信任并记录、放行（advisory）。

**Interface 模式**：server 走现有 `_inject_device_face`（`mcp_manager.py:134`）注入 `face_embedding_b64 + face_model_tag`（降级 `face_image_b64`）→ warehouse `/api/face/verify-mcp` 重比对 embedding，fail-closed → 落 `face_auth_logs`。

## 3. 设备固件（已大部分完成，剩余 1 项）

✅ 已完成：板级 SpeakerIdentity、15s 冻结、`self.conversation.speaker` 工具、状态栏视觉模式指示。

⬜ **待补（因契约决策）**：身份键用 `subject_id` 而非仅 name。
- `face_database` 入库时随 name 一起持久化 warehouse 下发的 `subject_id`（见 §4 库导出）。
- `FaceRecognition::SpeakerIdentity` 增加 `int subject_id`（无匹配时为 0/-1）。
- `self.conversation.speaker` 返回值增加 `"subject_id":int`。
- `DecodeName`/匹配结果结构体串联 subject_id（`face_recognition.cc:299/348`）。

## 4. Warehouse（可独立实施 + 验证，先做）

### 4.1 schema
`tenant_face_config` 增列（`backend/metadata.py:361-388`）：
```python
Column("verify_mode", String(16), nullable=False, server_default="interface"),
CheckConstraint("verify_mode IN ('session','interface')", name="ck_tenant_face_config_verify_mode"),
```
- **同步**改 `initial_schema.py` 的 create_table（全新 DB 不断链）。
- **幂等 migration**：照 `greeting_enabled` 写法（inspector `_column_exists` guard + `batch_alter_table().add_column()`），全新 SQLite `alembic upgrade head` 验整链。

### 4.2 库导出加身份键（契约决策：库导出加 subject_id）
`/api/face/library`（`backend/routers/face.py:348-388`）响应每项增加 `subject_id`（可选 `employee_id`）：
```
{name, subject_id, embedding_b64, model_tag}
```
设备 face DB 随 name 一起存 subject_id，`self.conversation.speaker` 原样回传 → warehouse 用 subject_id 精确定位，避免 name 歧义。

### 4.3 verify-mcp 分叉
`mcp/warehouse_mcp.py` `_enforce_face`（`200-274`）按 verify_mode 分叉：
- `interface`：维持现有 `_face_guard` 硬校验，fail-closed（网络/HTTP 失败 → deny，`warehouse_mcp.py:214`）。
- `session`：读隐藏参 `speaker_id`/`speaker_subject_id` → 解析 subject → 记 `face_auth_logs`（decision=pass, advisory）→ 放行。**绝不**调 `_face_guard`，**绝不**因默认继承走硬校验。
- `/api/face/verify-mcp`（`face.py` FaceVerifyMcpPayload ~530，Decision 响应 ~572）接受可选 `speaker_subject_id` + `speaker_name`。

### 4.4 config API + 前端
- `FaceConfigPayload`（`face.py:45`）、GET select（`face.py:109`）、PUT values（`face.py:137`）加 `verify_mode`。
- 前端 `frontend/src/modules/features/face-recognition.js:225/324` 加 select 控件（参考 `face-config-mode` ID 风格 `:244/327`）。

### 4.5 voice_sessions 表（可选）
pull 范式下身份随工具调用到达，**不再需要** `/api/face/session-identity` 推送端点。仅当需要追踪"整段对话是谁"时再建 `voice_sessions(id, tenant_id, device_id, session_id, subject_id, confidence, started_at, ended_at)`，唯一索引 `(tenant_id, device_id, session_id)`。MVP 可只靠 `face_auth_logs` 逐操作记录。

## 5. Server

- **Session 注入**：扩展 `_inject_device_face`（`mcp_manager.py:134/219/244`）或加同款 sibling：当 face-gated 工具被调用时，额外 call 设备 `self.conversation.speaker`，把 `subject_id`/`name`/`similarity` 作为隐藏参注入 warehouse 工具调用。与 embedding 注入并存，warehouse 按 verify_mode 取用 → server 也保持模式无关。
- **补漏**：Endpoint MCP executor（`mcp_endpoint_executor.py:35/38`）当前不走 `_inject_device_face`，interface 模式需在此加同等 face 注入。
- `receiveAudioHandle.py:49` 的 ASR voiceprint `speaker` 字段与本方案**无关**，不复用。

## 6. 字段命名契约（三库统一）

| 用途 | 字段名 |
|---|---|
| 会话说话人显示名 | `speaker_name`（设备工具返回键 `name`） |
| 会话说话人主键 | `speaker_subject_id`（设备工具返回键 `subject_id`） |
| 推理 embedding（base64） | `embedding_b64` |
| 推理模型标签 | `embedding_model_tag` / `model_tag` |
| MCP 隐藏 face 参数 | `face_embedding_b64`, `face_model_tag`, `face_image_b64` |

改任何字段名前做全链路检查：exports↔imports、HTML ID↔JS querySelector、后端硬编码字符串、测试 SQL（`tests/test_mcp.py`、`tests/test_face_wire_contract.py`、`tests/test_enum_wire_format.py`、`tests/contracts/mcp/`）。

## 7. 风险与坑

- **Alembic 链断**：加列必须同时改 `initial_schema.py` + 写幂等迁移 + 全新库验整链（见 memory `project_alembic_chain_orphan_migration`）。
- **Enum/wire-format**：`verify_mode` 值为字符串 `"session"/"interface"`，非整数；`tests/test_enum_wire_format.py` 断言字面值。
- **Fail-closed 边界**：interface 网络/HTTP 失败必须 deny；session 必须显式 advisory，不得回退硬校验。
- **name→subject 歧义**：已用库导出 subject_id 解决；务必保证设备入库与回传链路都带 subject_id。
- **固件部署成本**：仅 SenseCAP Watcher 有摄像头/NPU 能产出有效身份；其他板 `valid=false`，server/warehouse 必须容忍缺失。补 subject_id 需重烧设备。

## 8. 实施顺序

1. **Warehouse**（独立验证）：schema+migration → 库导出加 subject_id → config API/前端 → `_enforce_face` 分叉。验证：`uv run pytest tests/test_mcp.py tests/test_face_wire_contract.py tests/test_enum_wire_format.py` + 全新 SQLite `alembic upgrade head`。
2. **设备**：face DB 存 subject_id + 工具回传 subject_id。验证：串口日志确认工具返回含 subject_id。
3. **Server**：session 注入 + endpoint MCP 路径 face 注入。验证：mock warehouse + tool call 参数断言。
