# warehouse MCP 语音端到端回归（e2e_voice_mcp）

回归 warehouse 的 MCP 功能，**不需要真机、不需要麦克风**。用 TTS 合成语音喂进
一个 headless 的"虚拟小智设备"，走**官方小智全链路**验证 warehouse 工具被正确调用。

```
合成语音(WAV) → py-xiaozhi(headless,脚手架) 上行 → 官方 tenclass ASR
  → 官方云端 LLM(function_call) → 官方 wss MCP 接入点
  → warehouse mcp_pipe.py(外拨) → warehouse_mcp.py → warehouse 后端 API
```

这条链路覆盖了 `mcp/mcp_pipe.py` 的 WS 外拨生产链路——本地 server 模拟测不到的盲区。

> **py-xiaozhi 只是脚手架**（驱动虚拟设备的外部运行时依赖），作为 **git submodule**
> 挂在 `vendor/py-xiaozhi`，版本随 warehouse 一起锁定。用它自己的 venv 运行测试
> （含 opuslib 等客户端依赖）；`PY_XIAOZHI_ROOT` 可覆盖成别处的 checkout。

## 依赖组件与前置条件

| 组件 | 作用 | 启动 |
|---|---|---|
| `vendor/py-xiaozhi`（submodule） | 虚拟设备脚手架，须**已激活**到官方 agent | 见下"一次性准备" |
| warehouse 后端 :2124 | MCP 工具落地的 API | `./run_backend.sh` |
| mcp_pipe 外拨 | 把 warehouse 工具经 wss 注册给官方 agent | `./run_bridge.sh` |
| `.env.local` | 接入点 wss + API key（gitignored） | 手动维护 |

- py-xiaozhi 设备须已激活，且其所属官方 agent 与 `.env.local` 里 wss 接入点是**同一个 agent**。
  激活状态在用户数据目录（`~/Library/Application Support/py-xiaozhi/`），与源码位置无关。
- 首次激活见 py-xiaozhi 的 GUI 流程（`cd vendor/py-xiaozhi && python main.py`，控制台输验证码）。

## 一次性准备

**初始化脚手架 submodule 并建它的 venv：**
```bash
# 在 warehouse 仓库根
git submodule update --init e2e_voice_mcp/vendor/py-xiaozhi
cd e2e_voice_mcp/vendor/py-xiaozhi && uv sync && cd -
```
（`git clone` warehouse 时加 `--recurse-submodules` 可一步到位。submodule 的 `.venv`
与 warehouse 的 `.venv` 互相隔离，不会污染。）

**配置**

`.env.local`（gitignored，chmod 600）：
```bash
export MCP_ENDPOINT="wss://api.xiaozhi.me/mcp/?token=…"   # 官方控制台该 agent 的 MCP 接入点
export WAREHOUSE_API_KEY="whmcp-…"                        # warehouse 后端 X-API-Key
export PORT=2124
export PY_XIAOZHI_ROOT="/Users/harvest/project/py-xiaozhi"
```

> **接入点会轮换。** wss 里的 token 有有效期；失效后测试会连不上工具（`test_voice_mcp_e2e`
> 断言失败或 LLM fallback 到设备工具）。去 xiaozhi.me 控制台 → 该 agent → MCP 接入点
> 重新复制 `wss://…`，更新 `.env.local` 的 `MCP_ENDPOINT`，重启 `./run_bridge.sh`。

## 跑测试

```bash
# 终端1：后端
./run_backend.sh
# 终端2：mcp 外拨桥
./run_bridge.sh
# 终端3：跑测试（必须用 py-xiaozhi 的 venv）
source .env.local
"$PY_XIAOZHI_ROOT/.venv/bin/python" -m pytest e2e_voice_mcp/ -c e2e_voice_mcp/pytest.ini
```

（从 warehouse 仓库根执行终端3；路径按需调整。）

## 测试清单

| 文件 | 覆盖 | 依赖官方 | 依赖后端+桥 |
|---|---|---|---|
| `test_smoke_official.py` | headless 连接 + 设备 MCP 握手 | ✅ | ✗ |
| `test_voice_official.py` | 语音注入 → 官方 ASR → LLM 应答 | ✅ | ✗ |
| `test_voice_mcp_e2e.py` | **全链路**：语音 → 官方 → warehouse 工具真实执行 | ✅ | ✅ |

`test_voice_mcp_e2e` 的硬断言 = warehouse **后端日志**在本轮新增 MCP 工具驱动的 API 命中
（fuzzy-match / product-stats 等），而非依赖 LLM 自然语言（可能幻觉）。后端+桥未起时该用例 **skip**。

## 关键实现点（踩坑）

- **必须 MANUAL 模式 + 显式 `send_stop_listening`** 才触发官方 ASR 断句；AUTO_STOP 的
  服务器端 VAD 对合成静音帧不稳定，收不到 stt。见 `voice_inject.py`。
- 上行门控：`send_audio` 需 `is_audio_channel_opened()`（连接后即 True）。
- 音频参数固定 16kHz/mono/20ms（320 样本/帧），Opus 编码复用 py-xiaozhi 的 `OpusCodec`。
- 语料生成（macOS）：`say -v Tingting "…" -o x.wav --data-format=LEI16@16000 --file-format=WAVE`。
- 工具调用发生在云端，**设备侧看不到 tools/call**——故用后端日志作硬证据。
