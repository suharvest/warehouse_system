#!/usr/bin/env python3
"""
仓库管理系统 MCP 服务器

支持可插拔 WMS 后端，通过 config.yml 的 provider 字段切换。
默认使用自有后端（DefaultProvider），也可对接第三方 WMS。

提供通用的库存管理功能：
- 名称模糊解析（resolve_name）
- 库存查询（query_stock，内建模糊匹配）
- 入库/出库操作（stock_in / stock_out，内建模糊匹配）
- 统一搜索（search，支持物料/联系方/操作员）
- 当天统计（get_today_statistics）

注意：本服务通过 Provider 调用后端 API 实现所有操作，不直接操作数据库。
"""

from fastmcp import FastMCP
import asyncio
import sys
import os
import logging
import yaml
import functools
import json
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime

# 调试模式：MCP_DEBUG=1 时打印每个 tool 的入参和返回值到 stderr（由 mcp_pipe.py 转发到终端）
_MCP_DEBUG = os.environ.get('MCP_DEBUG') == '1'

if _MCP_DEBUG:
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s [MCP] %(levelname)s %(message)s',
        stream=sys.stderr,
    )
logger = logging.getLogger('WarehouseMCP')

# MCP Tool 调用日志装饰器（仅 MCP_DEBUG=1 时生效）
def log_mcp_call(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        def invoke():
            if not _debug_enabled():
                return func(*args, **kwargs)
            logger.info(f"→ {func.__name__}({json.dumps(kwargs, ensure_ascii=False, default=str)})")
            try:
                result = func(*args, **kwargs)
                result_str = json.dumps(result, ensure_ascii=False, default=str)
                logger.info(f"← {func.__name__} => {result_str[:3000]}")
                return result
            except Exception as e:
                logger.error(f"✗ {func.__name__} => {e}", exc_info=True)
                raise

        # FastMCP 2.13 calls synchronous tools directly on its event loop.
        # Providers call this same Uvicorn service over HTTP, so direct execution
        # deadlocks the event loop until requests times out. asyncio.to_thread
        # also copies ContextVars, preserving per-session tenant credentials.
        return await asyncio.to_thread(invoke)
    return wrapper

# 修复 Windows 控制台 UTF-8 编码
if sys.platform == 'win32':
    sys.stderr.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')


# 加载配置文件
def load_config():
    """从 config.yml 加载配置，支持环境变量覆盖"""
    config_path = os.path.join(os.path.dirname(__file__), 'config.yml')
    port = os.environ.get('PORT', '2124')
    config = {
        'api_base_url': f'http://localhost:{port}/api',
        'api_key': '',
        'max_results': 10,  # MCP 搜索结果上限（云端帧 ~13 KB，30 条容易撞 1009）
    }

    # 尝试读取配置文件
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                file_config = yaml.safe_load(f) or {}
                config.update(file_config)
        except Exception as e:
            logger.warning(f"读取配置文件失败: {e}，使用默认配置")

    # 环境变量优先级更高
    if os.environ.get("WAREHOUSE_API_URL"):
        config['api_base_url'] = os.environ.get("WAREHOUSE_API_URL")
    if os.environ.get("WAREHOUSE_API_KEY"):
        config['api_key'] = os.environ.get("WAREHOUSE_API_KEY")
    if os.environ.get("WAREHOUSE_PROVIDER"):
        config['provider'] = os.environ.get("WAREHOUSE_PROVIDER")

    # 兼容旧版顶层 api_key 字段：归一化为 auth.api_key 结构，
    # 让 _face_guard / BaseProvider.get_auth_headers 等统一逻辑都能拿到。
    if config.get('api_key') and not config.get('auth'):
        config['auth'] = {
            'type': 'api_key',
            'key': config['api_key'],
            'header': 'X-API-Key',
        }

    return config


_config = load_config()

# 确保能找到 providers 包（直接运行 warehouse_mcp.py 时需要）
sys.path.insert(0, os.path.dirname(__file__))
from providers import load_provider  # noqa: E402


def _load_provider_from_db_or_default(default_config: dict):
    """通过后端 API 读取系统模式 / 激活 Provider，按租户隔离加载。

    旧版直接打开 sqlite 用 `SELECT * FROM erp_providers WHERE is_active = 1 LIMIT 1`，
    在多租户部署里会拿到其他租户的 Provider。改为调用
    GET /api/erp/providers/active-for-mcp，由后端按 X-API-Key 推导出的 tenant_id
    做 build_scope_filter 隔离，从根上消除跨租户泄露点。

    任何异常（网络错误、4xx/5xx、文件缺失等）均回退到默认 Provider。
    """
    import requests as _requests

    api_base = (default_config.get('api_base_url') or '').rstrip('/')
    if not api_base:
        logger.warning("未配置 api_base_url，使用默认 Provider")
        return load_provider(default_config)

    headers = {}
    auth = default_config.get('auth') or {}
    if auth.get('type') == 'api_key':
        key = auth.get('key', '')
        if key:
            headers[auth.get('header', 'X-API-Key')] = key
    elif auth.get('type') == 'bearer':
        headers['Authorization'] = f"Bearer {auth.get('token', '')}"

    try:
        resp = _requests.get(
            f"{api_base}/erp/providers/active-for-mcp",
            headers=headers,
            timeout=(5, default_config.get('timeout', 10)),
        )
    except Exception as e:
        logger.warning(f"调用 active-for-mcp 失败: {e}，回退到默认 Provider")
        return load_provider(default_config)

    if resp.status_code == 404:
        logger.warning("系统模式为 external_erp 但当前租户没有激活的 Provider，回退到默认 Provider")
        return load_provider(default_config)

    if resp.status_code >= 400:
        logger.warning(
            f"active-for-mcp 返回 {resp.status_code}: {resp.text[:200]}，回退到默认 Provider"
        )
        return load_provider(default_config)

    try:
        payload = resp.json()
    except Exception as e:
        logger.warning(f"解析 active-for-mcp 响应失败: {e}，回退到默认 Provider")
        return load_provider(default_config)

    mode = payload.get('mode', 'self_owned')
    if mode != 'external_erp':
        return load_provider(default_config)

    provider_info = payload.get('provider') or {}
    provider_name = provider_info.get('provider_name')
    filename = provider_info.get('filename')
    stored_config = provider_info.get('config') or {}

    if not provider_name or not filename:
        logger.warning("active-for-mcp 响应缺少 provider 信息，回退到默认 Provider")
        return load_provider(default_config)

    merged_config = {**default_config, **stored_config}
    merged_config['provider'] = provider_name

    custom_dir = os.path.join(os.path.dirname(__file__), 'providers', 'custom')
    filepath = os.path.join(custom_dir, filename)

    if not os.path.exists(filepath):
        logger.warning(f"激活的 Provider 文件不存在: {filepath}，回退到默认 Provider")
        return load_provider(default_config)

    try:
        from providers.test_runner import load_provider_from_file
        logger.info(f"使用外部 ERP Provider: {provider_name} ({filename})")
        return load_provider_from_file(filepath, merged_config)
    except Exception as e:
        logger.warning(f"动态加载 Provider 文件失败: {e}，回退到默认 Provider")
        return load_provider(default_config)


_provider = None
_provider_lock = threading.Lock()
_runtime_state: ContextVar[dict | None] = ContextVar(
    'warehouse_mcp_runtime_state', default=None
)


def create_runtime_state(
    api_base_url: str,
    api_key: str,
    *,
    debug: bool = False,
    provider: str | None = None,
) -> dict:
    """Create isolated configuration and provider cache for one MCP session."""
    config = deepcopy(_config)
    config['api_base_url'] = api_base_url.rstrip('/')
    config['api_key'] = api_key
    config['auth'] = {
        'type': 'api_key',
        'key': api_key,
        'header': 'X-API-Key',
    }
    if provider:
        config['provider'] = provider
    return {
        'config': config,
        'provider': None,
        'provider_lock': threading.Lock(),
        'debug': bool(debug),
    }


@contextmanager
def runtime_context(state: dict):
    """Bind one tenant/session runtime to the current async task context."""
    token = _runtime_state.set(state)
    try:
        yield
    finally:
        _runtime_state.reset(token)


def _get_config() -> dict:
    state = _runtime_state.get()
    return state['config'] if state is not None else _config


def _debug_enabled() -> bool:
    state = _runtime_state.get()
    return bool(state['debug']) if state is not None else _MCP_DEBUG


def _get_provider():
    """Load the tenant provider on first tool use, not during MCP startup.

    The watcher expects an initialize response within roughly 30 seconds. A
    production restart starts every configured MCP connection together, and
    eagerly calling the backend API here made all child processes contend
    before they could read the initialize request. Keeping startup side-effect
    free lets the protocol handshake complete immediately.
    """
    state = _runtime_state.get()
    if state is not None:
        if state['provider'] is None:
            with state['provider_lock']:
                if state['provider'] is None:
                    state['provider'] = _load_provider_from_db_or_default(
                        state['config']
                    )
        return state['provider']

    global _provider
    if _provider is None:
        with _provider_lock:
            if _provider is None:
                _provider = _load_provider_from_db_or_default(_config)
    return _provider


# ============ Face Guard (Phase 1) ============
# 仅对 MCP tool 调用生效。通过后端 /api/face/verify-mcp 桥接到
# backend.face.orchestrator.verify_mcp_face；后端用 X-API-Key 识别
# 当前用户、租户与仓库上下文。
def _face_guard(
    operation: str,
    warehouse_id: int = None,
    image_b64: str = None,
    embedding_b64: str = None,
    embedding_model_tag: str = None,
) -> dict:
    """Verify face for an MCP write operation. Returns the decision dict.

    Behavior:
    - status='pass'    -> caller proceeds
    - status='skipped' -> caller proceeds (feature disabled or rule not required)
    - status='deny'    -> caller MUST surface an error to the LLM and abort

    Failure handling (fail-closed):
    - api_base unset -> skipped (face module not deployed in this MCP host)
    - HTTP 4xx/5xx   -> deny (server reachable but rejected; never silently bypass)
    - transport error (network, timeout) -> deny (treat as if face check failed)

    ``image_b64`` is the caller-captured face frame (e.g. xiaozhi vision
    snapshot). The unified face_rec_api backend is stateless so the
    image must be supplied here; when None we still call the backend so
    that disabled-feature / rule-not-required tenants short-circuit to
    'skipped' instead of being blocked on missing camera input.
    """
    import requests as _r
    config = _get_config()
    api_base = config.get('api_base_url', '').rstrip('/')
    if not api_base:
        return {"status": "skipped", "failure_reason": "no_api_base"}
    headers = {}
    auth = config.get('auth') or {}
    if auth.get('type') == 'api_key':
        headers[auth.get('header', 'X-API-Key')] = auth.get('key', '')
    elif auth.get('type') == 'bearer':
        headers['Authorization'] = f"Bearer {auth.get('token', '')}"
    body = {"operation": operation, "warehouse_id": warehouse_id}
    if image_b64:
        body["image_b64"] = image_b64
    if embedding_b64:
        body["embedding_b64"] = embedding_b64
    if embedding_model_tag:
        body["embedding_model_tag"] = embedding_model_tag
    # NOTE: speaker_subject_id / speaker_name are intentionally NOT forwarded.
    # Under B (session mode = backend-direct device pull) the backend derives the
    # identity itself from the physical device and IGNORES any LLM-forwarded
    # identity; interface mode re-matches the embedding. Forwarding LLM-supplied
    # identity would only re-open the prompt-injection surface we closed. The two
    # params remain in the tool signatures as deprecated no-ops for wire compat.
    try:
        # 18s：后端可能同步直连设备拉取身份/拉图。local fresh=1 现拍 ~6s；lan option 3
        # 拉一张 JPEG(~8s，含切 sensor mode 3 + 抓帧 + 编码) 之后还要接一次端点 /infer(≤10s)。
        # 必须给足预算，否则慢路径被过早判 transport_error 而 fail-closed 误杀。
        resp = _r.post(f"{api_base}/face/verify-mcp", json=body, headers=headers, timeout=18)
        if resp.status_code >= 400:
            logger.warning("face verify returned %s: %s", resp.status_code, resp.text[:200])
            return {"status": "deny", "failure_reason": f"http_{resp.status_code}"}
        return resp.json()
    except Exception as e:
        logger.warning("face verify transport error: %s", e)
        return {"status": "deny", "failure_reason": "transport_error"}


def _enforce_face(
    operation: str,
    warehouse_id: int = None,
    image_b64: str = None,
    embedding_b64: str = None,
    embedding_model_tag: str = None,
) -> dict | None:
    """Run the face gate; return a tool-error dict to surface, or None to proceed.

    Single authoritative gate: forward EVERYTHING we have (server-injected
    embedding AND LLM/device-supplied speaker identity) to ``/face/verify-mcp``
    and honour whatever the backend decides. The backend is the sole authority
    on ``verify_mode``:

    * **session**: backend trusts the device-resolved speaker identity but
      still enforces it — an unresolved speaker (or one outside the rule's
      allow-list) gets ``deny``; only a resolved, active subject passes.
    * **interface** (default): backend ignores the speaker params and re-matches
      the embedding, fail-closed.

    Crucially we do NOT branch on which params are present — so a spoofed
    ``speaker_subject_id`` from the (now LLM-visible) tool arg cannot short-
    circuit the interface-mode hard check. ``deny`` (incl. HTTP/transport
    errors → fail-closed) aborts; ``pass``/``skipped`` proceed.
    """
    decision = _face_guard(
        operation, warehouse_id,
        image_b64=image_b64,
        embedding_b64=embedding_b64,
        embedding_model_tag=embedding_model_tag,
    )
    if decision.get("status") == "deny":
        reason = decision.get("failure_reason") or "denied"
        # 后端(B)直连设备取身份，LLM 无需也不应自己调 speaker/填参数。设备没认到人时
        # 引导用户面向摄像头重试即可（而非让 LLM 补调工具）。
        if reason in ("device_no_identity", "speaker_unresolved"):
            return {
                "success": False,
                "error": f"face_auth_denied:{reason}",
                "message": (
                    "没有识别到已登记的操作人。请面向摄像头后再说一次本次操作；"
                    "若仍失败，请联系管理员确认人脸是否已录入。"
                ),
            }
        if reason == "device_unresolved":
            return {
                "success": False,
                "error": f"face_auth_denied:{reason}",
                "message": "无法连接到人脸识别设备，出入库已阻止。请联系管理员检查设备在线状态。",
            }
        return {
            "success": False,
            "error": f"face_auth_denied:{reason}",
            "message": f"人脸校验未通过：{reason}。请由本人操作或联系管理员检查权限规则。",
        }
    return None


# ============================================================================
# 反幻觉响应契约（最小闸门版）
# ============================================================================
# 设计目标：堵住 LLM 凭空编造执行结果的幻觉。
# 真实事故：用户语音"出库批次003共3个"，后端因该批次仅2个返回失败，
# LLM 仍口播"已出库3个，库存4个"，实际数据库零扣减。
#
# 闸门策略（两个硬字段）：
#   1. facts.executed  : 布尔。是否真的改了数据库。
#                        写工具（stock_in/out）成功才为 true；查询类永远 false。
#                        失败时一定为 false，LLM 据此可判断"没扣"。
#   2. speak/speak_ask/speak_failed : 三选一非空。LLM 必须照搬原文不许改写。
#                        所有数字都已嵌入文本，LLM 无机会自己算。
#
# Prompt 硬规则（注入到每个 tool docstring 的 _RULES_FOOTER）告诉 LLM 怎么用。
# ============================================================================

_RULES_FOOTER = """\
反幻觉硬规则（七条）：
1. 数字只能来自响应字段，不许口算或推测。
2. say 必须照搬原文，不许改写/合并/增减数字。回复=say。禁止在前面加"好的/Okay/让我看看"，禁止在后面加"大概/左右/估计"。
3. executed=false 时禁止说"已/完成/成功/出库了/入库了"。
4. say_kind=ask → 念候选、等用户选；say_kind=fail → 仅播报，禁止重试。
5. awaiting_confirm 非空 → 先问用户，用户同意后才用 patch 重发；拒绝则结束。
6. 用户问"现在/还剩/最新"必须重新调工具，不许引用历史结果。
7. 不需要计算/闲聊/天气/数学时只回一句"我只负责仓库管理"（15字内），禁止展开解释。
"""

# 写操作集合 — 只有这类在 success=true 时才被认为 executed=true
_WRITE_OPS = {"stock_in", "stock_out", "move_batch_location"}


def _wrap_response(operation: str, resp: dict) -> dict:
    """把 provider 响应压缩为 MCP 对外稳定 schema。"""
    if not isinstance(resp, dict):
        return resp

    success = bool(resp.get("success"))
    executed = success and (operation in _WRITE_OPS)
    say = None
    say_kind = "tell" if success else "fail"
    data = {}
    awaiting_confirm = None

    def _candidates(limit=3):
        return [
            {"name": c.get("name", ""),
             "sku": (c.get("extra") or {}).get("sku", "") or c.get("sku", ""),
             "score": c.get("score"),
             "stock": c.get("stock") or (c.get("extra") or {}).get("stock")}
            for c in (resp.get("candidates") or [])[:limit]
        ]

    def _copy(keys):
        return {k: resp[k] for k in keys if k in resp}

    def _fail_text(default):
        msg = resp.get("message") or resp.get("error") or default
        return str(msg)

    if operation == "stock_out":
        if success:
            p = resp.get("product") or {}
            bcs = resp.get("batch_consumptions") or []
            unit = p.get("unit") or "个"
            details = "、".join(
                f"批次{b.get('batch_no')}出{b.get('quantity')}{unit}" for b in bcs
            )
            tail = f"（{details}）" if details else ""
            say = (
                f"已出库{p.get('name', '')}共{p.get('out_quantity', '?')}{unit}{tail}，"
                f"当前库存{p.get('new_quantity', '?')}{unit}。"
            )
            data = {
                "name": p.get("name"),
                "out_qty": p.get("out_quantity"),
                "after": p.get("new_quantity"),
                "unit": unit,
                "batches": [
                    {"no": b.get("batch_no"), "qty": b.get("quantity")}
                    for b in bcs[:3]
                ],
            }
        else:
            err = resp.get("error") or ""
            msg = resp.get("message") or "出库失败"
            data = _copy([
                "batch_no_requested", "batch_available", "shortfall",
                "can_fallback", "fallback_total_available",
            ])
            candidates = _candidates()
            if candidates:
                data["candidates"] = candidates
            if err in ("ambiguous_name", "location_ambiguous"):
                parts = []
                for c in candidates:
                    n = c["name"]
                    sku = c.get("sku", "")
                    stock = c.get("stock")
                    if sku:
                        n += f"（{sku}）"
                    if stock is not None:
                        n += f"库存{stock}"
                    parts.append(n)
                names = "、".join(parts)
                say = f"我不确定你说的是哪一个，候选有：{names}。请告诉我具体是哪个。"
                say_kind = "ask"
            elif err == "batch_insufficient_stock":
                bn = resp.get("batch_no_requested") or "该批次"
                avail = resp.get("batch_available")
                short = resp.get("shortfall")
                can = resp.get("can_fallback")
                other = resp.get("fallback_total_available")
                if can:
                    say = (
                        f"批次{bn}只有{avail}个，缺{short}个；其他批次合计{other}个可补。"
                        f"要不要先扣完{bn}的{avail}个，再从其他批次补{short}个？请说是或否。"
                    )
                    say_kind = "ask"
                    awaiting_confirm = {"patch": {"allow_partial_fallback": True}}
                else:
                    say = (
                        f"本次没有扣任何库存。批次{bn}只有{avail}个，"
                        f"其他批次合计{other}个也不够补{short}个，无法完成出库。"
                    )
            else:
                say = f"本次没有扣任何库存。{msg}"

    elif operation == "stock_in":
        if success:
            p = resp.get("product") or {}
            b = resp.get("batch") or {}
            unit = p.get("unit") or "个"
            bn = b.get("batch_no") or "-"
            say = (
                f"已入库{p.get('name', '')}{p.get('in_quantity', '?')}{unit}，"
                f"批次号{bn}，当前库存{p.get('new_quantity', '?')}{unit}。"
            )
            data = {
                "name": p.get("name"),
                "in_qty": p.get("in_quantity"),
                "after": p.get("new_quantity"),
                "unit": unit,
                "batch_no": bn,
            }
        else:
            candidates = _candidates()
            if candidates:
                data["candidates"] = candidates
                parts = []
                for c in candidates:
                    n = c["name"]
                    sku, stock = c.get("sku", ""), c.get("stock")
                    if sku: n += f"（{sku}）"
                    if stock is not None: n += f"库存{stock}"
                    parts.append(n)
                say = f"我不确定你说的是哪一个，候选有：{'、'.join(parts)}。请告诉我具体是哪个。"
                say_kind = "ask"
            else:
                say = f"本次没有入库。{_fail_text('入库失败')}"

    elif operation == "query_stock":
        if success:
            p = resp.get("product") or {}
            b = resp.get("batch") or {}
            if b and not p:
                unit = b.get("unit") or "个"
                loc = b.get("location") or "未指定库位"
                say = (
                    f"批次{b.get('batch_no', '')}是{b.get('material_name', '')}，"
                    f"当前余量{b.get('quantity', '?')}{unit}，位于{loc}。"
                )
                data = {
                    "batch_no": b.get("batch_no"),
                    "name": b.get("material_name"),
                    "qty": b.get("quantity"),
                    "unit": unit,
                    "location": loc,
                }
            else:
                unit = p.get("unit") or "个"
                qty = p.get("current_stock", "?")
                batch_count = len(resp.get("batches") or [])
                extra = f"，共{batch_count}个批次" if batch_count else ""
                say = f"{p.get('name', '')}当前库存{qty}{unit}{extra}。"
                data = {
                    "name": p.get("name"),
                    "qty": qty,
                    "unit": unit,
                    "batch_count": batch_count,
                }
        else:
            candidates = _candidates()
            if candidates:
                data["candidates"] = candidates
                parts = []
                for c in candidates:
                    n = c["name"]
                    sku, stock = c.get("sku", ""), c.get("stock")
                    if sku: n += f"（{sku}）"
                    if stock is not None: n += f"库存{stock}"
                    parts.append(n)
                say = f"找到多个相似产品：{'、'.join(parts)}。请告诉我具体是哪个。"
                say_kind = "ask"
            else:
                say = _fail_text("查询失败，未找到该产品。")

    elif operation == "search":
        total = int(resp.get("total") or 0)
        items = []
        for item in (resp.get("items") or [])[:5]:
            items.append({
                "name": item.get("name") or item.get("material_name") or item.get("display_name"),
                "qty": item.get("current_stock") or item.get("quantity"),
                "unit": item.get("unit"),
            })
        data = {"total": total, "items": items}
        if success and total > 0:
            count = len(items)
            say = f"找到{total}条匹配，已返回{count}条。"
            if total > count:
                say += "结果太多已截断，可缩小关键词。"
        elif success:
            say = "没有找到任何匹配的结果。"
            say_kind = "fail"
        else:
            say = _fail_text("搜索失败。")

    elif operation == "resolve_name":
        if resp.get("confident") and resp.get("best_match"):
            best = resp["best_match"]
            say = f"我识别为{best.get('name', '')}。"
            data = {"name": best.get("name")}
        elif resp.get("candidates"):
            candidates = _candidates()
            data["candidates"] = candidates
            parts = []
            for c in candidates:
                n = c["name"]
                sku, stock = c.get("sku", ""), c.get("stock")
                if sku: n += f"（{sku}）"
                if stock is not None: n += f"库存{stock}"
                parts.append(n)
            say = f"我不确定你说的是哪一个，候选有：{'、'.join(parts)}。请告诉我具体是哪个。"
            say_kind = "ask"
        else:
            say = _fail_text("没有找到匹配的名称。")

    elif operation == "query_batch":
        if success:
            b = resp.get("batch") or {}
            p = resp.get("product") or {}
            if p and not b:
                unit = p.get("unit") or "个"
                qty = p.get("current_stock", "?")
                say = f"{p.get('name', '')}当前库存{qty}{unit}。"
                data = {
                    "name": p.get("name"),
                    "qty": qty,
                    "unit": unit,
                    "batch_count": len(resp.get("batches") or []),
                }
            else:
                unit = b.get("unit") or "个"
                var = f"（{b.get('variant')}）" if b.get("variant") else ""
                loc = b.get("location") or "未指定库位"
                wh = b.get("warehouse_name")
                wh_info = f"，仓库：{wh}" if wh else ""
                if b.get("is_exhausted"):
                    say = (
                        f"批次{b.get('batch_no', '')}已耗尽，原本是"
                        f"{b.get('material_name', '')}{var}，"
                        f"初始数量{b.get('initial_quantity', '?')}{unit}，位于{loc}{wh_info}。"
                    )
                else:
                    say = (
                        f"批次{b.get('batch_no', '')}是{b.get('material_name', '')}{var}，"
                        f"当前余量{b.get('quantity', '?')}{unit}，位于{loc}{wh_info}。"
                    )
                data = {
                    "batch_no": b.get("batch_no"),
                    "name": b.get("material_name"),
                    "qty": b.get("quantity"),
                    "unit": unit,
                    "location": loc,
                }
        else:
            say = _fail_text("没有找到该批次。")

    elif operation == "move_batch_location":
        if success:
            src = resp.get("source_batch") or {}
            tgt = resp.get("target_batch") or {}
            moved = resp.get("moved_quantity", "?")
            full = bool(resp.get("full_move"))
            to_loc = resp.get("to_location") or ""
            if full:
                say = (
                    f"已把批次{src.get('batch_no', '')}整体挪到"
                    f"{to_loc or '新库位'}，共{moved}件。"
                )
            else:
                say = (
                    f"已从批次{src.get('batch_no', '')}拆出{moved}件到"
                    f"{to_loc or '新库位'}，新批次{tgt.get('batch_no', '')}，"
                    f"原批次还剩{src.get('quantity', '?')}件。"
                )
            data = {
                "batch_no": src.get("batch_no"),
                "to_location": to_loc,
                "moved_qty": moved,
                "full_move": full,
            }
        else:
            say = f"本次没有移动批次。{_fail_text('批次移位失败')}"

    elif operation == "get_today_statistics":
        if success:
            s = resp.get("statistics") or {}
            say = (
                f"今天入库{s.get('today_in', 0)}件、出库{s.get('today_out', 0)}件，"
                f"净变化{s.get('net_change', 0)}件，当前库存总量{s.get('total_stock', 0)}件，"
                f"低库存{s.get('low_stock_count', 0)}个。"
            )
            data = {
                "in": s.get("today_in", 0),
                "out": s.get("today_out", 0),
                "net": s.get("net_change", 0),
                "total": s.get("total_stock", 0),
                "low": s.get("low_stock_count", 0),
            }
        else:
            say = _fail_text("统计查询失败。")

    if say is None:
        say = _fail_text("操作失败。")
        say_kind = "fail"

    return {
        "ok": success,
        "executed": executed,
        "say": say,
        "say_kind": say_kind,
        "data": data,
        "awaiting_confirm": awaiting_confirm,
    }


def _antihallucination(operation: str):
    """装饰器：包装返回值，注入 facts.executed / speak* / next_action / retry_hint。

    设计变更（2026-05-15）：之前把 _RULES_FOOTER 追加到每个 tool 的 docstring，
    导致 ListToolsRequest 响应体超过 xiaozhi WS 缓冲（实测 ~17KB），云端用
    1009 (message too big) 直接关闭连接。
    现在规则只通过 FastMCP(instructions=...) 在 initialize 阶段一次性下发，
    不在每个 tool 描述里重复，将 ListTools 响应大小压回到 ~2KB 量级。

    硬化（2026-05-22）：增加 try/except 包住整个 fn 调用 + _wrap_response。
    LLM 偶尔会编造 schema 外的关键字参数（如给 query_stock 多传 location='A-01'），
    Python 在 wrapper 调 fn(**kwargs) 时直接抛 TypeError，未捕获会冒出到
    FastMCP stdio handler 让整个 server task group 崩溃、stdio 断（BrokenResourceError），
    导致评测 / 生产长跑后 MCP 服务突然失联。这里把任何异常都转为结构化 error
    返回，进程不死。
    """
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                raw = fn(*args, **kwargs)
            except TypeError as e:
                # LLM 传了 schema 外的 kwarg 或类型不对
                logger.warning(f"{operation} TypeError: {e}; args={args} kwargs={kwargs}")
                raw = {
                    "success": False,
                    "error": "invalid_arguments",
                    "message": f"参数有误：{e}。请检查参数名和类型后重试。",
                }
            except Exception as e:  # noqa: BLE001
                logger.error(f"{operation} unhandled: {e}", exc_info=True)
                raw = {
                    "success": False,
                    "error": "internal_error",
                    "message": f"{operation} 内部错误：{e}。请稍后重试。",
                }
            try:
                return _wrap_response(operation, raw)
            except Exception as e:  # noqa: BLE001
                logger.error(f"{operation} wrap_response failed: {e}", exc_info=True)
                return {
                    "ok": False,
                    "executed": False,
                    "say": "系统繁忙，请稍后重试。",
                    "say_kind": "fail",
                    "data": {},
                    "awaiting_confirm": None,
                }
        return wrapper
    return deco


# 通用异常兜底：无论 provider 内部抛什么，都返回结构化 dict，让 AI 告知用户重试
def _tool_error(op: str, e: Exception) -> dict:
    logger.error(f"{op} 异常: {e}", exc_info=True)
    return {
        "success": False,
        "error": str(e),
        "message": f"{op}时遇到错误，请稍后重试。",
    }


# 创建 MCP 服务器
# instructions 字段会在 MCP initialize 响应里下发给客户端（xiaozhi 会注入到系统 prompt），
# 比塞进每个 tool 的 docstring 高效得多（避免 ListTools 响应过大触发 WS 1009 message too big）。
mcp = FastMCP("Warehouse System", instructions=_RULES_FOOTER.strip())


@mcp.tool()
@log_mcp_call
@_antihallucination("resolve_name")
def resolve_name(text: str, entity_type: str = "all") -> dict:
    """仅"先确认实体再下一步"时用；查库存/批次/出入库已内建模糊匹配，请直接调对应工具。"""
    try:
        return _get_provider().resolve_name(text, entity_type)
    except Exception as e:
        return _tool_error("名称解析", e)


@mcp.tool()
@log_mcp_call
@_antihallucination("query_stock")
def query_stock(product_name: str) -> dict:
    """按产品名查库存。"""
    try:
        resp = _get_provider().query_stock(product_name)
    except Exception as e:
        resp = _tool_error("查询库存", e)
    return resp


@mcp.tool()
@log_mcp_call
@_antihallucination("query_batch")
def query_batch(batch_no: str) -> dict:
    """按批次号查批次。"""
    try:
        resp = _get_provider().query_batch(batch_no)
    except Exception as e:
        resp = _tool_error("批次查询", e)
    return resp


@mcp.tool(
    exclude_args=["contact_id", "variant",
                  "face_image_b64", "face_embedding_b64", "face_model_tag"],
    # requires_face 已移除：拍照/识别决策统一在后端按规则驱动（option 3——规则需要时
    # 后端直连设备拉图/拉身份），不再靠 xiaozhi 运行时看静态 meta 预抓拍。
)
@log_mcp_call
@_antihallucination("stock_in")
def stock_in(product_name: str, quantity: int,
             reason_category: str = "purchase", reason_note: str = "",
             operator: str = "MCP系统",
             location: str = None, contact_id: int = None,
             variant: str = None,
             face_image_b64: str = None,
             face_embedding_b64: str = None,
             face_model_tag: str = None) -> dict:
    """入库。reason_category: purchase|return|refund|produce|transfer_in|other_in（也接受中文别名）。"""
    blocked = _enforce_face(
        "stock_in",
        image_b64=face_image_b64,
        embedding_b64=face_embedding_b64,
        embedding_model_tag=face_model_tag,
    )
    if blocked is not None:
        return blocked
    try:
        return _get_provider().stock_in(product_name, quantity, reason_category, reason_note,
                                  operator, True, location, contact_id, variant)
    except Exception as e:
        return _tool_error("入库", e)


@mcp.tool(
    exclude_args=["allow_partial_fallback",
                  "face_image_b64", "face_embedding_b64", "face_model_tag"],
    # requires_face 已移除：拍照/识别决策统一在后端按规则驱动（option 3——规则需要时
    # 后端直连设备拉图/拉身份），不再靠 xiaozhi 运行时看静态 meta 预抓拍。
)
@log_mcp_call
@_antihallucination("stock_out")
def stock_out(product_name: str, quantity: int,
              reason_category: str, reason_note: str = "",
              operator: str = "MCP系统",
              variant: str = None, location: str = None,
              batch_no: str = None,
              allow_partial_fallback: bool = False,
              face_image_b64: str = None,
              face_embedding_b64: str = None,
              face_model_tag: str = None) -> dict:
    """出库。reason_category: sell|lend|consume|loss|transfer_out|other_out（也接受中文别名/use→consume/scrap→loss）。"""
    blocked = _enforce_face(
        "stock_out",
        image_b64=face_image_b64,
        embedding_b64=face_embedding_b64,
        embedding_model_tag=face_model_tag,
    )
    if blocked is not None:
        return blocked
    try:
        return _get_provider().stock_out(product_name, quantity, reason_category, reason_note,
                                   operator, True, variant, location,
                                   batch_no=batch_no, location_fuzzy=True,
                                   allow_partial_fallback=allow_partial_fallback)
    except Exception as e:
        return _tool_error("出库", e)


@mcp.tool(exclude_args=["category", "status", "contact_type", "include_batches", "max_results"])
@log_mcp_call
@_antihallucination("search")
def search(query: str = None, entity_type: str = "material",
           category: str = None, status: str = None,
           contact_type: str = None, include_batches: bool = False,
           max_results: int = 0) -> dict:
    """搜索物料、联系方或操作员。"""
    try:
        return _get_provider().search(query, entity_type, category, status, contact_type, True,
                                include_batches, max_results)
    except Exception as e:
        return _tool_error("搜索", e)


@mcp.tool(
    exclude_args=["face_image_b64", "face_embedding_b64", "face_model_tag"],
    # requires_face 已移除：拍照/识别决策统一在后端按规则驱动（option 3——规则需要时
    # 后端直连设备拉图/拉身份），不再靠 xiaozhi 运行时看静态 meta 预抓拍。
)
@log_mcp_call
@_antihallucination("move_batch_location")
def move_batch_location(batch_no: str, new_location: str,
                         quantity: int = None,
                         operator: str = "MCP系统",
                         face_image_b64: str = None,
                         face_embedding_b64: str = None,
                         face_model_tag: str = None) -> dict:
    """批次库位移位。batch_no 精确指定批次，new_location 目标库位。
    quantity 不传=整批移；传了=拆分（该数量移到新库位，余量留在原位）。
    注意：不需要传 product_name 或 from_location，batch_no 已足够定位。
    """
    blocked = _enforce_face(
        "move_batch_location",
        image_b64=face_image_b64,
        embedding_b64=face_embedding_b64,
        embedding_model_tag=face_model_tag,
    )
    if blocked is not None:
        return blocked
    try:
        # 该工具有意不暴露 from_location / product_name（batch_no 已足够定位），
        # provider 对应参数默认为 None。此前误传了未定义的 from_location/product_name
        # 名字，触发 NameError → 批次移位永远失败。
        return _get_provider().move_batch_location(
            batch_no, new_location, quantity, operator=operator
        )
    except Exception as e:
        return _tool_error("批次移位", e)


@mcp.tool()
@log_mcp_call
@_antihallucination("get_today_statistics")
def get_today_statistics() -> dict:
    """今日入出库与库存概览。"""
    try:
        return _get_provider().get_today_statistics()
    except Exception as e:
        return _tool_error("统计查询", e)


# 启动服务器
if __name__ == "__main__":
    mcp.run(transport="stdio")
