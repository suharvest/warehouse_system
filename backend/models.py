"""
Pydantic models for API request/response validation
"""
from pydantic import BaseModel
from typing import List, Optional


# ============ Dashboard Models ============

class DashboardStats(BaseModel):
    """仪表盘统计数据"""
    total_stock: int
    today_in: int
    today_out: int
    low_stock_count: int
    material_types: int
    in_change: float
    out_change: float


class CategoryItem(BaseModel):
    """类别分布项"""
    name: str
    value: int


class WeeklyTrend(BaseModel):
    """周趋势数据"""
    dates: List[str]
    in_data: List[int]
    out_data: List[int]


class TopStock(BaseModel):
    """库存TOP10"""
    names: List[str]
    quantities: List[int]
    categories: List[str]


class LowStockItem(BaseModel):
    """库存预警项"""
    name: str
    sku: str
    category: str
    quantity: int
    safe_stock: int
    location: str
    shortage: int


# ============ Material Models ============

class MaterialItem(BaseModel):
    """物料项"""
    name: str
    sku: str
    category: str
    quantity: int
    unit: str
    safe_stock: int
    location: str
    status: str
    status_text: str


class XiaozhiItem(BaseModel):
    """xiaozhi物料项"""
    name: str
    sku: str
    quantity: int
    unit: str
    category: str
    location: str


class ProductStats(BaseModel):
    """产品统计数据"""
    name: str
    sku: str
    current_stock: int
    unit: str
    safe_stock: int
    location: str
    today_in: int
    today_out: int
    in_change: float
    out_change: float
    total_in: int
    total_out: int


class ProductRecord(BaseModel):
    """产品出入库记录"""
    type: str
    quantity: int
    operator: str
    reason: Optional[str]
    created_at: str


# ============ Stock Operation Models ============

class StockOperationRequest(BaseModel):
    """入库/出库请求"""
    product_name: str
    quantity: int
    reason: Optional[str] = None
    operator: Optional[str] = "MCP系统"


class StockOperationProduct(BaseModel):
    """入库/出库操作中的产品信息"""
    name: str
    old_quantity: int
    in_quantity: Optional[int] = None
    out_quantity: Optional[int] = None
    new_quantity: int
    unit: str
    safe_stock: Optional[int] = None


class StockOperationResponse(BaseModel):
    """入库/出库响应"""
    success: bool
    operation: Optional[str] = None
    product: Optional[StockOperationProduct] = None
    message: str
    warning: Optional[str] = None
    error: Optional[str] = None
