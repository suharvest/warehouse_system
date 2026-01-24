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
    contact_id: Optional[int] = None  # 联系方ID（供应商/客户）


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


class MissingSkuItem(BaseModel):
    """缺失的SKU项"""
    sku: str
    name: str
    category: str
    current_quantity: int


class ExcelImportPreviewResponse(BaseModel):
    """Excel导入预览响应"""
    success: bool
    preview: List[ImportPreviewItem]
    new_skus: List[ImportPreviewItem]
    missing_skus: List[MissingSkuItem] = []  # 系统中有但导入文件中没有的SKU
    total_in: int
    total_out: int
    total_new: int
    total_missing: int = 0  # 缺失SKU数量
    message: str


class ExcelImportConfirm(BaseModel):
    """Excel导入确认请求"""
    changes: List[ImportPreviewItem]
    operator: Optional[str] = None  # 可选，如不提供则使用当前登录用户
    reason: str
    confirm_new_skus: bool = False  # 是否确认创建新SKU
    confirm_disable_missing_skus: bool = False  # 是否确认禁用导入文件以外的SKU


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
    operator: Optional[str] = None  # 可选，如不提供则使用当前登录用户
    reason: str
    contact_id: Optional[int] = None  # 联系方ID（供应商/客户）


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
    operator: str  # 保留用于向后兼容（旧记录）
    operator_user_id: Optional[int] = None  # 关联用户ID
    operator_name: Optional[str] = None  # 从用户表获取的当前显示名称
    reason: Optional[str]
    created_at: str
    material_status: str  # 物料当前状态
    is_disabled: bool = False
    contact_id: Optional[int] = None
    contact_name: Optional[str] = None
    batch_id: Optional[int] = None
    batch_no: Optional[str] = None
    batch_details: Optional[str] = None  # 出库时的批次消耗详情


class PaginatedProductRecordsResponse(BaseModel):
    """产品进出库记录分页响应"""
    items: List[ProductRecord]
    page: int
    page_size: int
    total: int
    total_pages: int


# ============ Auth Models ============

class AuthStatusResponse(BaseModel):
    """认证状态响应"""
    initialized: bool  # 系统是否已初始化（有管理员）
    logged_in: bool
    user: Optional['UserInfo'] = None


class UserInfo(BaseModel):
    """用户信息"""
    id: int
    username: str
    display_name: Optional[str]
    role: str  # 'admin' | 'operate' | 'view'


class SetupRequest(BaseModel):
    """首次设置请求"""
    username: str
    password: str
    display_name: Optional[str] = None


class LoginRequest(BaseModel):
    """登录请求"""
    username: str
    password: str


class LoginResponse(BaseModel):
    """登录响应"""
    success: bool
    message: str
    user: Optional[UserInfo] = None


class CreateUserRequest(BaseModel):
    """创建用户请求"""
    username: str
    password: str
    display_name: Optional[str] = None
    role: str = 'view'  # 'admin' | 'operate' | 'view'


class UpdateUserRequest(BaseModel):
    """更新用户请求"""
    username: Optional[str] = None
    display_name: Optional[str] = None
    role: Optional[str] = None
    password: Optional[str] = None
    is_disabled: Optional[bool] = None


class UserListItem(BaseModel):
    """用户列表项"""
    id: int
    username: str
    display_name: Optional[str]
    role: str
    is_disabled: bool
    created_at: str


# ============ API Key Models ============

class CreateApiKeyRequest(BaseModel):
    """创建API密钥请求"""
    name: str
    role: str = 'operate'  # 'admin' | 'operate' | 'view'


class ApiKeyStatusRequest(BaseModel):
    """API密钥状态请求"""
    disabled: bool


class ApiKeyResponse(BaseModel):
    """API密钥响应（只在创建时返回完整密钥）"""
    id: int
    name: str
    role: str
    key: Optional[str] = None  # 只在创建时返回
    created_at: str
    last_used_at: Optional[str] = None


class ApiKeyListItem(BaseModel):
    """API密钥列表项"""
    id: int
    name: str
    role: str
    is_disabled: bool
    created_at: str
    last_used_at: Optional[str] = None


# ============ Contact Models ============

class ContactBase(BaseModel):
    """联系方基础信息"""
    name: str
    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    is_supplier: bool = False
    is_customer: bool = False
    notes: Optional[str] = None


class CreateContactRequest(ContactBase):
    """创建联系方请求"""
    pass


class UpdateContactRequest(BaseModel):
    """更新联系方请求"""
    name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    is_supplier: Optional[bool] = None
    is_customer: Optional[bool] = None
    notes: Optional[str] = None
    is_disabled: Optional[bool] = None


class ContactItem(BaseModel):
    """联系方项"""
    id: int
    name: str
    address: Optional[str]
    phone: Optional[str]
    email: Optional[str]
    is_supplier: bool
    is_customer: bool
    notes: Optional[str]
    is_disabled: bool
    created_at: str


class ContactListItem(BaseModel):
    """联系方列表项（简化版，用于下拉选择）"""
    id: int
    name: str
    is_supplier: bool
    is_customer: bool


class PaginatedContactsResponse(BaseModel):
    """联系方分页响应"""
    items: List[ContactItem]
    page: int
    page_size: int
    total: int
    total_pages: int


# ============ Batch Models ============

class BatchInfo(BaseModel):
    """批次信息（入库时返回）"""
    batch_no: str
    batch_id: int
    quantity: int


class BatchConsumption(BaseModel):
    """批次消耗详情（出库时返回）"""
    batch_no: str
    batch_id: int
    quantity: int  # 从该批次消耗的数量
    remaining: int  # 该批次剩余数量


class StockInResponse(BaseModel):
    """入库响应（含批次信息）"""
    success: bool
    operation: Optional[str] = None
    product: Optional[StockOperationProduct] = None
    batch: Optional[BatchInfo] = None
    message: str
    warning: Optional[str] = None
    error: Optional[str] = None


class StockOutResponse(BaseModel):
    """出库响应（含批次消耗详情）"""
    success: bool
    operation: Optional[str] = None
    product: Optional[StockOperationProduct] = None
    batch_consumptions: Optional[List[BatchConsumption]] = None
    message: str
    warning: Optional[str] = None
    error: Optional[str] = None


class BatchItem(BaseModel):
    """批次列表项"""
    id: int
    batch_no: str
    material_id: int
    material_name: str
    quantity: int
    initial_quantity: int
    contact_id: Optional[int] = None
    contact_name: Optional[str] = None
    is_exhausted: bool
    created_at: str


class OperatorListItem(BaseModel):
    """操作员列表项（用于筛选下拉）"""
    user_id: int
    username: str
    display_name: Optional[str]


# ============ Database Management Models ============

class DatabaseClearRequest(BaseModel):
    """清空数据库请求"""
    confirm: bool


class DatabaseOperationResponse(BaseModel):
    """数据库操作响应"""
    success: bool
    message: str
    details: Optional[dict] = None


# ============ MCP Connection Models ============

class CreateMCPConnectionRequest(BaseModel):
    """创建MCP连接请求"""
    name: str
    mcp_endpoint: str
    role: str = 'operate'  # 'admin' | 'operate' | 'view'
    auto_start: bool = True


class UpdateMCPConnectionRequest(BaseModel):
    """更新MCP连接请求"""
    name: Optional[str] = None
    mcp_endpoint: Optional[str] = None
    role: Optional[str] = None  # 'admin' | 'operate' | 'view'
    auto_start: Optional[bool] = None


class MCPConnectionItem(BaseModel):
    """MCP连接列表项"""
    id: str
    name: str
    mcp_endpoint: str
    role: str = 'operate'
    auto_start: bool
    status: str  # stopped | running | error
    error_message: Optional[str] = None
    restart_count: int = 0
    pid: Optional[int] = None
    uptime_seconds: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class MCPConnectionResponse(BaseModel):
    """MCP连接操作响应"""
    success: bool
    message: str
    connection: Optional[MCPConnectionItem] = None
