"""
Pydantic models for API request/response validation
"""
from pydantic import BaseModel
from typing import List, Optional, Generic, TypeVar

T = TypeVar('T')


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


# ============ Excel Import/Export Models ============

class ImportPreviewItem(BaseModel):
    """导入预览项"""
    sku: str
    name: str
    category: Optional[str] = None
    unit: Optional[str] = None
    safe_stock: Optional[int] = None
    location: Optional[str] = None
    current_quantity: Optional[int] = None  # None表示新SKU
    import_quantity: int
    difference: int
    operation: str  # 'in' | 'out' | 'none' | 'new'
    is_new: bool = False


class ExcelImportPreviewResponse(BaseModel):
    """Excel导入预览响应"""
    success: bool
    preview: List[ImportPreviewItem]
    new_skus: List[ImportPreviewItem]
    total_in: int
    total_out: int
    total_new: int
    message: str


class ExcelImportConfirm(BaseModel):
    """Excel导入确认请求"""
    changes: List[ImportPreviewItem]
    operator: str
    reason: str
    confirm_new_skus: bool = False  # 是否确认创建新SKU


class ExcelImportResponse(BaseModel):
    """Excel导入响应"""
    success: bool
    in_count: int
    out_count: int
    new_count: int
    records_created: int
    message: str


class ManualRecordRequest(BaseModel):
    """手动新增出入库记录请求"""
    product_name: str
    type: str  # 'in' | 'out'
    quantity: int
    operator: str
    reason: str


# ============ Pagination Models ============

class PaginatedMaterialsResponse(BaseModel):
    """物料分页响应"""
    items: List['MaterialItemWithDisabled']
    page: int
    page_size: int
    total: int
    total_pages: int


class PaginatedRecordsResponse(BaseModel):
    """进出库记录分页响应"""
    items: List['InventoryRecordItem']
    page: int
    page_size: int
    total: int
    total_pages: int


class MaterialItemWithDisabled(BaseModel):
    """物料项（含禁用状态）"""
    name: str
    sku: str
    category: str
    quantity: int
    unit: str
    safe_stock: int
    location: str
    status: str  # 'normal' | 'warning' | 'danger' | 'disabled'
    status_text: str
    is_disabled: bool = False


class InventoryRecordItem(BaseModel):
    """进出库记录项"""
    id: int
    material_name: str
    material_sku: str
    category: str
    type: str  # 'in' | 'out'
    quantity: int
    operator: str
    reason: Optional[str]
    created_at: str
    material_status: str  # 物料当前状态
    is_disabled: bool = False


class PaginatedProductRecordsResponse(BaseModel):
    """产品进出库记录分页响应"""
    items: List[ProductRecord]
    page: int
    page_size: int
    total: int
    total_pages: int
