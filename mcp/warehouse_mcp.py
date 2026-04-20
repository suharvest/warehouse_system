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

# 配置日志
logger = logging.getLogger('WarehouseMCP')

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

    return config


_config = load_config()

# 确保能找到 providers 包（直接运行 warehouse_mcp.py 时需要）
sys.path.insert(0, os.path.dirname(__file__))
from providers import load_provider  # noqa: E402


def _load_provider_from_db_or_default(default_config: dict):
    """从数据库读取系统模式，若为 external_erp 则加载激活的自定义 Provider。

    任何异常（数据库不存在、文件缺失等）均回退到默认 Provider。
    """
    import sqlite3 as _sqlite3
    import json as _json

    db_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        'backend',
        os.environ.get('DATABASE_PATH', 'warehouse.db')
    )

    try:
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"数据库文件不存在: {db_path}")

        conn = _sqlite3.connect(db_path)
        conn.row_factory = _sqlite3.Row
        cursor = conn.cursor()

        # 读取系统模式
        cursor.execute("SELECT value FROM system_settings WHERE key = 'system_mode'")
        row = cursor.fetchone()
        mode = row['value'] if row else 'self_owned'

        if mode != 'external_erp':
            conn.close()
            return load_provider(default_config)

        # 查询激活的 Provider
        cursor.execute("SELECT * FROM erp_providers WHERE is_active = 1 LIMIT 1")
        provider_row = cursor.fetchone()
        conn.close()

        if not provider_row:
            logger.warning("系统模式为 external_erp 但没有激活的 Provider，回退到默认 Provider")
            return load_provider(default_config)

        # 构造 Provider 配置
        stored_config = _json.loads(provider_row['config']) if provider_row['config'] else {}
        merged_config = {**default_config, **stored_config}
        merged_config['provider'] = provider_row['provider_name']

        # 从文件动态加载
        custom_dir = os.path.join(os.path.dirname(__file__), 'providers', 'custom')
        filepath = os.path.join(custom_dir, provider_row['filename'])

        if not os.path.exists(filepath):
            logger.warning(f"激活的 Provider 文件不存在: {filepath}，回退到默认 Provider")
            return load_provider(default_config)

        from providers.test_runner import load_provider_from_file
        logger.info(f"使用外部 ERP Provider: {provider_row['provider_name']} ({provider_row['filename']})")
        return load_provider_from_file(filepath, merged_config)

    except Exception as e:
        logger.warning(f"从数据库加载 Provider 失败: {e}，回退到默认 Provider")
        return load_provider(default_config)


_provider = _load_provider_from_db_or_default(_config)

# 创建 MCP 服务器
mcp = FastMCP("Warehouse System")


@mcp.tool()
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
    return _provider.resolve_name(text, entity_type)


@mcp.tool()
def query_stock(product_name: str, show_batches: bool = False) -> dict:
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

    返回:
        success=true 时：产品库存详情（name, sku, current_stock, unit, safe_stock,
        location, today_in, today_out, status 等）。多批次时消息自动包含每批明细
        （含 variant 变体标识和 location 位置）。
        success=false 时：如有候选项会在 candidates 中列出
    """
    return _provider.query_stock(product_name, show_batches)


@mcp.tool()
def stock_in(product_name: str, quantity: int,
             reason_category: str = "purchase", reason_note: str = "",
             operator: str = "MCP系统", fuzzy: bool = True,
             location: str = None, contact_id: int = None,
             variant: str = None) -> dict:
    """
    产品入库。可直接传入模糊名称，自动解析为精确产品。

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
        fuzzy: 是否启用模糊匹配（默认 true）
        location: 存放位置（可选，如"A区-01架"）
        contact_id: 关联联系方 ID（可选，如供应商 ID）
        variant: 变体标识（可选，如"红"、"大号"等）。
                 同一产品可能有多个变体（如不同颜色），入库时可指定变体以区分批次。

    返回:
        success=true 时：入库成功，含批次信息（含 variant）和产品详情
        success=false 且有 candidates 时：名称不够明确，需用候选中的精确名称重试
    """
    return _provider.stock_in(product_name, quantity, reason_category, reason_note,
                              operator, fuzzy, location, contact_id, variant)


@mcp.tool()
def stock_out(product_name: str, quantity: int,
              reason_category: str, reason_note: str = "",
              operator: str = "MCP系统", fuzzy: bool = True,
              variant: str = None, location: str = None,
              batch_no: str = None) -> dict:
    """
    产品出库。可直接传入模糊名称，自动解析为精确产品。
    默认按 FIFO 消耗批次；若指定 variant / location，则仅从匹配批次中 FIFO 消耗。
    若指定 batch_no，则仅从该批次扣减，不 fallback 到 FIFO。

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
        fuzzy: 是否启用产品名模糊匹配（默认 True）
        variant: 变体过滤（可选，如"红"）。指定后仅消耗该变体的批次。精确匹配。
        location: 库位过滤（可选，如"A-01"）。
                  MCP 场景自动开启作用域模糊匹配：用户口述"A 区"可匹配到 A-01。
                  若模糊结果歧义会返回候选让 LLM 判断。
        batch_no: 指定批次号（可选，如"B-2026-003"）。
                  用户明确说"出 B-2026-003 这批"时才传。
                  指定后只从该批次扣，不足直接报错（不 fallback 到 FIFO 补齐）。
                  若同时传 location/variant 与批次实际不符，会报 batch_field_mismatch。

    返回：
        success=true 时：出库成功，含批次消耗详情（每个消耗批次含 variant 字段）
        success=false 时：含具体错误类型，如 ambiguous_name / location_ambiguous /
                         batch_not_found / batch_insufficient_stock / batch_field_mismatch 等
    """
    return _provider.stock_out(product_name, quantity, reason_category, reason_note,
                               operator, fuzzy, variant, location,
                               batch_no=batch_no, location_fuzzy=True)


@mcp.tool()
def search(query: str = None, entity_type: str = "material",
           category: str = None, status: str = None,
           contact_type: str = None, fuzzy: bool = True,
           include_batches: bool = False,
           max_results: int = 0) -> dict:
    """
    统一搜索工具，可搜索物料、联系方、操作员。支持模糊匹配。

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
        fuzzy: 是否启用模糊匹配（默认 true）
        include_batches: 搜索物料时是否附带每个物料的批次列表（默认 false，仅 entity_type="material" 有效）
        max_results: 返回结果上限（0 表示使用配置默认值）

    返回:
        items: 匹配结果列表（include_batches=true 时每个物料含 batches 字段，
               每个批次含 variant 变体标识）
        total: 总匹配数（可能大于返回的 items 数量）
    """
    return _provider.search(query, entity_type, category, status, contact_type, fuzzy,
                            include_batches, max_results)


@mcp.tool()
def get_today_statistics() -> dict:
    """
    查询当天仓库统计概览。无需参数，直接调用即可。

    返回：今日入库量、出库量、库存总量、低库存数量、净变化量。
    适用于：「今天仓库情况怎么样」「今日出入库汇总」等问题。
    """
    return _provider.get_today_statistics()


# 启动服务器
if __name__ == "__main__":
    mcp.run(transport="stdio")
