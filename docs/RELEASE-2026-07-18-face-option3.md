# 发布清单 — 2026-07-18 · 人脸校验 option 3 + UI

本次主题：**lan 模式人脸校验统一到后端拉图（option 3），并把人脸验证从 LLM/客户端彻底解耦**；附人脸配置页 UI 重排、**部署级 `FACE_ENABLED` 开关**（线上版可整体关闭人脸）、本机模式 20 张录入上限拦截。含 warehouse 云端镜像 + 设备端三样固件/模型的可核验记录。

> 更新 `2026-07-18` 下午：镜像刷新到 `2179abf`（新增 FACE_ENABLED / 20 张上限 / 文档重写），digest 见下；固件三样未变。

---

## 1. 云端镜像（warehouse all-in-one）

| 项 | 值 |
|---|---|
| 镜像 | `sensecraft-missionpack.seeed.cn/solution/warehouse` |
| Tags | `latest`、`2179abf`（不可变，推荐部署引用） |
| 构建 | `Dockerfile.prod`（glibc / python:3.12-slim，前端预构建）· buildx multi-arch |
| OCI index digest | `sha256:5f7d511e7b264b883f4fe71a2d9bec9a91d175d52d754de810a1f70d2a819134` |
| linux/amd64 | `sha256:8173c6cf3060035ca8c97b89a4786170b6bd0eebdc25fb779a1c5caa06d508d9` |
| linux/arm64 | `sha256:62d897b7e01f3de19a22730046aedd4e88212f4bc53b2c7d92e0789492438dd5` |
| 体积 | 约 450 MB（glibc base，arm64） |
| 冒烟 | 两态实测：默认 `/api/system/mode` 含 `face_enabled:true`、`/api/face/config` 401、`/health` 200；`FACE_ENABLED=false` 时 `face_enabled:false`、`/api/face/config` **404**（router guard 生效）。日志无 error/traceback/crash |

部署：`docker compose up -d`（compose 指向 `:latest`），或改用 `:2179abf`。**线上/云端版关闭人脸**：`FACE_ENABLED=false`（见 §5）。

> 上一版 `2e6a4e9`（OCI index `sha256:99b01a0c…`）为纯 option 3，无 FACE_ENABLED；已被 `2179abf` 取代。

> ⚠️ glibc base 是有意为之：we2-sim 的 `ai-edge-litert` 只有 manylinux wheel、无 musl，Alpine 会静默装空 venv 运行崩（见 memory `project_alpine_musl_wheel_silent_break`）。换基础镜像务必 `docker run` 打 `/health` 冒烟。

---

## 2. 本次提交

**warehouse_system** · 分支 `feat/face-verify-mode`（HEAD `2179abf`）
- `2179abf` feat(face): 本机模式录入前置拦截 20 张图片上限
- `902a07c` feat: 部署级 FACE_ENABLED 开关（线上版可整体关闭人脸识别）
- `f6d1a8e` docs(mcp): 按 option 3 重写人脸鉴权章节 + 透明 gate 教训
- `2e6a4e9` feat(face): lan 模式校验改后端拉图（option 3）+ 解耦 LLM 预验
- `45742ff` feat(face): 人脸配置卡片重排 + 启用开关改滑动开关 + 隐藏认证 Token
- `4135b6b` feat(face): 人脸配置「待下发」状态提示 + 隐藏认证 Token
- `584053c` feat(dashboard): 页面重新聚焦时立即刷新当前 tab

**xiaozhi-esp32** · 分支 `feat/face-identify-on-demand`（HEAD `8c3863d`）
- `8c3863d` feat(face): 新增 GET /api/face/capture 后端拉图端点（lan option 3）
- `14c05af` feat(face): 未识别人脸加 2s 宽限期，避免唤醒瞬间闪 "person detected"

---

## 3. 设备端固件 / 模型（当前烧录版本）

### ① 小智固件（ESP32 · Watcher 主控）
```
/Users/harvest/project/xiaozhi-esp32/build/xiaozhi.bin
sha256=605b5af3dc8e56042e6d71fabd0eac83fcff0018f5eef7711e8db76343fca560
size=3246752  mtime=2026-07-18 15:49   git=8c3863d @ feat/face-identify-on-demand
烧录口=/dev/cu.usbmodem5AF91658863
```
含：`/api/face/capture` 拉图端点、未识别 2s 宽限、conv_seq、pull_token 鉴权。

### ② 人脸模型（distill_v2_relu6 · 128D · model_tag `we2-mfnr6-128-v1`）
设备上跑的是编译进 Himax 固件的 Vela 版：
```
工程目录:
  /Users/harvest/project/grove_vision_2/sscma-example-we2/model_zoo/tflm_face_embedding/qat_distill_v2_relu6_128d/

Vela 编译版（打进 Himax 固件的就是它）:
  model_128d.int8_vela.tflite
  sha256=6e994e8d42882336d2ac3bb6e0aef99a40804c871a13d7ee44c5c84200af3618
  size=1044752  mtime=2026-07-16 22:30

非-Vela int8:  model_128d.int8.tflite
源:            model_128d.onnx / model_qat_relu6_best.pt
```
后端 we2-sim 仿真用的独立副本：
```
/Users/harvest/project/warehouse_system/backend/face/we2/models/mfn128_distill_v2_relu6.int8.tflite
sha256=708edf1775ab2ffa05b6681b5abcc0d81a546d1de5b6a9ec00df5f76eb37e0f3
size=1309024  mtime=2026-07-17 08:51
```

### ③ Himax WE2 固件（人脸检测 SCRFD + embedding 推理，模型内嵌）
```
工程:   /Users/harvest/project/grove_vision_2/sscma-example-we2/
烧录镜像:
  .../we2_image_gen_local/output_case1_sec_wlcsp/output.img
  sha256=31bf0e9754c1f08f649433763197e4e45c16066ba27736a2fda3053573c0689b
  size=602112  mtime=2026-07-17 19:26
应用镜像: .../output_case1_sec_wlcsp/cm55m_s_application.img
```
`output.img` 是最终刷进 WE2 的镜像，编译好的模型已打包在内。

> ⚠️ **别混用同名旧副本**：
> - `/Users/harvest/project/sscma-example-we2/`（另一份拷贝）
> - `/Users/harvest/project/_bin/Seeed_Grove_Vision_AI_Module_V2/`（参考预构建，内含**旧 ghostfacenet 模型**，非蒸馏版）
>
> 设备当前跑的是 `grove_vision_2/sscma-example-we2/` 这套（distill_v2_relu6）。

---

## 4. model_tag 一致性检查点

`we2-mfnr6-128-v1` 必须三处一致，否则 128D 向量不可比：
- 设备固件 `DEVICE_FACE_MODEL_TAG`（backend/routers/mcp_admin.py）
- 后端 we2-sim `MODEL_TAG`（backend/face/we2/simulator.py）
- 人脸库录入时写入的 model_tag

---

## 5. 部署级人脸开关 `FACE_ENABLED`

线上/云端版通常不支持人脸识别，只有本地/私有部署支持。用部署级 env 控制**整套功能的可用性**（与租户级 `tenant_face_config.enabled` 正交——后者只控某租户运行时是否刷脸）。

| 取值 | 行为 |
|---|---|
| 未设 / `true` / `1` / `yes`（默认） | 人脸功能全开，行为不变（本地/私有部署） |
| `false` / `0` / `no` / `off` | 关闭：① 前端隐藏「人脸识别」设置 tab 与面板；② `/api/face/*` 管理端点全 **404**（豁免 `verify-mcp` / `device/recognize` 两个运行时端点，否则 MCP fail-closed 会拦死出入库）；③ 出入库闸门 `verify_mcp_face` 首行返回 `skipped`，一律放行 |

设置方式（三选一）：docker-compose `environment:` / `.env` 里 `FACE_ENABLED=false` / `docker run -e FACE_ENABLED=false`。改后需**重启后端**生效。

实现锚点：`backend/database.py::get_face_enabled()`、`app.py /api/system/mode` + `_face_feature_gate` 路由守卫、`backend/face/orchestrator.py::verify_mcp_face()` 首行短路、前端 `state.js/tenants.js/main.js`。

## 6. 本机模式录入上限

本机(local)模式设备端人脸库上限 **20 条 embedding = 20 张图片（不是 20 个人）**，与固件 `FACE_MAX_COUNT=20` / 后端 `MAX_PUSH_FACES=20` 对齐。前端录入时前置拦截（`frontend/.../face-recognition.js`：`FACE_LOCAL_MAX_ENROLLMENTS`），超限明确报错并显示剩余额度；lan 模式在端点比对、不受此限。
