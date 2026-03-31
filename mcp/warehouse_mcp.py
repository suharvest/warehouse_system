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
    config = {
        'api_base_url': 'http://localhost:2124/api',
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

_provider = load_provider(_config)

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
def stock_in(product_name: str, quantity: int, reason: str = "采购入库",
             operator: str = "MCP系统", fuzzy: bool = True,
             location: str = None, contact_id: int = None,
             variant: str = None) -> dict:
    """
    产品入库。可直接传入模糊名称，自动解析为精确产品。

    参数:
        product_name: 产品名称（支持模糊输入，如"螺丝"会自动匹配"M3螺丝"）
        quantity: 入库数量（正整数）
        reason: 入库原因（默认"采购入库"）
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
    return _provider.stock_in(product_name, quantity, reason, operator, fuzzy,
                              location, contact_id, variant)


@mcp.tool()
def stock_out(product_name: str, quantity: int, reason: str = "销售出库",
              operator: str = "MCP系统", fuzzy: bool = True,
              variant: str = None) -> dict:
    """
    产品出库。可直接传入模糊名称，自动解析为精确产品。
    默认按 FIFO 消耗批次；若指定 variant，则仅从匹配变体的批次中 FIFO 消耗。

    参数:
        product_name: 产品名称（支持模糊输入，如"螺丝"会自动匹配"M3螺丝"）
        quantity: 出库数量（正整数）
        reason: 出库原因（默认"销售出库"）
        operator: 操作人（默认"MCP系统"）
        fuzzy: 是否启用模糊匹配（默认 true）
        variant: 变体过滤（可选，如"红"）。指定后仅消耗该变体的批次。
                 例如"出库2个红色指示灯"时设为"红"。

    返回:
        success=true 时：出库成功，含批次消耗详情（每个消耗批次含 variant 字段）
        success=false 且有 candidates 时：名称不够明确，需用候选中的精确名称重试
    """
    return _provider.stock_out(product_name, quantity, reason, operator, fuzzy, variant)


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
