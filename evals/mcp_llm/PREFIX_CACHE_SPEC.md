# edgellm Prefix Cache for MCP Tools — 架构层改造 Spec

## 背景

Orin NX 上 Qwen3-4B-AWQ 跑仓管 MCP（8 个工具，system prompt + tools ~5000 tokens），**每次请求从零 prefill 全部 5000 tokens**，TTFT = 12-31s。如果 system prompt + tools 的 KV cache 能跨请求复用，TTFT 可降到 ~1-2s。

openai-compat wrapper (`tools.py`) 已用 Jinja `chat_template` 渲染 tools-aware prompt，并通过 `formattedRequests` 路径传给 engine。但 engine 的 prefix cache 机制与 `formattedRequests` 路径不兼容——cache 走的是 messages 路径的 engine 内部模板，两者产生不同 token 序列，cache 永远 miss。

## 现状代码位置

| 组件 | 文件 | 关键行 |
|---|---|---|
| System prompt cache 预热 | `api_server.py` | `108-168` (`POST /v1/cache/system_prompt`) |
| 请求中 cache flag | `api_server.py` | `170-221` (`save_system_prompt_kv_cache`, `prefix_cache`) |
| formattedRequests 分支 | `api_server.py` | `210-221` (构造 `FormattedRequest`) |
| formatted_prefix 传递 | `api_server.py` | `218-221` (`formatted_request.formatted_system_prompt`) |
| C++ cache 匹配 | `llmInferenceSpecDecodeRuntime.cpp` | `1990` (`setUpForPrefillExecutionOneShot`) |
| wrapper 端渲染 | `tools.py`（openai-compat） | `555-613`（render + formatted_prefix） |

## 问题诊断

实测日志（`llmInferenceSpecDecodeRuntime.cpp:1990`）：

```
System-prompt KV cache token_ids mismatch (cached_len=1, live_input_len=5176,
shapeOk=1, matchIds=0) → falling back to fresh prefill
```

**根因**：`save_system_prompt_kv_cache=True` 被 `tools.py` 设置到请求体中，但 `api_server.py:203-204` 把这个 flag 挂到 `request.save_system_prompt_kv_cache`，engine 在 prefill 时从 **messages 路径**提取 system prompt 并缓存 KV。然而 `tools.py` 传给 engine 的 messages 是 stub：`[{"role":"user","content":""}]`（formattedRequests 只需要这个消息满足非空校验），所以缓存的 KV 只覆盖 **1 个 token**。下一次请求的 `formatted_complete_request` 有 5176 tokens（Jinja 渲染的），前 1 个 token 匹配后全盘 miss。

**深层矛盾**：`formattedRequests` 路径的 system prompt 缓存需要基于 `formatted_system_prompt` 字段（`api_server.py:221`），而不是 messages 提取的 system prompt。

## 改造方案（建议在 api_server.py 层面）

### 改动点 1：让 `save_system_prompt_kv_cache` 对 `formattedRequests` 路径生效

**位置**：`api_server.py:203-221`

当前逻辑：
```python
request.save_system_prompt_kv_cache = save_system_prompt_kv_cache  # 全局 flag
# ...
if prefix_cache:
    formatted_request = rt.FormattedRequest()
    formatted_request.formatted_system_prompt = formatted_prefix  # 已传给 C++
    formatted_request.formatted_complete_request = formatted_complete
    request.formatted_requests = [formatted_request]
```

C++ 侧 `request.save_system_prompt_kv_cache` 会让 prefill 阶段保存整个 `formatted_complete_request` 的 KV，而非仅 system prompt 部分。

**改为**：当 `formatted_prefix` 非空且 `save_system_prompt_kv_cache=True` 时，额外调用 `llm_instance.save_system_prompt_kv_cache(formatted_prefix)` —— 这与 `/v1/cache/system_prompt` 端点的行为一致（`api_server.py:144`），但发生在 chat completions 请求的上下文中，且缓存的 key 是 `formatted_prefix` 的 token 序列，正好对应 `formatted_complete_request` 的前缀。

```python
if prefix_cache and formatted_prefix:
    formatted_request.formatted_system_prompt = formatted_prefix
    formatted_request.formatted_complete_request = formatted_complete
    request.formatted_requests = [formatted_request]
    # 新增：基于 formatted_prefix 存储 system prompt KV cache
    if save_system_prompt_kv_cache:
        llm_instance.save_system_prompt_kv_cache(formatted_prefix, lora_weights_name)
```

### 改动点 2：C++ 侧 cache 匹配逻辑确认

**位置**：`llmInferenceSpecDecodeRuntime.cpp:1990`

确认 `setUpForPrefillExecutionOneShot` 在 `formattedRequests` 路径下，会用 `formatted_system_prompt` 作为 cache lookup key。当前日志 `cached_len=1` 说明 C++ 尝试了 lookup 但缓存内容不对（1 token 来自 stub messages）。改动点 1 让缓存内容变成正确的 token 序列后，应该自然匹配。

**需要 C++ 侧确认**：当 `request.formatted_requests` 非空时，`save_system_prompt_kv_cache` 是否基于 `formatted_system_prompt` 存储，还是仍然基于 messages 路径。如果是后者，则 C++ 也需要微调。

### 改动点 3（openai-compat wrapper）：传正确的 formatted_prefix

**位置**：`tools.py:602-612`

当前传 `formatted_prefix = " "`。改为 Jinja 渲染的 system message + tools 前缀（`add_generation_prompt=False`）：

```python
_msgs = body.get("messages", []) or []
_sys = [m for m in _msgs if m.get("role") == "system"]
_pfx = " "
if _sys:
    try:
        _pfx = render_chat_with_tools(
            _sys, tools,
            tool_choice=tool_choice,
            engine_dir=self.engine_dir,
            model_name=self.model_name,
            add_generation_prompt=False,
        )
    except Exception:
        pass
new_body["formatted_prefix"] = _pfx
new_body["save_system_prompt_kv_cache"] = True
```

**注意**：`render_chat_with_tools` 当前硬编码 `add_generation_prompt=True`，需改为接受参数。

## 预期效果

| 指标 | 当前 | 改造后 |
|---|---|---|
| 首请求 TTFT | 14-17s | 14-17s（无变化，首次需要 prefill） |
| 同 system+tools 后续请求 TTFT | 14-17s | **1-2s**（仅 prefill 增量 user message） |
| pass^1 | 68.2% | 不变（TTFT 影响延迟不改变准确率） |
| MCP 兼容性 | — | ✅ 与 tools 来源无关，只看 system+tools 是否相同 |

## 风险与边界

1. **不同 tools 集合**：如果 MCP server 注入的工具集变化（增删工具），`formatted_prefix` 会变，cache 自动 miss → fallback 到 full prefill（安全退化）
2. **多租户**：不同租户可能有不同 system prompt，cache 按 token 序列匹配，不会混淆
3. **显存**：KV cache 复用不额外消耗显存（只是标记已计算的部分不重新算）

## 改动量估算

| 文件 | 改动行数 |
|---|---|
| `api_server.py` | +3 行（改动点 1） |
| `tools.py` | +15 行（改动点 3，含 `add_generation_prompt` 参数化） |
| `llmInferenceSpecDecodeRuntime.cpp` | 待确认，大概率 0 行（如果 C++ 已支持 formatted system prompt cache） |
