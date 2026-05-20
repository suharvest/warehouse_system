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
import sys
import os
import logging
import yaml
import functools
import json
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
    if not _MCP_DEBUG:
        return func  # 非调试模式，直接返回原函数，零开销

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        logger.info(f"→ {func.__name__}({json.dumps(kwargs, ensure_ascii=False, default=str)})")
        try:
            result = func(*args, **kwargs)
            result_str = json.dumps(result, ensure_ascii=False, default=str)
            logger.info(f"← {func.__name__} => {result_str[:3000]}")
            return result
        except Exception as e:
            logger.error(f"✗ {func.__name__} => {e}", exc_info=True)
            raise
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
        'max_results': 30,  # MCP 搜索结果上限
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


_provider = _load_provider_from_db_or_default(_config)


# ============ Face Guard (Phase 1) ============
# 仅对 MCP tool 调用生效。通过后端 /api/face/verify-mcp 桥接到
# backend.face.orchestrator.verify_mcp_face；后端用 X-API-Key 识别
# 当前用户、租户与仓库上下文。
def _face_guard(operation: str, warehouse_id: int = None) -> dict:
    """Verify face for an MCP write operation. Returns the decision dict.

    Behavior:
    - status='pass'    -> caller proceeds
    - status='skipped' -> caller proceeds (feature disabled or rule not required)
    - status='deny'    -> caller MUST surface an error to the LLM and abort

    Failure handling (fail-closed):
    - api_base unset -> skipped (face module not deployed in this MCP host)
    - HTTP 4xx/5xx   -> deny (server reachable but rejected; never silently bypass)
    - transport error (network, timeout) -> deny (treat as if face check failed)
    """
    import requests as _r
    api_base = _config.get('api_base_url', '').rstrip('/')
    if not api_base:
        return {"status": "skipped", "failure_reason": "no_api_base"}
    headers = {}
    auth = _config.get('auth') or {}
    if auth.get('type') == 'api_key':
        headers[auth.get('header', 'X-API-Key')] = auth.get('key', '')
    elif auth.get('type') == 'bearer':
        headers['Authorization'] = f"Bearer {auth.get('token', '')}"
    body = {"operation": operation, "warehouse_id": warehouse_id}
    try:
        resp = _r.post(f"{api_base}/face/verify-mcp", json=body, headers=headers, timeout=5)
        if resp.status_code >= 400:
            logger.warning("face verify returned %s: %s", resp.status_code, resp.text[:200])
            return {"status": "deny", "failure_reason": f"http_{resp.status_code}"}
        return resp.json()
    except Exception as e:
        logger.warning("face verify transport error: %s", e)
        return {"status": "deny", "failure_reason": "transport_error"}


def _enforce_face(operation: str, warehouse_id: int = None) -> dict | None:
    """Run face guard; return a tool-error dict to surface, or None to proceed."""
    decision = _face_guard(operation, warehouse_id)
    if decision.get("status") == "deny":
        reason = decision.get("failure_reason") or "denied"
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

────────── 反幻觉硬规则（所有工具通用，违反即用户投诉） ──────────
1. 数字必须来自响应的 facts 字段或 product/batch 字段，禁止口算或推测。
2. 响应里有 speak / speak_ask / speak_failed 时必须**照搬原文**，禁止改写、合并、增减数字或状态描述。
3. facts.executed=false 时操作**未发生**，禁止使用"已 / 完成 / 成功 / 出库了 / 入库了"等表示写入的词。
4. success=false 时禁止生成成功结果；只能播报 speak_failed 或 speak_ask，不得自行重试。
5. candidates 非空时必须让用户从中选择；禁止自行选最高分候选，禁止从对话历史里猜实体。
6. 用户问"现在 / 还剩 / 目前 / 最新"时必须重新调 query_stock；禁止引用 5 秒前的查询结果。
7. truncated=true 时必须告诉用户"结果太多已截断"，禁止假装"就这些"。
8. side_effect 字段只表示工具类型（如 stock_out 表示这是出库工具），**不表示真的执行了**。
   实际是否执行只看 facts.executed。
9. 工具响应若缺少 speak / speak_ask / speak_failed 任一字段非空，必须回答"系统返回不完整，
   我不能确认结果"，禁止编造业务结论。
10. 用户说"你看着办 / 帮我处理"等模糊指令，涉及写操作时仍必须调工具并以工具结果为准；
    禁止口头承诺已处理。
11. next_action 决定下一步：
    - done：仅播报 speak。
    - ask_user_to_choose：用 speak_ask 问用户，等用户选了候选再继续。
    - ask_user_to_confirm_partial_fallback：必须用 speak_ask 询问用户是否允许从其他批次补差额，
      仅当用户明确说"是/可以/同意"后，再用 retry_hint.params_patch 合并到原参数重发；
      用户说"否/不要/算了"则播报"那本次不出库"，结束。
    - retry_forbidden / no_result：仅播报 speak_failed，禁止自动重试。
12. retry_hint.requires_user_confirmation=true 时，未获得用户口头确认前**绝对不许**重发工具调用，
    哪怕系统认为"显然应该补"也不行——尊重用户最终决定权。
13. query_stock ↔ query_batch 路由纠错（仅一次例外，覆盖第 4/11 条 retry_forbidden）：
    a. 首次调用任一查询工具失败且 candidates 为空时，**允许**换到另一个查询工具重试**一次**；
       重试时**必须**传 routing_retry=true，否则视为违反第 4 条。
    b. 响应里 facts.routing_retry_used=true 表示本次已经是纠错重试。
    c. 看到 facts.routing_retry_used=true 时**禁止**再发起任何工具调用，
       无论成败都只能按响应播报（success→speak；failure→speak_failed）。
    d. 禁止用 routing_retry=true 重发**同一**工具——纠错只能是换到另一个工具。
    e. routing_retry 仅适用于 query_stock 和 query_batch，其他工具不接受此参数。
"""

# 写操作集合 — 只有这类在 success=true 时才被认为 executed=true
_WRITE_OPS = {"stock_in", "stock_out", "move_batch_location"}


def _wrap_response(operation: str, resp: dict) -> dict:
    """给所有 MCP 工具响应注入 facts.executed 和 speak/speak_ask/speak_failed。

    职责：把幻觉风险点（数字、执行状态、下一步动作）全部落到结构化字段里，
    让 LLM 无空间发挥。
    """
    if not isinstance(resp, dict):
        return resp  # provider 异常情况，保持原样

    success = bool(resp.get("success"))
    facts = resp.setdefault("facts", {})
    facts["query_at"] = datetime.now().isoformat(timespec="seconds")
    # executed = "数据库真的被改了吗"
    facts["executed"] = success and (operation in _WRITE_OPS)
    resp["side_effect"] = (
        "inventory_out" if operation == "stock_out"
        else "inventory_in" if operation == "stock_in"
        else "batch_relocate" if operation == "move_batch_location"
        else "none"
    )

    speak = speak_ask = speak_failed = None
    next_action = "done" if success else "retry_forbidden"
    retry_hint = None

    if operation == "stock_out":
        if success:
            p = resp.get("product") or {}
            bcs = resp.get("batch_consumptions") or []
            unit = (p.get("unit") or "个")
            details = "、".join(
                f"批次{b.get('batch_no')}出{b.get('quantity')}{unit}" for b in bcs
            )
            tail = f"（{details}）" if details else ""
            speak = (
                f"已出库{p.get('name', '')}共{p.get('out_quantity', '?')}{unit}{tail}，"
                f"当前库存{p.get('new_quantity', '?')}{unit}。"
            )
        else:
            err = resp.get("error") or ""
            msg = resp.get("message") or "出库失败"
            if err in ("ambiguous_name", "location_ambiguous"):
                cands = resp.get("candidates") or []
                names = "、".join((c.get("name") or "") for c in cands[:5])
                speak_ask = f"我不确定你说的是哪一个，候选有：{names}。请告诉我具体是哪个。"
                next_action = "ask_user_to_choose"
            elif err == "batch_insufficient_stock":
                bn = resp.get("batch_no_requested") or "该批次"
                avail = resp.get("batch_available")
                short = resp.get("shortfall")
                can = resp.get("can_fallback")
                other = resp.get("fallback_total_available")
                if can:
                    # 进入"询问用户是否允许从其他批次补差额"流程
                    speak_ask = (
                        f"批次{bn}只有{avail}个，缺{short}个；其他批次合计{other}个可补。"
                        f"要不要先扣完{bn}的{avail}个，再从其他批次补{short}个？请说是或否。"
                    )
                    next_action = "ask_user_to_confirm_partial_fallback"
                    retry_hint = {
                        "allowed": True,
                        "tool": "stock_out",
                        "params_patch": {"allow_partial_fallback": True},
                        "requires_user_confirmation": True,
                        "reason": "用户口头确认后，使用 params_patch 重发同一请求即可。",
                    }
                else:
                    speak_failed = (
                        f"本次没有扣任何库存。批次{bn}只有{avail}个，"
                        f"其他批次合计{other}个也不够补{short}个，无法完成出库。"
                    )
                    next_action = "retry_forbidden"
            else:
                speak_failed = f"本次没有扣任何库存。{msg}"
                next_action = "retry_forbidden"

    elif operation == "stock_in":
        if success:
            p = resp.get("product") or {}
            b = resp.get("batch") or {}
            unit = (p.get("unit") or "个")
            bn = b.get("batch_no") or "-"
            speak = (
                f"已入库{p.get('name', '')}{p.get('in_quantity', '?')}{unit}，"
                f"批次号{bn}，当前库存{p.get('new_quantity', '?')}{unit}。"
            )
        else:
            err = resp.get("error") or ""
            msg = resp.get("message") or "入库失败"
            if err == "ambiguous_name":
                cands = resp.get("candidates") or []
                names = "、".join((c.get("name") or "") for c in cands[:5])
                speak_ask = f"我不确定你说的是哪一个，候选有：{names}。请告诉我具体是哪个。"
                next_action = "ask_user_to_choose"
            else:
                speak_failed = f"本次没有入库。{msg}"
                next_action = "retry_forbidden"

    elif operation == "query_stock":
        if success:
            p = resp.get("product") or {}
            unit = (p.get("unit") or "个")
            qty = p.get("current_stock", "?")
            extra = ""
            batches = resp.get("batches") or []
            if batches:
                extra = f"，共{len(batches)}个批次"
            speak = f"{p.get('name', '')}当前库存{qty}{unit}{extra}。"
        else:
            cands = resp.get("candidates") or []
            if cands:
                names = "、".join((c.get("name") or "") for c in cands[:5])
                speak_ask = f"找到多个相似产品：{names}。请告诉我具体是哪个。"
                next_action = "ask_user_to_choose"
            else:
                speak_failed = resp.get("message") or "查询失败，未找到该产品。"
                next_action = "no_result"

    elif operation == "search":
        total = int(resp.get("total") or 0)
        count = int(resp.get("count") or 0)
        facts["truncated"] = total > count
        if success and total > 0:
            speak = f"找到{total}条匹配，已返回{count}条。"
            if total > count:
                speak += "结果太多已截断，可缩小关键词。"
        elif success:
            speak_failed = "没有找到任何匹配的结果。"
            next_action = "no_result"
        else:
            speak_failed = resp.get("message") or "搜索失败。"
            next_action = "retry_forbidden"

    elif operation == "resolve_name":
        if resp.get("confident") and resp.get("best_match"):
            best = resp["best_match"]
            speak = f"我识别为{best.get('name', '')}。"
            next_action = "done"
        elif resp.get("candidates"):
            names = "、".join((c.get("name") or "") for c in resp["candidates"][:5])
            speak_ask = f"我不确定你说的是哪一个，候选有：{names}。请告诉我具体是哪个。"
            next_action = "ask_user_to_choose"
        else:
            speak_failed = resp.get("message") or "没有找到匹配的名称。"
            next_action = "no_result"

    elif operation == "query_batch":
        if success:
            b = resp.get("batch") or {}
            unit = b.get("unit") or "个"
            var = f"（{b.get('variant')}）" if b.get("variant") else ""
            loc = b.get("location") or "未指定库位"
            wh = b.get("warehouse_name")
            wh_info = f"，仓库：{wh}" if wh else ""
            if b.get("is_exhausted"):
                speak = (
                    f"批次{b.get('batch_no', '')}已耗尽，原本是"
                    f"{b.get('material_name', '')}{var}，"
                    f"初始数量{b.get('initial_quantity', '?')}{unit}，位于{loc}{wh_info}。"
                )
            else:
                speak = (
                    f"批次{b.get('batch_no', '')}是{b.get('material_name', '')}{var}，"
                    f"当前余量{b.get('quantity', '?')}{unit}，位于{loc}{wh_info}。"
                )
        else:
            err = resp.get("error") or ""
            if err == "batch_not_found":
                speak_failed = resp.get("message") or "没有找到该批次。"
                next_action = "no_result"
            else:
                speak_failed = resp.get("message") or "批次查询失败。"
                next_action = "retry_forbidden"

    elif operation == "move_batch_location":
        if success:
            src = resp.get("source_batch") or {}
            tgt = resp.get("target_batch") or {}
            moved = resp.get("moved_quantity", "?")
            full = bool(resp.get("full_move"))
            to_loc = resp.get("to_location") or ""
            if full:
                speak = (
                    f"已把批次{src.get('batch_no', '')}整体挪到"
                    f"{to_loc or '新库位'}，共{moved}件。"
                )
            else:
                speak = (
                    f"已从批次{src.get('batch_no', '')}拆出{moved}件到"
                    f"{to_loc or '新库位'}，新批次{tgt.get('batch_no', '')}，"
                    f"原批次还剩{src.get('quantity', '?')}件。"
                )
        else:
            msg = resp.get("message") or "批次移位失败"
            # 移位错误均无候选可选；要么是用户输入有误（让用户重述/纠正），要么是系统拒绝。
            # 一律走 retry_forbidden + speak_failed，由 LLM 在下一轮对话里询问用户重新输入。
            speak_failed = f"本次没有移动批次。{msg}"
            next_action = "retry_forbidden"

    elif operation == "get_today_statistics":
        if success:
            s = resp.get("statistics") or {}
            speak = (
                f"今天入库{s.get('today_in', 0)}件、出库{s.get('today_out', 0)}件，"
                f"净变化{s.get('net_change', 0)}件，当前库存总量{s.get('total_stock', 0)}件，"
                f"低库存{s.get('low_stock_count', 0)}个。"
            )
        else:
            speak_failed = resp.get("message") or "统计查询失败。"

    # 三个 speak 字段一定要全部出现（哪怕为 None），让 LLM 无歧义
    resp["speak"] = speak
    resp["speak_ask"] = speak_ask
    resp["speak_failed"] = speak_failed
    resp["next_action"] = next_action
    resp["retry_hint"] = retry_hint
    return resp


def _antihallucination(operation: str):
    """装饰器：包装返回值，注入 facts.executed / speak* / next_action / retry_hint。

    设计变更（2026-05-15）：之前把 _RULES_FOOTER 追加到每个 tool 的 docstring，
    导致 ListToolsRequest 响应体超过 xiaozhi WS 缓冲（实测 ~17KB），云端用
    1009 (message too big) 直接关闭连接。
    现在规则只通过 FastMCP(instructions=...) 在 initialize 阶段一次性下发，
    不在每个 tool 描述里重复，将 ListTools 响应大小压回到 ~2KB 量级。
    """
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return _wrap_response(operation, fn(*args, **kwargs))
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
    """
    将模糊文本（语音识别、用户口语输入等）解析为系统中精确的实体名称。

    适用场景：
    - 用户说了一个不确定的名称，需要先确认再操作
    - 需要同时搜索物料、联系方、操作员（entity_type="all"）
    - 需要获取候选列表让用户选择

    注意：query_stock、stock_in、stock_out 已内建模糊匹配，
    大多数情况下可直接调用，无需先调 resolve_name。
    仅在需要「先确认名称再决定下一步」时才需要本工具。

    参数:
        text: 需要解析的文本（如语音识别结果"螺丝钉"、"张三"等）
        entity_type: 实体类型，"material" | "contact" | "operator" | "all"（默认搜索全部类型）

    返回:
        best_match: 最佳匹配（含 name, score, entity_type）
        confident: 是否高置信度（true 时可直接使用 best_match）
        candidates: 候选列表（confident=false 时需从中选择）
    """
    try:
        return _provider.resolve_name(text, entity_type)
    except Exception as e:
        return _tool_error("名称解析", e)


@mcp.tool()
@log_mcp_call
@_antihallucination("query_stock")
def query_stock(product_name: str, show_batches: bool = False,
                routing_retry: bool = False) -> dict:
    """
    查询产品库存详情。支持模糊名称输入，内建自动解析。

    可直接传入不精确的名称（如语音识别结果），工具会自动模糊匹配。
    无需先调用 resolve_name。

    注意：同一产品可能有多个变体（如不同规格型号），也可能分布在不同位置。
    当有多个批次时，返回结果会自动包含每个批次的数量、变体和位置明细。
    用户提到的规格型号信息可以直接包含在 product_name 中，无需拆分。

    参数:
        product_name: 产品名称（支持模糊输入，可包含规格型号，如"空气开关D10A2P"、"螺丝"等）
        show_batches: 是否在返回结果中包含完整批次列表（默认 false）。
                      多批次时消息中已自动包含明细，此参数控制是否额外返回结构化数据。
        routing_retry: 路由纠错标志。仅当上一次 query_batch 失败且 candidates 为空、
                      LLM 判断用户其实是说产品名时置 True（详见 _RULES_FOOTER 第 13 条）。
                      默认 False。

    返回:
        success=true 时：产品库存详情（name, sku, current_stock, unit, safe_stock,
        location, today_in, today_out, status 等）。多批次时消息自动包含每批明细
        （含 variant 变体标识和 location 位置）。
        success=false 时：如有候选项会在 candidates 中列出
    """
    try:
        resp = _provider.query_stock(product_name, show_batches)
    except Exception as e:
        resp = _tool_error("查询库存", e)
    resp.setdefault("facts", {})["routing_retry_used"] = bool(routing_retry)
    return resp


@mcp.tool()
@log_mcp_call
@_antihallucination("query_batch")
def query_batch(batch_no: str, routing_retry: bool = False) -> dict:
    """
    按**批次号**查询批次详情（只读）。

    使用时机（用户语音典型说法）：
    - "B-2026-003 是什么"
    - "批次 20260513-001 还剩多少"
    - "B 二零二六零零三在哪个货架"

    与 query_stock 的分工：
    - 用户说**产品名**（"螺丝"、"指示灯红色"）→ 用 query_stock
    - 用户说**批次号**（含日期数字串如 20260513，或字母-年-编号如 B-2026-003）→ 用本工具
    - 任一查询失败且 candidates 为空 → 切换到另一工具重试**一次**，
      重试时**必须**置 routing_retry=True（详见 _RULES_FOOTER 第 13 条）

    批次号自动归一化语音输入（"20260513023" → "20260513-023"）。
    含已耗尽批次（is_exhausted=true）方便溯源——播报时会明确告知"已耗尽"。

    参数：
        batch_no: 批次号（必填）
        routing_retry: 路由纠错标志。仅当上一次 query_stock 失败且 candidates 为空、
                      LLM 判断用户其实是说批次号时置 True。默认 False。

    返回：
        success=true：含 batch 字段（batch_no, quantity, initial_quantity, location,
                     variant, is_exhausted, material_name, material_sku, unit,
                     warehouse_name, contact_name, created_at）
        success=false 且 error="batch_not_found"：作用域内确实没有该批次
        facts.routing_retry_used 字段会回显本次是否为路由纠错重试
    """
    try:
        resp = _provider.query_batch(batch_no)
    except Exception as e:
        resp = _tool_error("批次查询", e)
    resp.setdefault("facts", {})["routing_retry_used"] = bool(routing_retry)
    return resp


@mcp.tool()
@log_mcp_call
@_antihallucination("stock_in")
def stock_in(product_name: str, quantity: int,
             reason_category: str = "purchase", reason_note: str = "",
             operator: str = "MCP系统",
             location: str = None, contact_id: int = None,
             variant: str = None) -> dict:
    """
    产品入库。直接传入名称即可，工具内部始终启用模糊匹配。

    MCP 上游通常是 ASR（语音识别），输入注定带噪声（如"螺丝"被识别成"螺司"），
    所以模糊匹配是工具的硬性前提，不暴露开关。

    参数:
        product_name: 产品名称（支持模糊输入，如"螺丝"会自动匹配"M3螺丝"）
        quantity: 入库数量（正整数）
        reason_category: 入库原因分类，必须是以下之一：
            - "purchase": 采购入库（默认）
            - "return": 借还（物料归还）
            - "refund": 退货入库
            - "produce": 生产入库
            - "transfer_in": 调拨入库
            - "other_in": 其他入库
        reason_note: 备注详情（可选），如"张三归还"、"供应商A"
        operator: 操作人（默认"MCP系统"）
        location: 存放位置（可选，如"A区-01架"）
        contact_id: 关联联系方 ID（可选，如供应商 ID）
        variant: 变体标识（可选，如"红"、"大号"等）。
                 同一产品可能有多个变体（如不同颜色），入库时可指定变体以区分批次。

    返回:
        success=true 时：入库成功，含批次信息（含 variant）和产品详情
        success=false 且有 candidates 时：名称不够明确，需用候选中的精确名称重试
    """
    blocked = _enforce_face("stock_in")
    if blocked is not None:
        return blocked
    try:
        return _provider.stock_in(product_name, quantity, reason_category, reason_note,
                                  operator, True, location, contact_id, variant)
    except Exception as e:
        return _tool_error("入库", e)


@mcp.tool()
@log_mcp_call
@_antihallucination("stock_out")
def stock_out(product_name: str, quantity: int,
              reason_category: str, reason_note: str = "",
              operator: str = "MCP系统",
              variant: str = None, location: str = None,
              batch_no: str = None,
              allow_partial_fallback: bool = False) -> dict:
    """
    产品出库。可直接传入模糊名称，自动解析为精确产品。
    默认按 FIFO 消耗批次；若指定 variant / location，则仅从匹配批次中 FIFO 消耗。
    若指定 batch_no，则仅从该批次扣减，不 fallback 到 FIFO。

    MCP 上游通常是 ASR（语音识别），输入注定带噪声，
    所以模糊匹配始终启用，不暴露开关。

    参数：
        product_name: 产品名称（支持模糊输入，如"指示灯"会匹配到"LED指示灯"）
        quantity: 出库数量（正整数）
        reason_category: 出库原因分类，必须是以下之一：
            - "sell": 销售出库
            - "use": 领用/消耗
            - "lend": 借出
            - "scrap": 报废
            - "return_out": 退货出库
            - "transfer_out": 调拨出库
            - "other_out": 其他出库
        reason_note: 详情备注（如"销售给XX公司"、"借给小王"），选填
        operator: 操作员姓名（默认"MCP系统"）
        variant: 变体过滤（可选，如"红"）。指定后仅消耗该变体的批次。精确匹配。
        location: 库位过滤（可选，如"A-01"）。
                  MCP 场景自动开启作用域模糊匹配：用户口述"A 区"可匹配到 A-01。
                  若模糊结果歧义会返回候选让 LLM 判断。
        batch_no: 指定批次号（可选，如"B-2026-003"）。
                  用户明确说"出 B-2026-003 这批"时才传。
                  默认指定后只从该批次扣，不足时返回 batch_insufficient_stock 失败。
                  若同时传 location/variant 与批次实际不符，会报 batch_field_mismatch。
        allow_partial_fallback: 默认 False。仅在用户**明确确认**"愿意从其他批次补差额"
                  后才置 True 重发。看到 next_action=ask_user_to_confirm_partial_fallback
                  时**必须先用 speak_ask 询问用户**，得到肯定答复再用 retry_hint.params_patch
                  重发；禁止首次调用就置 True，禁止未经用户同意就重试。

    返回：
        success=true 时：出库成功，含批次消耗详情（每个消耗批次含 variant 字段）
        success=false 时：含具体错误类型，如 ambiguous_name / location_ambiguous /
                         batch_not_found / batch_insufficient_stock / batch_field_mismatch 等
    """
    blocked = _enforce_face("stock_out")
    if blocked is not None:
        return blocked
    try:
        return _provider.stock_out(product_name, quantity, reason_category, reason_note,
                                   operator, True, variant, location,
                                   batch_no=batch_no, location_fuzzy=True,
                                   allow_partial_fallback=allow_partial_fallback)
    except Exception as e:
        return _tool_error("出库", e)


@mcp.tool()
@log_mcp_call
@_antihallucination("search")
def search(query: str = None, entity_type: str = "material",
           category: str = None, status: str = None,
           contact_type: str = None,
           include_batches: bool = False,
           max_results: int = 0) -> dict:
    """
    统一搜索工具，可搜索物料、联系方、操作员。

    MCP 上游通常是 ASR（语音识别），输入注定带噪声，
    所以模糊匹配始终启用，不暴露开关。

    常见用法：
    - 搜物料："帮我找螺丝相关的产品" → search(query="螺丝", entity_type="material")
    - 查库存告急："哪些产品库存不足" → search(status="danger,warning", entity_type="material")
    - 查变体："指示灯有哪些颜色" → search(query="指示灯", include_batches=True)
    - 找供应商："搜索张三" → search(query="张三", entity_type="contact", contact_type="supplier")
    - 找操作员："小李是谁" → search(query="小李", entity_type="operator")

    参数:
        query: 搜索关键词（支持模糊输入，如语音识别结果）
        entity_type: 搜索类型 "material"(物料) | "contact"(联系方) | "operator"(操作员)
        category: 物料分类过滤（仅 material 有效，精确匹配）
        status: 库存状态过滤（仅 material 有效），可选值：
                "normal"(正常) / "warning"(偏低) / "danger"(告急)，多个用逗号分隔
        contact_type: 联系方类型过滤（仅 contact 有效），"supplier"(供应商) / "customer"(客户)
        include_batches: 搜索物料时是否附带每个物料的批次列表（默认 false，仅 entity_type="material" 有效）
        max_results: 返回结果上限（0 表示使用配置默认值）

    返回:
        items: 匹配结果列表（include_batches=true 时每个物料含 batches 字段，
               每个批次含 variant 变体标识）
        total: 总匹配数（可能大于返回的 items 数量）
    """
    try:
        return _provider.search(query, entity_type, category, status, contact_type, True,
                                include_batches, max_results)
    except Exception as e:
        return _tool_error("搜索", e)


@mcp.tool()
@log_mcp_call
@_antihallucination("move_batch_location")
def move_batch_location(batch_no: str, new_location: str,
                         quantity: int = None,
                         from_location: str = None,
                         product_name: str = None,
                         operator: str = "MCP系统") -> dict:
    """
    批次库位移动。支持整批移位和**部分数量拆分移位**。

    使用场景（用户语音常见说法）：
    - "把 B-2026-003 这批整体挪到 B-02 区" → 整批移位（不传 quantity）
    - "把 B-2026-003 这批挪 50 个到 B-02 区" → 拆分移位（quantity=50）
    - "A-01 的螺丝挪一半到 B-02" → 用户先确认 batch_no 再调用

    与入库/出库的区别：本工具**不改变物料总库存**，只调整批次的库位/数量分布。
    禁止用此工具完成出库或入库；如果用户想"调拨到别的仓库"应改用调拨流程。

    MCP 上游通常是 ASR（语音识别），batch_no 自动归一化（"20260513023" → "20260513-023"）。

    参数：
        batch_no: 源批次号（必填，如 "B-2026-003" 或 "20260513-023"）
        new_location: 目标库位（必填，如 "B-02 架"）
        quantity: 移位数量（可选）。
                  None 或等于该批次余量时 → 整批移位（仅更新 location）；
                  小于该批次余量时 → 拆分：源批次扣减此数量，并在目标库位新建同物料/同变体的新批次；
                  大于该批次余量时 → 报错 insufficient_quantity。
        from_location: 源库位校验（可选）。指定后若与批次当前位置不符会返回
                       from_location_mismatch，用于防止 ASR 误识别批次号导致挪错。
        product_name: 物料名校验（可选）。指定后若与批次实际所属物料不符会返回 product_mismatch。
        operator: 操作员（默认 "MCP系统"）

    返回：
        success=true 时：含 source_batch（移位后源批次余量）、target_batch（目标批次，
                         整批移位时与源批次同 id/同 batch_no，拆分时是新生成的批次）、
                         moved_quantity、from_location、to_location、full_move
        success=false 时：error 取值为
            - missing_batch_no / empty_location
            - batch_not_found（批次不存在或已耗尽）
            - product_mismatch / from_location_mismatch
            - same_location（新旧库位相同，无需移动）
            - invalid_quantity / insufficient_quantity
    """
    blocked = _enforce_face("move_batch_location")
    if blocked is not None:
        return blocked
    try:
        return _provider.move_batch_location(
            batch_no, new_location, quantity, from_location, product_name, operator
        )
    except Exception as e:
        return _tool_error("批次移位", e)


@mcp.tool()
@log_mcp_call
@_antihallucination("get_today_statistics")
def get_today_statistics() -> dict:
    """
    查询当天仓库统计概览。无需参数，直接调用即可。

    返回：今日入库量、出库量、库存总量、低库存数量、净变化量。
    适用于：「今天仓库情况怎么样」「今日出入库汇总」等问题。
    """
    try:
        return _provider.get_today_statistics()
    except Exception as e:
        return _tool_error("统计查询", e)


# 启动服务器
if __name__ == "__main__":
    mcp.run(transport="stdio")
