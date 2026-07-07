# 以仓库管理为例开发 MCP Server

本文面向需要把现有业务系统接入 MCP 的同事。仓库管理系统只是参考样例：同一套模式也适用于农业数据平台、设备运维平台、CRM、ERP 等，只要目标系统有 API 或可查询的数据源，就可以包装成 MCP tool 给语音助手或智能体调用。

重点结论：

- MCP tool 层负责定义“用户能做什么”，例如查库存、出库、查询区域传感器。
- Provider 层负责把 tool 参数翻译成具体系统 API，不建议在 MCP tool 里直接写某个业务系统的 HTTP 细节。
- fuzzy、normalize、fallback、候选澄清、响应包装这些逻辑尽量复用当前仓库项目里的经验，不要每接一个系统就重新发明一套。

## 当前项目的可复用结构

仓库系统已有一个较清晰的分层：

```text
用户语音/LLM
  -> MCP tool: mcp/warehouse_mcp.py
  -> Provider 接口: mcp/providers/base.py
  -> 具体 Provider: mcp/providers/default.py 或 mcp/providers/custom/*.py
  -> 业务后端 API / 外部平台 API
```

相关文件：

| 文件 | 作用 |
| --- | --- |
| `mcp/warehouse_mcp.py` | MCP server 入口，定义 tool、加载配置、加载 Provider、包装返回给 LLM 的响应 |
| `mcp/providers/base.py` | Provider 抽象接口，约束每个业务系统必须实现哪些方法 |
| `mcp/providers/default.py` | 自有仓库后端 Provider，包含大量可复用的 normalize、fallback、响应裁剪经验 |
| `mcp/providers/__init__.py` | Provider 自动发现和加载，新增 provider 文件后通常不需要手动注册 |
| `backend/fuzzy_match.py` | 仓库系统内部的模糊匹配实现，包含中文拼音、token、SKU+名称、租户/仓库隔离 |
| `docs/WMS_Provider_Development.md` | 只对接 WMS Provider 时的详细开发指南 |

## 新接入一个数据源时，要先判断改哪一层

### 情况一：仍然是仓库语义

例如接入第三方 WMS、ERP 库存模块、供应商仓储 API，用户仍然问：

- “查一下 M3 螺丝库存”
- “给七彩灯红色出库 3 个”
- “把这个批次挪到 A 区 2 架”

这种情况优先只新增 Provider：

```text
mcp/providers/acme_wms.py
```

不用改 `warehouse_mcp.py` 的 tool 定义。实现 `BaseProvider` 里的方法，把外部 WMS API 响应转换成当前 MCP 期望的统一 dict 格式即可。

### 情况二：业务语义已经变了

例如接入农业数据平台，用户要问：

- “查一下东区温室今天的温湿度”
- “3 号地块最近 6 小时土壤水分有没有异常”
- “把 A 区传感器数据汇总一下”

这不再是仓库的 `query_stock` / `stock_in` / `stock_out` 语义，应新增 MCP tool 和对应 Provider 方法。例如：

```text
mcp/agriculture_mcp.py                 # 独立 MCP server，适合完整新领域
mcp/providers/agriculture_platform.py  # 外部农业平台适配器
```

或者在现有 `warehouse_mcp.py` 中少量增加农业查询 tool，但只建议临时 PoC 这么做。长期维护更推荐独立 server，避免仓库 tool 变成大杂烩。

## 可以直接复用的逻辑

### 1. Provider 自动发现和配置加载

`mcp/providers/__init__.py` 已经支持扫描 `mcp/providers/` 和 `mcp/providers/custom/` 下的 Provider 类。新增文件时只要：

```python
from .base import BaseProvider


class AgricultureProvider(BaseProvider):
    PROVIDER_NAME = "agriculture"
```

然后配置：

```yaml
provider: "agriculture"
api_base_url: "https://agri.example.com/openapi/v1"
auth:
  type: bearer
  token: "xxx"
timeout: 15
```

基类 `BaseProvider` 里已经提供：

- `get_auth_headers()`：支持 `api_key`、`bearer`、`basic`
- `http_get()` / `http_post()`：统一拼接 `base_url`、注入认证头、处理 HTTP 错误
- `timeout`：区分连接超时和读取超时

如果外部平台是 HMAC、AK/SK、OAuth 刷新 token，就 override `get_auth_headers()` 或 `http_get()`。

### 2. normalize 逻辑

仓库里已有几类归一化经验：

- `backend/fuzzy_match.py::_normalize()`：去空格、横杠、斜杠、括号、逗号等干扰字符，并统一小写。
- `backend/fuzzy_match.py::_tokenize()`：给中英文/数字边界补空格，适合“银色M3螺丝”这种口语输入。
- `mcp/providers/default.py::_normalize_reason_category()`：把 LLM 传来的中文、英文、口语别名映射到后端枚举。
- `mcp/providers/default.py::_normalize_batch_no()`：把语音中的批次号、空格、中文横杠、纯数字批次归一成标准批次号。

农业数据平台可照这个思路建立领域归一化：

```python
_REGION_ALIAS = {
    "东区": "east",
    "东侧": "east",
    "一号棚": "greenhouse-1",
    "1号棚": "greenhouse-1",
}

_METRIC_ALIAS = {
    "温度": "temperature",
    "气温": "temperature",
    "湿度": "humidity",
    "空气湿度": "humidity",
    "土壤水分": "soil_moisture",
    "土壤湿度": "soil_moisture",
}


def normalize_region(value: str) -> str:
    key = str(value or "").strip().lower()
    return _REGION_ALIAS.get(key, key)


def normalize_metric(value: str) -> str:
    key = str(value or "").strip().lower()
    return _METRIC_ALIAS.get(key, key)
```

原则是：MCP tool 对 LLM 暴露自然语言友好的参数，Provider 在调用外部 API 前把它们转换成平台的标准 code。

### 3. fuzzy 匹配逻辑

当前 `backend/fuzzy_match.py` 的关键能力：

- 文本相似度：`rapidfuzz.fuzz.ratio`、`partial_ratio`
- 中文拼音相似度：`pypinyin.lazy_pinyin`
- token 顺序无关匹配：`token_set_ratio`
- SKU/编号和名称组合加权：避免只靠编号误命中
- `confident` 判断：不仅看最高分，还看第一名和第二名分差
- `tenant_id` / `warehouse_id` 过滤：避免跨租户或跨仓库泄露
- `resolve_location_in_scope()`：在特定物料+仓库范围内做库位模糊，而不是全局模糊

农业平台可以复用这个设计，但要换成自己的实体：

| 仓库实体 | 农业平台类比 |
| --- | --- |
| material | sensor、plot、greenhouse、device |
| location | region、field、zone |
| SKU | sensor_id、device_sn、station_code |
| warehouse_id | farm_id、project_id、site_id |
| variant | metric、crop_type、sensor_type |

例如“东区棚温度传感器”和“东侧一号棚温湿度站”容易被 ASR 识别得不稳定，应该先 fuzzy 解析区域或设备，再调用精确 API。

建议实现一个通用小工具，而不是把农业实体塞进仓库的 `FuzzyMatcher`：

```python
import re

from rapidfuzz import fuzz


def normalize_text(text: str) -> str:
    return re.sub(r"[\s\-－/／()（）,，、]+", "", str(text or "")).lower()


def resolve_candidate(query: str, candidates: list[dict]) -> dict:
    norm_query = normalize_text(query)
    scored = []
    for item in candidates:
        name = item["name"]
        score = fuzz.token_set_ratio(norm_query, normalize_text(name))
        if score >= 50:
            scored.append({**item, "score": round(score, 1)})

    scored.sort(key=lambda x: x["score"], reverse=True)
    if not scored:
        return {"best_match": None, "confident": False, "candidates": []}

    best = scored[0]
    if len(scored) == 1:
        confident = best["score"] >= 75
    else:
        confident = best["score"] >= 85 and best["score"] - scored[1]["score"] > 10

    return {"best_match": best, "confident": confident, "candidates": scored[:5]}
```

如果新系统接入的是本仓库后端，可以直接调用 `/api/fuzzy-match`。如果接入外部农业平台，通常由 Provider 拉取区域/设备候选后在本地 fuzzy，或者调用农业平台自己的搜索接口。

### 4. fallback 逻辑

仓库系统里有几种值得复用的 fallback 模式：

- `query_stock()`：先精确查，失败后 fuzzy 查；fuzzy 可信再查精确实体，不可信则返回候选。
- `query_batch()`：用户给了像批次号的文本就查批次；如果不像批次而像产品名，就 fallback 到库存查询。
- `stock_out()`：指定批次库存不足时不静默 FIFO，而是返回结构化失败，并让用户确认是否允许 partial fallback。
- `active-for-mcp`：读取外部 Provider 失败时回退默认 Provider，但会记录 warning。

农业平台可对应成：

- 先精确查 `region_id` / `sensor_id`，失败后 fuzzy 查区域或设备。
- 用户说“东区温度”时，如果没有指定传感器，fallback 到该区域默认温度指标或聚合值。
- 用户说“今天”但平台 API 需要时间范围，Provider 归一成 `[today 00:00, now]`。
- 查询实时数据失败时，可以 fallback 到最近一次有效读数，但必须在 `message` 或 `data.stale=true` 中明确说明，不能假装是实时值。

失败时不要直接返回一句“失败”。应该返回可恢复的信息：

```python
{
    "success": False,
    "error": "region_ambiguous",
    "candidates": [
        {"name": "东区一号棚", "id": "gh-east-1", "score": 92},
        {"name": "东区二号棚", "id": "gh-east-2", "score": 88},
    ],
    "message": "东区有多个候选，请指定一号棚还是二号棚",
}
```

### 5. 反幻觉响应包装

`warehouse_mcp.py::_wrap_response()` 是很重要的防线。它把 Provider 原始响应压缩成 MCP 对外稳定 schema：

```python
{
    "success": true,
    "facts": {"executed": false},
    "say": "七彩灯当前库存10个。",
    "say_kind": "tell",
    "data": {...}
}
```

关键规则：

- 写操作成功时 `facts.executed=true`，查询类永远是 `false`。
- `say` 里放已经确认过的最终口播文本，数字不要让 LLM 自己算。
- 失败时 `say_kind=fail`，需要用户选择时 `say_kind=ask`。
- 候选项、确认 patch、截断信息都放结构化字段。

农业数据查询虽然多数是只读，但也应该复用这个思路：

```python
{
    "success": True,
    "facts": {"executed": False},
    "say": "东区一号棚当前温度26.4摄氏度，空气湿度71%，数据时间为14点05分。",
    "say_kind": "tell",
    "data": {
        "region": "东区一号棚",
        "temperature": 26.4,
        "humidity": 71,
        "timestamp": "2026-07-07T14:05:00+08:00",
        "stale": False
    }
}
```

如果是控制类 tool，例如“打开灌溉阀门”，必须像仓库出入库一样设置 `executed`，并且失败时明确“没有执行”。

## 农业数据平台示例

目标：用户可以问“查一下东区一号棚最近 1 小时温湿度”。

### 1. 定义 tool

如果做独立农业 MCP server，可以新建 `mcp/agriculture_mcp.py`：

```python
from fastmcp import FastMCP
from providers import load_provider

mcp = FastMCP("Agriculture Data MCP")
provider = load_provider(config)


@mcp.tool()
def query_sensor_data(
    region: str,
    metric: str = "all",
    time_range: str = "latest",
) -> dict:
    """查询指定区域的农业传感器数据。

    参数：
    - region: 区域、地块、温室名称，例如“东区一号棚”
    - metric: 指标，例如 temperature/humidity/soil_moisture/all
    - time_range: latest、1h、6h、today
    """
    resp = provider.query_sensor_data(region, metric, time_range)
    return wrap_agri_response("query_sensor_data", resp)
```

### 2. 实现 Provider

```python
class AgricultureProvider(BaseProvider):
    PROVIDER_NAME = "agriculture"

    def __init__(self, config: dict):
        super().__init__(config)
        self.project_id = config.get("project_id")

    def list_regions(self) -> list[dict]:
        data = self.http_get("/regions", params={"project_id": self.project_id})
        return data.get("items", [])

    def query_sensor_data(self, region: str, metric: str = "all", time_range: str = "latest") -> dict:
        regions = self.list_regions()
        resolved = resolve_candidate(region, regions)
        if not resolved["confident"]:
            return {
                "success": False,
                "error": "region_ambiguous" if resolved["candidates"] else "region_not_found",
                "candidates": resolved["candidates"],
                "message": "没有找到明确区域，请换一个说法或指定区域编号",
            }

        region_obj = resolved["best_match"]
        metric_code = normalize_metric(metric)
        start, end = normalize_time_range(time_range)

        data = self.http_get("/sensor/readings", params={
            "region_id": region_obj["id"],
            "metric": metric_code,
            "start": start,
            "end": end,
        })
        if data.get("success") is False or "error" in data:
            return {
                "success": False,
                "error": data.get("error", "query_failed"),
                "message": f"查询传感器数据失败：{data.get('message') or data.get('error')}",
            }

        return {
            "success": True,
            "region": region_obj["name"],
            "metric": metric_code,
            "readings": data.get("readings", []),
            "latest": data.get("latest"),
            "message": f"查询成功：{region_obj['name']} {metric_code} 数据已返回",
        }
```

### 3. 包装响应

不要把外部平台原始 JSON 全量丢给 LLM。应该整理成稳定字段和短口播：

```python
def wrap_agri_response(operation: str, resp: dict) -> dict:
    success = bool(resp.get("success"))
    if operation == "query_sensor_data" and success:
        latest = resp.get("latest") or {}
        region = resp.get("region", "")
        ts = latest.get("timestamp", "")
        temp = latest.get("temperature")
        humidity = latest.get("humidity")
        parts = []
        if temp is not None:
            parts.append(f"温度{temp}摄氏度")
        if humidity is not None:
            parts.append(f"湿度{humidity}%")
        say = f"{region}当前" + "，".join(parts) + f"，数据时间{ts}。"
        return {
            "success": True,
            "facts": {"executed": False},
            "say": say,
            "say_kind": "tell",
            "data": {
                "region": region,
                "latest": latest,
                "count": len(resp.get("readings") or []),
            },
        }

    candidates = resp.get("candidates") or []
    if candidates:
        names = "、".join(c["name"] for c in candidates[:3])
        return {
            "success": False,
            "facts": {"executed": False},
            "say": f"我不确定你说的是哪个区域，候选有：{names}。请告诉我具体区域。",
            "say_kind": "ask",
            "data": {"candidates": candidates[:3]},
        }

    return {
        "success": False,
        "facts": {"executed": False},
        "say": str(resp.get("message") or "查询失败。"),
        "say_kind": "fail",
        "data": {"error": resp.get("error")},
    }
```

## 开发检查清单

新增 MCP server 或新 Provider 时，按这个顺序做：

1. 明确用户自然语言意图：查询、写入、控制、统计分别是什么。
2. 设计 tool 参数：参数要贴近用户表达，但字段必须可被 Provider 归一化。
3. 决定复用现有 Provider 接口，还是新增领域接口。
4. 实现 auth：优先用 `BaseProvider` 的 `auth` 配置，不够再 override。
5. 实现 normalize：区域、指标、时间范围、枚举、编号都要先归一化。
6. 实现 fuzzy：先 scoped fuzzy，再全局 fuzzy；不 confident 就返回候选，不要硬选。
7. 实现 fallback：只做可解释、可追溯的 fallback，写操作不能静默改变用户意图。
8. 包装响应：提供 `success`、`say`、`say_kind`、`facts.executed`、`data`。
9. 控制响应大小：列表结果设置 `max_results`，必要时像 `DefaultProvider.search()` 一样按字节裁剪。
10. 写测试：至少覆盖精确命中、模糊命中、歧义、未命中、外部 API 错误、权限失败。

## 哪些不要复用

- 不要把农业实体硬塞进仓库的 `material/contact/operator`，除非只是临时 demo。
- 不要让 LLM 直接看外部 API 原始大 JSON，字段多会增加幻觉和响应超限风险。
- 不要在 MCP tool 里写大量业务系统细节；tool 应该稳定，变化放在 Provider。
- 不要在 fuzzy 不确定时自动取第一名，尤其是写操作或控制设备。
- 不要在外部平台请求失败时编造“最近正常值”；如果使用缓存或最近值，必须显式标记 stale。

## Session 级鉴权如何结合人脸识别

有些 MCP 场景不只是“能不能访问 API”，还要判断“当前这句话是不是授权的人说的”。例如：

- 仓库：出库、入库、移动批次。
- 农业：打开灌溉阀门、修改告警阈值、确认喷药任务。
- 运维：重启设备、切换生产配置。

这类场景建议把普通 API key / token 作为“系统到系统”的鉴权，把人脸识别作为“当前会话说话人”的二次鉴权。

### 推荐链路

当前仓库系统已经落地了这条链路，可直接参考：

```text
设备本地识别人脸/说话人
  -> MCP tool 调用前拿到 session speaker
  -> MCP tool 把 speaker_subject_id / speaker_name 转发给后端
  -> 后端 /api/face/verify-mcp 做权威裁决
  -> 通过才执行写操作；失败则返回结构化失败，业务操作不落库
```

仓库里的对应实现：

| 位置 | 作用 |
| --- | --- |
| `mcp/warehouse_mcp.py::_enforce_face()` | MCP 写操作前统一调用人脸 gate |
| `backend/routers/face.py:/api/face/verify-mcp` | MCP 专用人脸校验桥接端点 |
| `backend/face/orchestrator.py::verify_mcp_face()` | 根据租户配置、操作规则、allow-list 做最终裁决 |
| `tenant_face_config.verify_mode` | 决定鉴权强度：`session` 或 `interface` |
| `tenant_face_operation_rules` | 配置哪些操作需要人脸、哪些 subject 被允许 |

### 两种模式：session 与 interface

`verify_mode` 不等于人脸推理部署方式，它只表示鉴权强度：

| 模式 | 行为 | 适用场景 |
| --- | --- | --- |
| `session` | 信任设备在当前会话里识别出的说话人，只用 `speaker_subject_id` / `speaker_name` 做授权判断和审计 | 云端 server 不能改、只能让 LLM 编排设备 tool 的场景 |
| `interface` | 忽略 speaker 参数，使用当前图片或 embedding 在仓库后端重新比对，失败即拒绝 | 更高安全要求、server/runtime 能注入 `image_b64` 或 `embedding_b64` 的场景 |

关键安全点：

- MCP server 不应该自己决定“这个人是否允许操作”，只负责转发身份材料。
- 后端是唯一权威，必须在 `/api/face/verify-mcp` 里统一判断。
- 即使 LLM 或客户端伪造 `speaker_subject_id`，在 `interface` 模式下也会被后端忽略。
- `session` 模式下也要保留 allow-list：如果规则限定只有某些人能操作，未识别或不在名单内都必须拒绝。
- 人脸校验失败时写操作不能执行，响应里要明确“没有执行”。

### MCP tool 参数设计

以仓库 `stock_out` 为例，写操作保留两类身份字段：

```python
def stock_out(
    product_name: str,
    quantity: int,
    reason_category: str,
    face_image_b64: str = None,
    face_embedding_b64: str = None,
    face_model_tag: str = None,
    speaker_subject_id: int = None,
    speaker_name: str = None,
) -> dict:
    blocked = _enforce_face(
        "stock_out",
        image_b64=face_image_b64,
        embedding_b64=face_embedding_b64,
        embedding_model_tag=face_model_tag,
        speaker_subject_id=speaker_subject_id,
        speaker_name=speaker_name,
    )
    if blocked is not None:
        return blocked

    return provider.stock_out(...)
```

设计约定：

- `speaker_subject_id` / `speaker_name`：session 模式使用，来自设备当前会话识别结果。
- `face_image_b64` / `face_embedding_b64` / `face_model_tag`：interface 模式使用，通常由 runtime 或服务端注入，不暴露给普通用户填写。
- tool 描述里要明确：session 模式先调用设备的 `self.conversation.speaker`，把返回的 `subject_id` / `name` 填进写操作。
- 写操作入口第一步就是 `_enforce_face()`，不要先调用外部业务 API 再校验。

### 农业平台如何套用

假设新增一个控制灌溉阀门的 tool：

```python
@mcp.tool(meta={"requires_face": True})
def open_irrigation_valve(
    region: str,
    duration_minutes: int,
    speaker_subject_id: int = None,
    speaker_name: str = None,
    face_embedding_b64: str = None,
    face_model_tag: str = None,
) -> dict:
    blocked = enforce_face(
        operation="open_irrigation_valve",
        speaker_subject_id=speaker_subject_id,
        speaker_name=speaker_name,
        embedding_b64=face_embedding_b64,
        embedding_model_tag=face_model_tag,
    )
    if blocked:
        return {
            "success": False,
            "facts": {"executed": False},
            "say": "人脸校验未通过，本次没有打开灌溉阀门。",
            "say_kind": "fail",
            "data": blocked,
        }

    resp = provider.open_irrigation_valve(region, duration_minutes)
    return wrap_agri_response("open_irrigation_valve", resp)
```

对应后端规则可以按 operation 配置：

```text
operation = open_irrigation_valve
require_face = true
allowed_subject_ids = [12, 18]
warehouse_id / site_id / farm_id = 当前作用域
```

农业平台没有“仓库”的概念时，也要保留同等作用域字段，例如 `farm_id`、`site_id`、`greenhouse_id`。不要只按全局 subject 授权，否则 A 农场的人可能误操作 B 农场设备。

### 响应和审计要求

人脸鉴权必须和反幻觉响应契约配合：

```python
{
    "success": False,
    "facts": {"executed": False},
    "say": "人脸校验未通过，本次没有执行操作。",
    "say_kind": "fail",
    "data": {
        "error": "face_auth_denied:not_in_allow_list",
        "operation": "open_irrigation_valve"
    }
}
```

后端还应记录审计日志：

- `tenant_id` / `farm_id` / `warehouse_id`
- `operation`
- `user_id` 或 API key 归属用户
- `matched_subject_id`
- `decision`: `pass` / `deny` / `skipped`
- `failure_reason`
- `request_id`
- 时间戳

### 不要做的事

- 不要只靠 LLM 口头判断“我认识这个人”，必须有后端校验结果。
- 不要把 `speaker_name` 当唯一身份，优先使用稳定的 `speaker_subject_id`。
- 不要在写操作失败后让 LLM 自己“再试一次”，应返回 `say_kind=fail` 或明确等待用户确认。
- 不要在 `session` 模式下绕过 allow-list。
- 不要把人脸校验写进每个 Provider；Provider 只负责业务系统适配，鉴权 gate 应该在 MCP tool 或统一 wrapper 层。

## 推荐落地方式

仓库类外部系统：

```text
新增 mcp/providers/<vendor>_wms.py
复用 mcp/warehouse_mcp.py
复用 BaseProvider 接口和 _wrap_response
```

农业数据平台这类新领域：

```text
新增 mcp/agriculture_mcp.py
新增 mcp/providers/agriculture_platform.py
复制并收敛 warehouse_mcp.py 里的响应包装模式
按农业领域实现 normalize / fuzzy / fallback
```

如果只是先做一个小范围 PoC，可以在现有 MCP server 里加 1 到 2 个只读 tool；一旦出现控制类操作、多个农业实体、多个外部 API，就应该拆成独立 MCP server。
