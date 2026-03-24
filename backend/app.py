"""
仓库管理系统 FastAPI 后端
"""
import os
import logging
from fastapi import FastAPI, Query, HTTPException, File, UploadFile, Request, Response, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware
from datetime import datetime, timedelta
from contextlib import contextmanager
from typing import List, Optional
from io import BytesIO
from functools import wraps

# 速率限制
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from database import (
    init_database, generate_mock_data, get_db_connection,
    has_admin_user, hash_password, verify_password,
    generate_session_token, generate_api_key, hash_api_key,
    generate_batch_no, needs_password_rehash
)
from models import (
    DashboardStats, CategoryItem, WeeklyTrend, TopStock, LowStockItem,
    MaterialItem, ProductStats, ProductRecord,
    StockOperationRequest, StockOperationResponse, StockOperationProduct,
    ImportPreviewItem, ExcelImportPreviewResponse, ExcelImportConfirm,
    ExcelImportResponse, ManualRecordRequest, MissingSkuItem,
    PaginatedMaterialsResponse, PaginatedRecordsResponse, MaterialItemWithDisabled,
    InventoryRecordItem, PaginatedProductRecordsResponse,
    # Auth models
    AuthStatusResponse, UserInfo, SetupRequest, LoginRequest, LoginResponse,
    CreateUserRequest, UpdateUserRequest, UserListItem,
    CreateApiKeyRequest, ApiKeyStatusRequest, ApiKeyResponse, ApiKeyListItem,
    # Contact models
    CreateContactRequest, UpdateContactRequest, ContactItem, ContactListItem,
    PaginatedContactsResponse,
    # Batch models
    BatchInfo, BatchConsumption, StockInResponse, StockOutResponse,
    # Operator model
    OperatorListItem,
    # Database management models
    DatabaseClearRequest, DatabaseOperationResponse,
    # MCP models
    CreateMCPConnectionRequest, UpdateMCPConnectionRequest,
    MCPConnectionItem, MCPConnectionResponse,
    # Fuzzy match models
    FuzzyMatchCandidate, FuzzyMatchResponse,
)
from fuzzy_match import FuzzyMatcher
import math
import uuid

# Excel处理
from openpyxl import Workbook, load_workbook

# MCP进程管理
from mcp_manager import MCPProcessManager

# ============================================
# 环境变量配置
# ============================================
# CORS配置：逗号分隔的域名列表，或 * 表示允许所有
CORS_ORIGINS = os.environ.get('CORS_ORIGINS', '*')
# 是否生成模拟数据（生产环境设为false）
INIT_MOCK_DATA = os.environ.get('INIT_MOCK_DATA', 'true').lower() == 'true'
# 是否启用安全响应头
ENABLE_SECURITY_HEADERS = os.environ.get('ENABLE_SECURITY_HEADERS', 'false').lower() == 'true'
# 是否启用审计日志
ENABLE_AUDIT_LOG = os.environ.get('ENABLE_AUDIT_LOG', 'true').lower() == 'true'
# Excel上传限制
MAX_UPLOAD_SIZE_MB = int(os.environ.get('MAX_UPLOAD_SIZE_MB', '10'))
MAX_IMPORT_ROWS = int(os.environ.get('MAX_IMPORT_ROWS', '10000'))
# 模糊匹配置信度阈值
FUZZY_CONFIDENT_SCORE = float(os.environ.get('FUZZY_CONFIDENT_SCORE', '85'))
FUZZY_CONFIDENT_GAP = float(os.environ.get('FUZZY_CONFIDENT_GAP', '10'))

# 配置日志
logging.basicConfig(
    level=os.environ.get('LOG_LEVEL', 'INFO').upper(),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('warehouse')

# ============================================
# 速率限制配置
# ============================================
limiter = Limiter(key_func=get_remote_address, enabled=os.environ.get('DISABLE_RATE_LIMIT', '0') != '1')

# 创建 FastAPI 应用
app = FastAPI(
    title="仓库管理系统 API",
    description="智能硬件仓库管理系统后端 API",
    version="2.0.0"
)

# 注册速率限制异常处理（带 CORS 头）
app.state.limiter = limiter

async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    """自定义速率限制异常处理器，确保响应包含 CORS 头"""
    from starlette.responses import JSONResponse
    origin = request.headers.get("origin", "*")
    response = JSONResponse(
        status_code=429,
        content={"detail": f"Rate limit exceeded: {exc.detail}"}
    )
    # 添加 CORS 头
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Credentials"] = "true"
    return response

app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

# ============================================
# 安全头中间件
# ============================================
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """添加安全响应头"""
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if ENABLE_SECURITY_HEADERS:
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['X-Frame-Options'] = 'DENY'
            response.headers['X-XSS-Protection'] = '1; mode=block'
            response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        return response

app.add_middleware(SecurityHeadersMiddleware)

# ============================================
# CORS 配置
# ============================================
def get_cors_origins():
    """解析CORS配置"""
    if CORS_ORIGINS == '*':
        return ['*']
    return [origin.strip() for origin in CORS_ORIGINS.split(',') if origin.strip()]

cors_origins = get_cors_origins()

# 自定义 CORS 中间件：正确处理通配符和 credentials
class DynamicCORSMiddleware(BaseHTTPMiddleware):
    """
    动态 CORS 中间件，解决以下问题：
    1. 当 allow_origins=['*'] 时，自动将 Access-Control-Allow-Origin 设为请求的 Origin
    2. 确保 credentials 模式下不返回通配符
    3. 正确处理预检请求（OPTIONS）
    """
    async def dispatch(self, request: Request, call_next):
        origin = request.headers.get("origin")

        # 处理预检请求
        if request.method == "OPTIONS":
            response = Response(status_code=204)
        else:
            response = await call_next(request)

        # 设置 CORS 头
        if origin:
            if CORS_ORIGINS == '*':
                # 通配符模式：使用请求的 Origin
                response.headers["Access-Control-Allow-Origin"] = origin
            elif origin in cors_origins:
                # 明确列表模式：只允许列表中的 Origin
                response.headers["Access-Control-Allow-Origin"] = origin
            else:
                # Origin 不在允许列表中，不设置 CORS 头（浏览器会拒绝）
                pass

        # 设置其他 CORS 头
        if "Access-Control-Allow-Origin" in response.headers:
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, PATCH"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-API-Key"
            response.headers["Access-Control-Max-Age"] = "86400"

        return response

# 使用自定义 CORS 中间件替代 FastAPI 的 CORSMiddleware
app.add_middleware(DynamicCORSMiddleware)

# ============================================
# 审计日志函数
# ============================================
def audit_log(action: str, user_id: int = None, username: str = None, details: dict = None):
    """记录审计日志"""
    if not ENABLE_AUDIT_LOG:
        return
    log_data = {
        "action": action,
        "user_id": user_id,
        "username": username,
        "timestamp": datetime.now().isoformat(),
        "details": details or {}
    }
    logger.info(f"AUDIT: {action} | user={username}({user_id}) | {details}")

# ============================================
# 初始化数据库
# ============================================
init_database()
if INIT_MOCK_DATA:
    generate_mock_data()


# 数据库连接上下文管理器
@contextmanager
def get_db():
    conn = get_db_connection()
    try:
        yield conn
    finally:
        conn.close()


# FuzzyMatcher 全局实例
def get_fuzzy_matcher() -> FuzzyMatcher:
    """获取或创建 FuzzyMatcher 实例"""
    if not hasattr(app.state, 'fuzzy_matcher'):
        app.state.fuzzy_matcher = FuzzyMatcher(
            get_db_connection,
            confident_score=FUZZY_CONFIDENT_SCORE,
            confident_gap=FUZZY_CONFIDENT_GAP,
        )
    return app.state.fuzzy_matcher


# 自定义异常处理（保持响应格式兼容）
@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail}
    )


# ============ 认证相关 ============

# 权限级别映射（数字越大权限越高）
ROLE_LEVELS = {
    'view': 1,
    'operate': 2,
    'admin': 3
}


class CurrentUser:
    """当前用户信息"""
    def __init__(self, user_id: int = None, username: str = None,
                 display_name: str = None, role: str = 'view',
                 is_guest: bool = True, source: str = 'guest'):
        self.id = user_id
        self.username = username
        self.display_name = display_name
        self.role = role
        self.is_guest = is_guest
        self.source = source  # 'session' | 'api_key' | 'guest'

    def has_permission(self, min_role: str) -> bool:
        """检查是否有最低权限"""
        return ROLE_LEVELS.get(self.role, 0) >= ROLE_LEVELS.get(min_role, 0)

    def get_operator_name(self) -> str:
        """获取操作人名称"""
        if self.display_name:
            return self.display_name
        if self.username:
            return self.username
        return "访客"


async def get_current_user(request: Request) -> CurrentUser:
    """
    获取当前用户（认证中间件）
    优先级：X-API-Key > session_token Cookie > 访客
    """
    with get_db() as conn:
        cursor = conn.cursor()

        # 1. 检查 X-API-Key Header
        api_key = request.headers.get('X-API-Key')
        if api_key:
            key_hash = hash_api_key(api_key)
            cursor.execute('''
                SELECT ak.id, ak.name, ak.role, ak.user_id, u.username, u.display_name
                FROM api_keys ak
                LEFT JOIN users u ON ak.user_id = u.id
                WHERE ak.key_hash = ? AND ak.is_disabled = 0
            ''', (key_hash,))
            key_row = cursor.fetchone()

            if key_row:
                # 更新最后使用时间
                cursor.execute(
                    'UPDATE api_keys SET last_used_at = ? WHERE id = ?',
                    (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), key_row['id'])
                )
                conn.commit()

                # 使用关联用户名或API Key名称
                display_name = key_row['display_name'] or key_row['username'] or key_row['name']
                return CurrentUser(
                    user_id=key_row['user_id'],
                    username=key_row['username'] or key_row['name'],
                    display_name=display_name,
                    role=key_row['role'],
                    is_guest=False,
                    source='api_key'
                )

        # 2. 检查 session_token Cookie
        session_token = request.cookies.get('session_token')
        if session_token:
            cursor.execute('''
                SELECT s.user_id, s.expires_at, u.username, u.display_name, u.role
                FROM sessions s
                JOIN users u ON s.user_id = u.id
                WHERE s.token = ? AND u.is_disabled = 0
            ''', (session_token,))
            session_row = cursor.fetchone()

            if session_row:
                # 检查是否过期
                expires_at = datetime.strptime(session_row['expires_at'], '%Y-%m-%d %H:%M:%S')
                if expires_at > datetime.now():
                    return CurrentUser(
                        user_id=session_row['user_id'],
                        username=session_row['username'],
                        display_name=session_row['display_name'],
                        role=session_row['role'],
                        is_guest=False,
                        source='session'
                    )

        # 3. 访客模式
        return CurrentUser()


def require_auth(min_role: str = 'view'):
    """
    权限检查装饰器
    - view: 只读访问
    - operate: 入库/出库/导入/导出/管理联系方
    - admin: 用户管理
    """
    async def dependency(current_user: CurrentUser = Depends(get_current_user)):
        if not current_user.has_permission(min_role):
            if current_user.is_guest:
                raise HTTPException(status_code=401, detail="请先登录")
            else:
                raise HTTPException(status_code=403, detail="权限不足")
        return current_user
    return dependency


# ============ Auth APIs ============

@app.get("/api/auth/status", response_model=AuthStatusResponse)
async def get_auth_status(current_user: CurrentUser = Depends(get_current_user)):
    """获取认证状态"""
    initialized = has_admin_user()

    if current_user.is_guest:
        return AuthStatusResponse(
            initialized=initialized,
            logged_in=False,
            user=None
        )

    return AuthStatusResponse(
        initialized=initialized,
        logged_in=True,
        user=UserInfo(
            id=current_user.id,
            username=current_user.username,
            display_name=current_user.display_name,
            role=current_user.role
        )
    )


@app.post("/api/auth/setup", response_model=LoginResponse)
async def setup_admin(request: SetupRequest, response: Response):
    """首次设置管理员账号"""
    if has_admin_user():
        raise HTTPException(status_code=400, detail="系统已初始化，无法重复设置")

    if len(request.password) < 4:
        raise HTTPException(status_code=400, detail="密码长度至少4位")

    with get_db() as conn:
        cursor = conn.cursor()

        # 创建管理员
        password_hash = hash_password(request.password)
        cursor.execute('''
            INSERT INTO users (username, password_hash, role, display_name, created_at)
            VALUES (?, ?, 'admin', ?, ?)
        ''', (request.username, password_hash, request.display_name,
              datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

        user_id = cursor.lastrowid

        # 创建会话
        token = generate_session_token()
        expires_at = datetime.now() + timedelta(hours=24)
        cursor.execute('''
            INSERT INTO sessions (user_id, token, expires_at, created_at)
            VALUES (?, ?, ?, ?)
        ''', (user_id, token, expires_at.strftime('%Y-%m-%d %H:%M:%S'),
              datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

        conn.commit()

        # 设置Cookie
        response.set_cookie(
            key="session_token",
            value=token,
            max_age=86400,  # 24小时
            httponly=True,
            samesite="lax"
        )

        return LoginResponse(
            success=True,
            message="管理员账号创建成功",
            user=UserInfo(
                id=user_id,
                username=request.username,
                display_name=request.display_name,
                role='admin'
            )
        )


@app.post("/api/auth/login", response_model=LoginResponse)
@limiter.limit("5/minute")  # 登录接口速率限制：每分钟5次
async def login(request: Request, login_data: LoginRequest, response: Response):
    """用户登录"""
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('''
            SELECT id, username, password_hash, display_name, role, is_disabled
            FROM users WHERE username = ?
        ''', (login_data.username,))
        user = cursor.fetchone()

        if not user:
            return LoginResponse(success=False, message="用户名或密码错误")

        if user['is_disabled']:
            return LoginResponse(success=False, message="账号已被禁用")

        if not verify_password(login_data.password, user['password_hash']):
            return LoginResponse(success=False, message="用户名或密码错误")

        # 透明密码升级：如果使用旧的SHA256哈希，自动升级到bcrypt
        if needs_password_rehash(user['password_hash']):
            new_hash = hash_password(login_data.password)
            cursor.execute('UPDATE users SET password_hash = ? WHERE id = ?',
                          (new_hash, user['id']))
            logger.info(f"Password upgraded to bcrypt for user: {user['username']}")

        # 清理旧会话
        cursor.execute('DELETE FROM sessions WHERE user_id = ?', (user['id'],))

        # 创建新会话
        token = generate_session_token()
        expires_at = datetime.now() + timedelta(hours=24)
        cursor.execute('''
            INSERT INTO sessions (user_id, token, expires_at, created_at)
            VALUES (?, ?, ?, ?)
        ''', (user['id'], token, expires_at.strftime('%Y-%m-%d %H:%M:%S'),
              datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

        conn.commit()

        # 审计日志
        audit_log("LOGIN", user['id'], user['username'], {"role": user['role']})

        # 设置Cookie
        response.set_cookie(
            key="session_token",
            value=token,
            max_age=86400,
            httponly=True,
            samesite="lax"
        )

        return LoginResponse(
            success=True,
            message="登录成功",
            user=UserInfo(
                id=user['id'],
                username=user['username'],
                display_name=user['display_name'],
                role=user['role']
            )
        )


@app.post("/api/auth/logout")
async def logout(response: Response, current_user: CurrentUser = Depends(get_current_user)):
    """用户登出"""
    if current_user.source == 'session' and current_user.id:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM sessions WHERE user_id = ?', (current_user.id,))
            conn.commit()

    response.delete_cookie("session_token")
    return {"success": True, "message": "已登出"}


@app.get("/api/auth/me", response_model=UserInfo)
async def get_current_user_info(current_user: CurrentUser = Depends(require_auth('view'))):
    """获取当前用户信息"""
    if current_user.is_guest:
        raise HTTPException(status_code=401, detail="未登录")

    return UserInfo(
        id=current_user.id,
        username=current_user.username,
        display_name=current_user.display_name,
        role=current_user.role
    )


# ============ User Management APIs ============

@app.get("/api/users", response_model=List[UserListItem])
async def list_users(current_user: CurrentUser = Depends(require_auth('admin'))):
    """获取用户列表（仅管理员）"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, username, display_name, role, is_disabled, created_at
            FROM users ORDER BY created_at DESC
        ''')

        return [
            UserListItem(
                id=row['id'],
                username=row['username'],
                display_name=row['display_name'],
                role=row['role'],
                is_disabled=bool(row['is_disabled']),
                created_at=row['created_at']
            )
            for row in cursor.fetchall()
        ]


@app.post("/api/users", response_model=UserListItem)
async def create_user(
    request: CreateUserRequest,
    current_user: CurrentUser = Depends(require_auth('admin'))
):
    """创建用户（仅管理员）"""
    if request.role not in ['admin', 'operate', 'view']:
        raise HTTPException(status_code=400, detail="无效的角色")

    if len(request.password) < 4:
        raise HTTPException(status_code=400, detail="密码长度至少4位")

    with get_db() as conn:
        cursor = conn.cursor()

        # 检查用户名是否已存在
        cursor.execute('SELECT id FROM users WHERE username = ?', (request.username,))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="用户名已存在")

        password_hash = hash_password(request.password)
        created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        cursor.execute('''
            INSERT INTO users (username, password_hash, role, display_name, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (request.username, password_hash, request.role,
              request.display_name, current_user.id, created_at))

        user_id = cursor.lastrowid
        conn.commit()
        get_fuzzy_matcher().invalidate_cache()

        return UserListItem(
            id=user_id,
            username=request.username,
            display_name=request.display_name,
            role=request.role,
            is_disabled=False,
            created_at=created_at
        )


@app.put("/api/users/{user_id}", response_model=UserListItem)
async def update_user(
    user_id: int,
    request: UpdateUserRequest,
    current_user: CurrentUser = Depends(require_auth('admin'))
):
    """更新用户（仅管理员）"""
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
        user = cursor.fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")

        updates = []
        params = []

        if request.username is not None:
            # 检查用户名是否已被占用
            cursor.execute('SELECT id FROM users WHERE username = ? AND id != ?', (request.username, user_id))
            if cursor.fetchone():
                raise HTTPException(status_code=400, detail="用户名已存在")
            if len(request.username) < 2:
                raise HTTPException(status_code=400, detail="用户名长度至少2位")
            updates.append('username = ?')
            params.append(request.username)

        if request.display_name is not None:
            updates.append('display_name = ?')
            params.append(request.display_name)

        if request.role is not None:
            if request.role not in ['admin', 'operate', 'view']:
                raise HTTPException(status_code=400, detail="无效的角色")
            updates.append('role = ?')
            params.append(request.role)

        if request.password is not None:
            if len(request.password) < 4:
                raise HTTPException(status_code=400, detail="密码长度至少4位")
            updates.append('password_hash = ?')
            params.append(hash_password(request.password))

        if request.is_disabled is not None:
            updates.append('is_disabled = ?')
            params.append(1 if request.is_disabled else 0)

        if updates:
            params.append(user_id)
            cursor.execute(f'''
                UPDATE users SET {', '.join(updates)} WHERE id = ?
            ''', params)
            conn.commit()
            get_fuzzy_matcher().invalidate_cache()

        cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
        updated = cursor.fetchone()

        return UserListItem(
            id=updated['id'],
            username=updated['username'],
            display_name=updated['display_name'],
            role=updated['role'],
            is_disabled=bool(updated['is_disabled']),
            created_at=updated['created_at']
        )


@app.delete("/api/users/{user_id}")
async def delete_user(
    user_id: int,
    current_user: CurrentUser = Depends(require_auth('admin'))
):
    """禁用用户（仅管理员）"""
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="不能禁用自己")

    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="用户不存在")

        cursor.execute('UPDATE users SET is_disabled = 1 WHERE id = ?', (user_id,))
        cursor.execute('DELETE FROM sessions WHERE user_id = ?', (user_id,))
        conn.commit()
        get_fuzzy_matcher().invalidate_cache()

        return {"success": True, "message": "用户已禁用"}


# ============ API Key Management APIs ============

@app.get("/api/api-keys", response_model=List[ApiKeyListItem])
async def list_api_keys(current_user: CurrentUser = Depends(require_auth('admin'))):
    """获取API密钥列表（仅管理员）"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, name, role, is_disabled, created_at, last_used_at
            FROM api_keys WHERE is_system = 0 ORDER BY created_at DESC
        ''')

        return [
            ApiKeyListItem(
                id=row['id'],
                name=row['name'],
                role=row['role'],
                is_disabled=bool(row['is_disabled']),
                created_at=row['created_at'],
                last_used_at=row['last_used_at']
            )
            for row in cursor.fetchall()
        ]


@app.post("/api/api-keys", response_model=ApiKeyResponse)
async def create_api_key(
    request: CreateApiKeyRequest,
    current_user: CurrentUser = Depends(require_auth('admin'))
):
    """创建API密钥（仅管理员）"""
    if request.role not in ['admin', 'operate', 'view']:
        raise HTTPException(status_code=400, detail="无效的角色")

    api_key = generate_api_key()
    key_hash = hash_api_key(api_key)
    created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO api_keys (key_hash, name, role, user_id, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (key_hash, request.name, request.role, current_user.id, created_at))

        key_id = cursor.lastrowid
        conn.commit()

        return ApiKeyResponse(
            id=key_id,
            name=request.name,
            role=request.role,
            key=api_key,  # 只在创建时返回完整密钥
            created_at=created_at
        )


@app.delete("/api/api-keys/{key_id}")
async def delete_api_key(
    key_id: int,
    current_user: CurrentUser = Depends(require_auth('admin'))
):
    """删除API密钥（仅管理员）"""
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM api_keys WHERE id = ?', (key_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="API密钥不存在")

        cursor.execute('DELETE FROM api_keys WHERE id = ?', (key_id,))
        conn.commit()

        return {"success": True, "message": "API密钥已删除"}


@app.put("/api/api-keys/{key_id}/status")
async def toggle_api_key_status(
    key_id: int,
    request: ApiKeyStatusRequest,
    current_user: CurrentUser = Depends(require_auth('admin'))
):
    """切换API密钥状态（仅管理员）"""
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM api_keys WHERE id = ?', (key_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="API密钥不存在")

        cursor.execute('UPDATE api_keys SET is_disabled = ? WHERE id = ?',
                      (1 if request.disabled else 0, key_id))
        conn.commit()

        status_text = "已禁用" if request.disabled else "已启用"
        return {"success": True, "message": f"API密钥{status_text}"}


# ============ Database Management APIs ============

# 仓库相关表（导出/导入/清空时操作）
# 顺序很重要：先无依赖的表，再有外键依赖的表
# materials, contacts -> batches -> inventory_records -> batch_consumptions
WAREHOUSE_TABLES = ['materials', 'contacts', 'batches', 'inventory_records', 'batch_consumptions']


@app.get("/api/database/export")
def export_database(current_user: CurrentUser = Depends(require_auth('admin'))):
    """导出仓库数据为SQLite数据库文件（仅管理员）

    只导出仓库相关表：materials, inventory_records, batches, batch_consumptions, contacts
    不导出用户相关表：users, sessions, api_keys
    """
    import tempfile
    import sqlite3
    import shutil

    # 获取当前数据库路径
    db_path = os.environ.get('DATABASE_PATH', 'warehouse.db')

    # 创建临时文件
    temp_fd, temp_path = tempfile.mkstemp(suffix='.db')
    os.close(temp_fd)

    try:
        # 创建新的临时数据库
        temp_conn = sqlite3.connect(temp_path)
        temp_cursor = temp_conn.cursor()

        with get_db() as source_conn:
            source_cursor = source_conn.cursor()

            for table in WAREHOUSE_TABLES:
                # 获取表结构
                source_cursor.execute(f"SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,))
                result = source_cursor.fetchone()
                if result and result['sql']:
                    # 创建表
                    temp_cursor.execute(result['sql'])

                    # 复制数据
                    source_cursor.execute(f"SELECT * FROM {table}")
                    rows = source_cursor.fetchall()
                    if rows:
                        columns = [desc[0] for desc in source_cursor.description]
                        placeholders = ','.join(['?' for _ in columns])
                        insert_sql = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
                        for row in rows:
                            temp_cursor.execute(insert_sql, tuple(row[col] for col in columns))

        temp_conn.commit()
        temp_conn.close()

        # 读取临时文件内容
        with open(temp_path, 'rb') as f:
            db_content = f.read()

        # 创建 BytesIO 对象用于流式响应
        output = BytesIO(db_content)
        output.seek(0)

        filename = f"warehouse_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"

        if ENABLE_AUDIT_LOG:
            logger.info(f"[AUDIT] 用户 {current_user.username or 'unknown'} 导出了数据库")

        return StreamingResponse(
            output,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    finally:
        # 清理临时文件
        if os.path.exists(temp_path):
            os.unlink(temp_path)


@app.post("/api/database/import", response_model=DatabaseOperationResponse)
@limiter.limit("5/minute")
async def import_database(
    request: Request,
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(require_auth('admin'))
):
    """导入仓库数据（仅管理员）

    从上传的SQLite数据库文件中导入仓库相关表的数据。
    会清空现有仓库数据后再导入。
    不影响用户相关表：users, sessions, api_keys
    """
    import tempfile
    import sqlite3

    # 读取上传的文件
    contents = await file.read()

    # 检查文件大小
    file_size_mb = len(contents) / (1024 * 1024)
    if file_size_mb > MAX_UPLOAD_SIZE_MB:
        raise HTTPException(status_code=400, detail=f"文件过大，最大允许 {MAX_UPLOAD_SIZE_MB}MB")

    # 保存到临时文件
    temp_fd, temp_path = tempfile.mkstemp(suffix='.db')
    try:
        os.write(temp_fd, contents)
        os.close(temp_fd)

        # 验证是否为有效的SQLite数据库
        try:
            import_conn = sqlite3.connect(temp_path)
            import_conn.row_factory = sqlite3.Row
            import_cursor = import_conn.cursor()

            # 检查必要的表是否存在
            import_cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            available_tables = {row[0] for row in import_cursor.fetchall()}

            # 至少需要 materials 表
            if 'materials' not in available_tables:
                raise HTTPException(status_code=400, detail="无效的数据库文件：缺少 materials 表")

        except sqlite3.DatabaseError:
            raise HTTPException(status_code=400, detail="无效的数据库文件格式")

        # 开始导入
        details = {}

        with get_db() as conn:
            cursor = conn.cursor()

            try:
                # 按外键顺序清空现有数据
                for table in reversed(WAREHOUSE_TABLES):
                    cursor.execute(f"DELETE FROM {table}")

                # 按顺序导入数据
                for table in WAREHOUSE_TABLES:
                    if table not in available_tables:
                        details[table] = 0
                        continue

                    # 获取源表的数据
                    import_cursor.execute(f"SELECT * FROM {table}")
                    rows = import_cursor.fetchall()

                    if rows:
                        # 获取目标表的列名
                        cursor.execute(f"PRAGMA table_info({table})")
                        target_columns = {row['name'] for row in cursor.fetchall()}

                        # 获取源表的列名
                        source_columns = [desc[0] for desc in import_cursor.description]

                        # 只使用目标表中存在的列
                        common_columns = [col for col in source_columns if col in target_columns]

                        if common_columns:
                            placeholders = ','.join(['?' for _ in common_columns])
                            insert_sql = f"INSERT INTO {table} ({','.join(common_columns)}) VALUES ({placeholders})"

                            for row in rows:
                                values = [row[col] for col in common_columns]
                                cursor.execute(insert_sql, values)

                    details[table] = len(rows)

                conn.commit()

            except Exception as e:
                conn.rollback()
                import traceback
                logger.error(f"[ERROR] 数据库导入失败: {str(e)}")
                logger.error(traceback.format_exc())
                raise HTTPException(status_code=500, detail=f"导入失败: {str(e)}")

        import_conn.close()

        if ENABLE_AUDIT_LOG:
            logger.info(f"[AUDIT] 用户 {current_user.username or 'unknown'} 导入了数据库")

        message = f"导入成功：{details.get('materials', 0)} 物料，{details.get('inventory_records', 0)} 记录，{details.get('batches', 0)} 批次，{details.get('contacts', 0)} 联系方"

        return DatabaseOperationResponse(
            success=True,
            message=message,
            details=details
        )

    finally:
        # 清理临时文件
        if os.path.exists(temp_path):
            os.unlink(temp_path)


@app.post("/api/database/clear", response_model=DatabaseOperationResponse)
async def clear_database(
    request: DatabaseClearRequest,
    current_user: CurrentUser = Depends(require_auth('admin'))
):
    """清空仓库数据（仅管理员）

    清空仓库相关表：materials, inventory_records, batches, batch_consumptions, contacts
    不影响用户相关表：users, sessions, api_keys
    """
    if not request.confirm:
        raise HTTPException(status_code=400, detail="请确认清空操作")

    details = {}

    with get_db() as conn:
        cursor = conn.cursor()

        try:
            # 获取每个表的记录数（清空前）
            for table in WAREHOUSE_TABLES:
                cursor.execute(f"SELECT COUNT(*) as count FROM {table}")
                details[table] = cursor.fetchone()['count']

            # 按外键顺序删除
            for table in reversed(WAREHOUSE_TABLES):
                cursor.execute(f"DELETE FROM {table}")

            conn.commit()

        except Exception as e:
            conn.rollback()
            raise HTTPException(status_code=500, detail=f"清空失败: {str(e)}")

    if ENABLE_AUDIT_LOG:
        logger.info(f"[AUDIT] 用户 {current_user.username or 'unknown'} 清空了数据库")

    message = f"已清空：{details.get('materials', 0)} 物料，{details.get('inventory_records', 0)} 记录，{details.get('batches', 0)} 批次，{details.get('contacts', 0)} 联系方"

    return DatabaseOperationResponse(
        success=True,
        message=message,
        details=details
    )


# ============ Contact Management APIs ============

@app.get("/api/contacts")
async def list_contacts(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=10, le=100, description="每页条数"),
    name: Optional[str] = Query(None, description="名称模糊搜索"),
    contact_type: Optional[str] = Query(None, description="类型: supplier/customer/all"),
    include_disabled: bool = Query(False, description="是否包含禁用的联系方"),
    format: Optional[str] = Query(None, description="brief时精简返回"),
):
    """获取联系方列表（分页）"""
    with get_db() as conn:
        cursor = conn.cursor()

        base_query = '''
            SELECT id, name, address, phone, email, is_supplier, is_customer,
                   notes, is_disabled, created_at
            FROM contacts
            WHERE 1=1
        '''
        count_query = 'SELECT COUNT(*) as total FROM contacts WHERE 1=1'
        params = []

        if not include_disabled:
            base_query += ' AND is_disabled = 0'
            count_query += ' AND is_disabled = 0'

        if name:
            base_query += ' AND name LIKE ?'
            count_query += ' AND name LIKE ?'
            params.append(f'%{name}%')

        if contact_type == 'supplier':
            base_query += ' AND is_supplier = 1'
            count_query += ' AND is_supplier = 1'
        elif contact_type == 'customer':
            base_query += ' AND is_customer = 1'
            count_query += ' AND is_customer = 1'

        cursor.execute(count_query, params)
        total = cursor.fetchone()['total']

        base_query += ' ORDER BY name ASC LIMIT ? OFFSET ?'
        offset = (page - 1) * page_size
        params.extend([page_size, offset])

        cursor.execute(base_query, params)
        rows = cursor.fetchall()

        if format == "brief":
            items = [{"id": row['id'], "name": row['name']} for row in rows]
        else:
            items = [
                ContactItem(
                    id=row['id'],
                    name=row['name'],
                    address=row['address'],
                    phone=row['phone'],
                    email=row['email'],
                    is_supplier=bool(row['is_supplier']),
                    is_customer=bool(row['is_customer']),
                    notes=row['notes'],
                    is_disabled=bool(row['is_disabled']),
                    created_at=row['created_at']
                )
                for row in rows
            ]

        total_pages = math.ceil(total / page_size) if total > 0 else 1

        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
        }


@app.get("/api/contacts/suppliers", response_model=List[ContactListItem])
async def list_suppliers():
    """获取供应商列表（用于下拉选择）"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, name, is_supplier, is_customer
            FROM contacts
            WHERE is_supplier = 1 AND is_disabled = 0
            ORDER BY name ASC
        ''')
        return [
            ContactListItem(
                id=row['id'],
                name=row['name'],
                is_supplier=bool(row['is_supplier']),
                is_customer=bool(row['is_customer'])
            )
            for row in cursor.fetchall()
        ]


@app.get("/api/contacts/customers", response_model=List[ContactListItem])
async def list_customers():
    """获取客户列表（用于下拉选择）"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, name, is_supplier, is_customer
            FROM contacts
            WHERE is_customer = 1 AND is_disabled = 0
            ORDER BY name ASC
        ''')
        return [
            ContactListItem(
                id=row['id'],
                name=row['name'],
                is_supplier=bool(row['is_supplier']),
                is_customer=bool(row['is_customer'])
            )
            for row in cursor.fetchall()
        ]


@app.get("/api/operators", response_model=List[OperatorListItem])
async def get_operators_for_filter():
    """获取操作员列表（用于筛选下拉）- 返回所有有操作权限的用户"""
    with get_db() as conn:
        cursor = conn.cursor()
        # 获取所有有操作权限的用户（operate或admin角色）
        cursor.execute('''
            SELECT id as user_id, username, display_name
            FROM users
            WHERE is_disabled = 0 AND role IN ('operate', 'admin')
            ORDER BY display_name, username
        ''')
        return [
            OperatorListItem(
                user_id=row['user_id'],
                username=row['username'],
                display_name=row['display_name']
            )
            for row in cursor.fetchall()
        ]


@app.get("/api/contacts/{contact_id}", response_model=ContactItem)
async def get_contact(contact_id: int):
    """获取单个联系方详情"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, name, address, phone, email, is_supplier, is_customer,
                   notes, is_disabled, created_at
            FROM contacts WHERE id = ?
        ''', (contact_id,))
        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="联系方不存在")

        return ContactItem(
            id=row['id'],
            name=row['name'],
            address=row['address'],
            phone=row['phone'],
            email=row['email'],
            is_supplier=bool(row['is_supplier']),
            is_customer=bool(row['is_customer']),
            notes=row['notes'],
            is_disabled=bool(row['is_disabled']),
            created_at=row['created_at']
        )


@app.post("/api/contacts", response_model=ContactItem)
async def create_contact(
    request: CreateContactRequest,
    current_user: CurrentUser = Depends(require_auth('operate'))
):
    """创建联系方（需要operate权限）"""
    if not request.is_supplier and not request.is_customer:
        raise HTTPException(status_code=400, detail="必须选择供应商或客户至少一项")

    with get_db() as conn:
        cursor = conn.cursor()
        created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        cursor.execute('''
            INSERT INTO contacts (name, address, phone, email, is_supplier, is_customer, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            request.name,
            request.address,
            request.phone,
            request.email,
            1 if request.is_supplier else 0,
            1 if request.is_customer else 0,
            request.notes,
            created_at
        ))

        contact_id = cursor.lastrowid
        conn.commit()
        get_fuzzy_matcher().invalidate_cache()

        return ContactItem(
            id=contact_id,
            name=request.name,
            address=request.address,
            phone=request.phone,
            email=request.email,
            is_supplier=request.is_supplier,
            is_customer=request.is_customer,
            notes=request.notes,
            is_disabled=False,
            created_at=created_at
        )


@app.put("/api/contacts/{contact_id}", response_model=ContactItem)
async def update_contact(
    contact_id: int,
    request: UpdateContactRequest,
    current_user: CurrentUser = Depends(require_auth('operate'))
):
    """更新联系方（需要operate权限）"""
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM contacts WHERE id = ?', (contact_id,))
        contact = cursor.fetchone()
        if not contact:
            raise HTTPException(status_code=404, detail="联系方不存在")

        updates = []
        params = []

        if request.name is not None:
            updates.append('name = ?')
            params.append(request.name)
        if request.address is not None:
            updates.append('address = ?')
            params.append(request.address)
        if request.phone is not None:
            updates.append('phone = ?')
            params.append(request.phone)
        if request.email is not None:
            updates.append('email = ?')
            params.append(request.email)
        if request.is_supplier is not None:
            updates.append('is_supplier = ?')
            params.append(1 if request.is_supplier else 0)
        if request.is_customer is not None:
            updates.append('is_customer = ?')
            params.append(1 if request.is_customer else 0)
        if request.notes is not None:
            updates.append('notes = ?')
            params.append(request.notes)
        if request.is_disabled is not None:
            updates.append('is_disabled = ?')
            params.append(1 if request.is_disabled else 0)

        if updates:
            params.append(contact_id)
            cursor.execute(f'''
                UPDATE contacts SET {', '.join(updates)} WHERE id = ?
            ''', params)
            conn.commit()
            get_fuzzy_matcher().invalidate_cache()

        cursor.execute('''
            SELECT id, name, address, phone, email, is_supplier, is_customer,
                   notes, is_disabled, created_at
            FROM contacts WHERE id = ?
        ''', (contact_id,))
        updated = cursor.fetchone()

        return ContactItem(
            id=updated['id'],
            name=updated['name'],
            address=updated['address'],
            phone=updated['phone'],
            email=updated['email'],
            is_supplier=bool(updated['is_supplier']),
            is_customer=bool(updated['is_customer']),
            notes=updated['notes'],
            is_disabled=bool(updated['is_disabled']),
            created_at=updated['created_at']
        )


@app.delete("/api/contacts/{contact_id}")
async def delete_contact(
    contact_id: int,
    current_user: CurrentUser = Depends(require_auth('operate'))
):
    """禁用联系方（需要operate权限）"""
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM contacts WHERE id = ?', (contact_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="联系方不存在")

        cursor.execute('UPDATE contacts SET is_disabled = 1 WHERE id = ?', (contact_id,))
        conn.commit()
        get_fuzzy_matcher().invalidate_cache()

        return {"success": True, "message": "联系方已禁用"}


# ============ Dashboard APIs ============

@app.get("/api/dashboard/stats", response_model=DashboardStats)
def get_dashboard_stats():
    """获取仪表盘统计数据（排除禁用物料）"""
    with get_db() as conn:
        cursor = conn.cursor()

        # 库存总量（排除禁用）
        cursor.execute('SELECT SUM(quantity) as total FROM materials WHERE is_disabled = 0')
        total_stock = cursor.fetchone()['total'] or 0

        # 今日入库量（排除禁用物料的记录）
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        cursor.execute('''
            SELECT SUM(r.quantity) as total
            FROM inventory_records r
            JOIN materials m ON r.material_id = m.id
            WHERE r.type = 'in' AND r.created_at >= ? AND m.is_disabled = 0
        ''', (today_start.strftime('%Y-%m-%d %H:%M:%S'),))
        today_in = cursor.fetchone()['total'] or 0

        # 今日出库量（排除禁用物料的记录）
        cursor.execute('''
            SELECT SUM(r.quantity) as total
            FROM inventory_records r
            JOIN materials m ON r.material_id = m.id
            WHERE r.type = 'out' AND r.created_at >= ? AND m.is_disabled = 0
        ''', (today_start.strftime('%Y-%m-%d %H:%M:%S'),))
        today_out = cursor.fetchone()['total'] or 0

        # 库存预警（低于安全库存，排除禁用）
        cursor.execute('''
            SELECT COUNT(*) as count
            FROM materials
            WHERE quantity < safe_stock AND is_disabled = 0
        ''')
        low_stock_count = cursor.fetchone()['count']

        # 物料种类数（排除禁用）
        cursor.execute('SELECT COUNT(*) as count FROM materials WHERE is_disabled = 0')
        material_types = cursor.fetchone()['count']

        # 计算昨日数据用于百分比变化
        yesterday_start = (datetime.now() - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday_end = today_start

        cursor.execute('''
            SELECT SUM(quantity) as total
            FROM inventory_records
            WHERE type = 'in' AND created_at >= ? AND created_at < ?
        ''', (yesterday_start.strftime('%Y-%m-%d %H:%M:%S'), yesterday_end.strftime('%Y-%m-%d %H:%M:%S')))
        yesterday_in = cursor.fetchone()['total'] or 1

        cursor.execute('''
            SELECT SUM(quantity) as total
            FROM inventory_records
            WHERE type = 'out' AND created_at >= ? AND created_at < ?
        ''', (yesterday_start.strftime('%Y-%m-%d %H:%M:%S'), yesterday_end.strftime('%Y-%m-%d %H:%M:%S')))
        yesterday_out = cursor.fetchone()['total'] or 1

        # 计算百分比变化
        in_change = round(((today_in - yesterday_in) / yesterday_in * 100), 1) if yesterday_in > 0 else 0
        out_change = round(((today_out - yesterday_out) / yesterday_out * 100), 1) if yesterday_out > 0 else 0

        return DashboardStats(
            total_stock=total_stock,
            today_in=today_in,
            today_out=today_out,
            low_stock_count=low_stock_count,
            material_types=material_types,
            in_change=in_change,
            out_change=out_change
        )


@app.get("/api/dashboard/category-distribution", response_model=List[CategoryItem])
def get_category_distribution():
    """获取库存类型分布"""
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('''
            SELECT category, SUM(quantity) as total
            FROM materials
            GROUP BY category
            ORDER BY total DESC
        ''')

        return [
            CategoryItem(name=row['category'], value=row['total'])
            for row in cursor.fetchall()
        ]


@app.get("/api/dashboard/weekly-trend", response_model=WeeklyTrend)
def get_weekly_trend():
    """获取近7天出入库趋势"""
    with get_db() as conn:
        cursor = conn.cursor()

        dates = []
        in_data = []
        out_data = []

        for i in range(6, -1, -1):
            date = datetime.now() - timedelta(days=i)
            date_start = date.replace(hour=0, minute=0, second=0, microsecond=0)
            date_end = date_start + timedelta(days=1)

            dates.append(date.strftime('%m-%d'))

            # 入库数据
            cursor.execute('''
                SELECT SUM(quantity) as total
                FROM inventory_records
                WHERE type = 'in' AND created_at >= ? AND created_at < ?
            ''', (date_start.strftime('%Y-%m-%d %H:%M:%S'), date_end.strftime('%Y-%m-%d %H:%M:%S')))
            in_total = cursor.fetchone()['total'] or 0
            in_data.append(in_total)

            # 出库数据
            cursor.execute('''
                SELECT SUM(quantity) as total
                FROM inventory_records
                WHERE type = 'out' AND created_at >= ? AND created_at < ?
            ''', (date_start.strftime('%Y-%m-%d %H:%M:%S'), date_end.strftime('%Y-%m-%d %H:%M:%S')))
            out_total = cursor.fetchone()['total'] or 0
            out_data.append(out_total)

        return WeeklyTrend(dates=dates, in_data=in_data, out_data=out_data)


@app.get("/api/dashboard/top-stock", response_model=TopStock)
def get_top_stock():
    """获取库存TOP10"""
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('''
            SELECT name, quantity, category
            FROM materials
            ORDER BY quantity DESC
            LIMIT 10
        ''')

        names = []
        quantities = []
        categories = []

        for row in cursor.fetchall():
            names.append(row['name'])
            quantities.append(row['quantity'])
            categories.append(row['category'])

        return TopStock(names=names, quantities=quantities, categories=categories)


@app.get("/api/dashboard/low-stock-alert", response_model=List[LowStockItem])
def get_low_stock_alert():
    """获取库存预警列表"""
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('''
            SELECT name, sku, category, quantity, safe_stock, location
            FROM materials
            WHERE quantity < safe_stock
            ORDER BY (quantity - safe_stock) ASC
            LIMIT 20
        ''')

        return [
            LowStockItem(
                name=row['name'],
                sku=row['sku'],
                category=row['category'],
                quantity=row['quantity'],
                safe_stock=row['safe_stock'],
                location=row['location'],
                shortage=row['safe_stock'] - row['quantity']
            )
            for row in cursor.fetchall()
        ]


# ============ Fuzzy Match & Search APIs ============

@app.get("/api/fuzzy-match", response_model=FuzzyMatchResponse)
def fuzzy_match_endpoint(
    q: str = Query(..., description="搜索文本"),
    entity_type: str = Query("all", description="实体类型: material/contact/operator/all"),
    top_k: int = Query(5, ge=1, le=50, description="返回前k个结果"),
    threshold: float = Query(50.0, ge=0, le=100, description="最低分数阈值")
):
    """模糊匹配搜索"""
    matcher = get_fuzzy_matcher()
    result = matcher.resolve(q, entity_type=entity_type)
    candidates_raw = matcher.search(q, entity_type=entity_type, top_k=top_k, threshold=threshold)

    candidates = [FuzzyMatchCandidate(**c) for c in candidates_raw]
    best_match = FuzzyMatchCandidate(**result['best_match']) if result['best_match'] else None

    if result['confident'] and best_match:
        message = f"找到最佳匹配: {best_match.name} (置信度: {best_match.score})"
    elif candidates:
        message = f"找到 {len(candidates)} 个候选项，请确认选择"
    else:
        message = f"未找到与 '{q}' 匹配的结果"

    return FuzzyMatchResponse(
        query=q,
        candidates=candidates,
        best_match=best_match,
        confident=result['confident'],
        message=message
    )


@app.get("/api/search")
def unified_search(
    q: str = Query(None, description="搜索文本"),
    entity_type: str = Query("material", description="实体类型: material/contact/operator"),
    category: str = Query(None, description="分类（仅material）"),
    status: str = Query(None, description="状态（仅material，逗号分隔）"),
    contact_type: str = Query(None, description="联系方类型: supplier/customer"),
    fuzzy: bool = Query(True, description="是否开启模糊匹配"),
    format: str = Query(None, description="brief时只返回核心字段"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
):
    """统一搜索端点"""
    with get_db() as conn:
        cursor = conn.cursor()

        if entity_type == "material":
            return _search_materials(cursor, q, category, status, fuzzy, format, page, page_size)
        elif entity_type == "contact":
            return _search_contacts(cursor, q, contact_type, fuzzy, format, page, page_size)
        elif entity_type == "operator":
            return _search_operators(cursor, q, fuzzy, format, page, page_size)
        else:
            raise HTTPException(status_code=400, detail=f"不支持的实体类型: {entity_type}")


def _search_materials(cursor, q, category, status, fuzzy, fmt, page, page_size):
    """搜索物料"""
    # 获取匹配的 material IDs (fuzzy mode)
    matched_ids = None
    if q and fuzzy:
        matcher = get_fuzzy_matcher()
        results = matcher.search(q, entity_type="material", top_k=100, threshold=50.0)
        matched_ids = [r['entity_id'] for r in results]
        if not matched_ids:
            return {"items": [], "page": page, "page_size": page_size, "total": 0, "total_pages": 1}

    base_query = 'SELECT id, name, sku, category, quantity, unit, safe_stock, location, is_disabled FROM materials WHERE is_disabled = 0'
    count_query = 'SELECT COUNT(*) as total FROM materials WHERE is_disabled = 0'
    params = []

    if matched_ids is not None:
        placeholders = ','.join('?' * len(matched_ids))
        base_query += f' AND id IN ({placeholders})'
        count_query += f' AND id IN ({placeholders})'
        params.extend(matched_ids)
    elif q and not fuzzy:
        base_query += ' AND (name LIKE ? OR sku LIKE ?)'
        count_query += ' AND (name LIKE ? OR sku LIKE ?)'
        params.extend([f'%{q}%', f'%{q}%'])

    if category:
        base_query += ' AND category = ?'
        count_query += ' AND category = ?'
        params.append(category)

    # Status filter — computed in Python, so when active we fetch all rows and paginate manually
    status_filter = status.split(',') if status else None

    if status_filter:
        # Fetch all matching rows (no SQL pagination), filter by computed status in Python
        base_query += ' ORDER BY name ASC'
        cursor.execute(base_query, params)
        rows = cursor.fetchall()

        all_items = []
        for row in rows:
            qty = row['quantity']
            ss = row['safe_stock']
            if qty >= ss:
                item_status = 'normal'
            elif qty >= ss * 0.5:
                item_status = 'warning'
            else:
                item_status = 'danger'

            if item_status not in status_filter:
                continue

            if fmt == "brief":
                all_items.append({"id": row['id'], "name": row['name'], "sku": row['sku']})
            else:
                all_items.append({
                    "id": row['id'], "name": row['name'], "sku": row['sku'],
                    "category": row['category'], "quantity": qty, "unit": row['unit'],
                    "safe_stock": ss, "location": row['location'], "status": item_status,
                })

        total = len(all_items)
        total_pages = math.ceil(total / page_size) if total > 0 else 1
        offset = (page - 1) * page_size
        items = all_items[offset:offset + page_size]
    else:
        # No status filter — use SQL pagination
        cursor.execute(count_query, params)
        total = cursor.fetchone()['total']

        base_query += ' ORDER BY name ASC LIMIT ? OFFSET ?'
        offset = (page - 1) * page_size
        params.extend([page_size, offset])

        cursor.execute(base_query, params)
        rows = cursor.fetchall()

        items = []
        for row in rows:
            qty = row['quantity']
            ss = row['safe_stock']
            if qty >= ss:
                item_status = 'normal'
            elif qty >= ss * 0.5:
                item_status = 'warning'
            else:
                item_status = 'danger'

            if fmt == "brief":
                items.append({"id": row['id'], "name": row['name'], "sku": row['sku']})
            else:
                items.append({
                    "id": row['id'], "name": row['name'], "sku": row['sku'],
                    "category": row['category'], "quantity": qty, "unit": row['unit'],
                    "safe_stock": ss, "location": row['location'], "status": item_status,
                })

        total_pages = math.ceil(total / page_size) if total > 0 else 1

    return {"items": items, "page": page, "page_size": page_size, "total": total, "total_pages": total_pages}


def _search_contacts(cursor, q, contact_type, fuzzy, fmt, page, page_size):
    """搜索联系方"""
    matched_ids = None
    if q and fuzzy:
        matcher = get_fuzzy_matcher()
        results = matcher.search(q, entity_type="contact", top_k=100, threshold=50.0)
        matched_ids = [r['entity_id'] for r in results]
        if not matched_ids:
            return {"items": [], "page": page, "page_size": page_size, "total": 0, "total_pages": 1}

    base_query = 'SELECT id, name, address, phone, email, is_supplier, is_customer, notes, is_disabled, created_at FROM contacts WHERE is_disabled = 0'
    count_query = 'SELECT COUNT(*) as total FROM contacts WHERE is_disabled = 0'
    params = []

    if matched_ids is not None:
        placeholders = ','.join('?' * len(matched_ids))
        base_query += f' AND id IN ({placeholders})'
        count_query += f' AND id IN ({placeholders})'
        params.extend(matched_ids)
    elif q and not fuzzy:
        base_query += ' AND name LIKE ?'
        count_query += ' AND name LIKE ?'
        params.append(f'%{q}%')

    if contact_type == 'supplier':
        base_query += ' AND is_supplier = 1'
        count_query += ' AND is_supplier = 1'
    elif contact_type == 'customer':
        base_query += ' AND is_customer = 1'
        count_query += ' AND is_customer = 1'

    cursor.execute(count_query, params)
    total = cursor.fetchone()['total']

    base_query += ' ORDER BY name ASC LIMIT ? OFFSET ?'
    offset = (page - 1) * page_size
    params.extend([page_size, offset])

    cursor.execute(base_query, params)
    rows = cursor.fetchall()

    if fmt == "brief":
        items = [{"id": row['id'], "name": row['name']} for row in rows]
    else:
        items = [{
            "id": row['id'], "name": row['name'], "address": row['address'],
            "phone": row['phone'], "email": row['email'],
            "is_supplier": bool(row['is_supplier']), "is_customer": bool(row['is_customer']),
            "notes": row['notes'], "is_disabled": bool(row['is_disabled']),
            "created_at": row['created_at'],
        } for row in rows]

    total_pages = math.ceil(total / page_size) if total > 0 else 1
    return {"items": items, "page": page, "page_size": page_size, "total": total, "total_pages": total_pages}


def _search_operators(cursor, q, fuzzy, fmt, page, page_size):
    """搜索操作员"""
    matched_ids = None
    if q and fuzzy:
        matcher = get_fuzzy_matcher()
        results = matcher.search(q, entity_type="operator", top_k=100, threshold=50.0)
        matched_ids = [r['entity_id'] for r in results]
        if not matched_ids:
            return {"items": [], "page": page, "page_size": page_size, "total": 0, "total_pages": 1}

    base_query = 'SELECT id, username, display_name FROM users WHERE is_disabled = 0'
    count_query = 'SELECT COUNT(*) as total FROM users WHERE is_disabled = 0'
    params = []

    if matched_ids is not None:
        placeholders = ','.join('?' * len(matched_ids))
        base_query += f' AND id IN ({placeholders})'
        count_query += f' AND id IN ({placeholders})'
        params.extend(matched_ids)
    elif q and not fuzzy:
        base_query += ' AND (username LIKE ? OR display_name LIKE ?)'
        count_query += ' AND (username LIKE ? OR display_name LIKE ?)'
        params.extend([f'%{q}%', f'%{q}%'])

    cursor.execute(count_query, params)
    total = cursor.fetchone()['total']

    base_query += ' ORDER BY username ASC LIMIT ? OFFSET ?'
    offset = (page - 1) * page_size
    params.extend([page_size, offset])

    cursor.execute(base_query, params)
    rows = cursor.fetchall()

    if fmt == "brief":
        items = [{"id": row['id'], "name": row['display_name'] or row['username']} for row in rows]
    else:
        items = [{
            "id": row['id'], "username": row['username'],
            "display_name": row['display_name'],
            "name": row['display_name'] or row['username'],
        } for row in rows]

    total_pages = math.ceil(total / page_size) if total > 0 else 1
    return {"items": items, "page": page, "page_size": page_size, "total": total, "total_pages": total_pages}


# ============ Materials APIs ============

@app.get("/api/materials/all", response_model=List[MaterialItem])
def get_all_materials():
    """获取所有库存（兼容旧API）"""
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('''
            SELECT name, sku, category, quantity, unit, safe_stock, location, is_disabled
            FROM materials
            WHERE is_disabled = 0
            ORDER BY name ASC
        ''')

        result = []
        for row in cursor.fetchall():
            quantity = row['quantity']
            safe_stock = row['safe_stock']

            # 判断状态
            if quantity >= safe_stock:
                status = 'normal'
                status_text = '正常'
            elif quantity >= safe_stock * 0.5:
                status = 'warning'
                status_text = '偏低'
            else:
                status = 'danger'
                status_text = '告急'

            result.append(MaterialItem(
                name=row['name'],
                sku=row['sku'],
                category=row['category'],
                quantity=quantity,
                unit=row['unit'],
                safe_stock=safe_stock,
                location=row['location'],
                status=status,
                status_text=status_text
            ))

        return result


@app.get("/api/materials/list")
def get_materials_list(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=10, le=100, description="每页条数"),
    name: Optional[str] = Query(None, description="名称/SKU模糊搜索"),
    category: Optional[str] = Query(None, description="分类"),
    status: Optional[str] = Query(None, description="状态(逗号分隔: normal,warning,danger,disabled)"),
    min_stock: Optional[int] = Query(None, description="最小库存过滤"),
    max_stock: Optional[int] = Query(None, description="最大库存过滤"),
    location: Optional[str] = Query(None, description="位置模糊匹配"),
    fuzzy: bool = Query(True, description="名称模糊匹配开关"),
    format: Optional[str] = Query(None, description="brief时精简返回"),
):
    """获取物料列表（分页+筛选）— 一行一批次"""
    with get_db() as conn:
        cursor = conn.cursor()

        status_filter = status.split(',') if status else None

        # Fuzzy name search
        fuzzy_ids = None
        if name and fuzzy:
            matcher = get_fuzzy_matcher()
            results = matcher.search(name, entity_type="material", top_k=100, threshold=50.0)
            fuzzy_ids = [r['entity_id'] for r in results]
            if not fuzzy_ids:
                return {"items": [], "page": page, "page_size": page_size, "total": 0, "total_pages": 1}

        # 构建物料筛选条件
        where_clauses = []
        params = []

        if not status_filter or 'disabled' not in status_filter:
            where_clauses.append('m.is_disabled = 0')

        if fuzzy_ids is not None:
            placeholders = ','.join('?' * len(fuzzy_ids))
            where_clauses.append(f'm.id IN ({placeholders})')
            params.extend(fuzzy_ids)
        elif name and not fuzzy:
            where_clauses.append('(m.name LIKE ? OR m.sku LIKE ?)')
            params.extend([f'%{name}%', f'%{name}%'])

        if category:
            where_clauses.append('m.category = ?')
            params.append(category)

        if location:
            where_clauses.append('(b.location LIKE ? OR m.location LIKE ?)')
            params.extend([f'%{location}%', f'%{location}%'])

        where_sql = ' AND '.join(where_clauses) if where_clauses else '1=1'

        # 查询：一行一批次（LEFT JOIN batches）
        # 对于有批次的物料，每个活跃批次一行
        # 对于无批次的物料（quantity=0），显示一行空批次
        base_query = f'''
            SELECT m.id as material_id, m.name, m.sku, m.category, m.quantity as total_quantity,
                   m.unit, m.safe_stock, m.location as material_location, m.is_disabled,
                   b.batch_no, b.quantity as batch_quantity, b.location as batch_location,
                   c.name as contact_name
            FROM materials m
            LEFT JOIN batches b ON b.material_id = m.id AND b.is_exhausted = 0
            LEFT JOIN contacts c ON b.contact_id = c.id
            WHERE {where_sql}
            ORDER BY m.name ASC, b.created_at ASC
        '''

        cursor.execute(base_query, params)
        all_rows = cursor.fetchall()

        # 应用状态筛选和库存范围筛选（在应用层做，因为状态是计算值）
        filtered = []
        for row in all_rows:
            total_qty = row['total_quantity']
            safe_stock_val = row['safe_stock']
            is_disabled = bool(row['is_disabled'])

            if is_disabled:
                item_status = 'disabled'
            elif total_qty >= safe_stock_val:
                item_status = 'normal'
            elif total_qty >= safe_stock_val * 0.5:
                item_status = 'warning'
            else:
                item_status = 'danger'

            if status_filter and item_status not in status_filter:
                continue
            if min_stock is not None and total_qty < min_stock:
                continue
            if max_stock is not None and total_qty > max_stock:
                continue

            filtered.append((row, item_status))

        total = len(filtered)
        total_pages = math.ceil(total / page_size) if total > 0 else 1
        offset = (page - 1) * page_size
        page_rows = filtered[offset:offset + page_size]

        result = []
        for row, item_status in page_rows:
            is_disabled = bool(row['is_disabled'])
            status_text_map = {'normal': '正常', 'warning': '偏低', 'danger': '告急', 'disabled': '禁用'}

            batch_qty = row['batch_quantity'] if row['batch_quantity'] is not None else row['total_quantity']
            batch_loc = row['batch_location'] if row['batch_location'] else (row['material_location'] or '')

            if format == "brief":
                result.append({"id": row['material_id'], "name": row['name'], "sku": row['sku']})
            else:
                result.append(MaterialItemWithDisabled(
                    name=row['name'],
                    sku=row['sku'],
                    category=row['category'],
                    quantity=batch_qty,
                    unit=row['unit'],
                    safe_stock=row['safe_stock'],
                    location=batch_loc,
                    status=item_status,
                    status_text=status_text_map.get(item_status, ''),
                    is_disabled=is_disabled,
                    batch_no=row['batch_no'] or '',
                    contact_name=row['contact_name'] or '',
                    total_quantity=row['total_quantity'],
                ))

        return {
            "items": result,
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
        }


@app.get("/api/materials/categories", response_model=List[str])
def get_categories():
    """获取所有物料分类"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT DISTINCT category FROM materials ORDER BY category')
        return [row['category'] for row in cursor.fetchall()]


@app.get("/api/materials/product-stats", response_model=ProductStats)
def get_product_stats(name: str = Query(..., description="产品名称")):
    """获取单个产品的统计数据"""
    if not name:
        raise HTTPException(status_code=400, detail="缺少产品名称参数")

    with get_db() as conn:
        cursor = conn.cursor()

        # 查询产品基本信息
        cursor.execute('''
            SELECT id, name, sku, quantity, unit, safe_stock, location
            FROM materials
            WHERE name = ?
        ''', (name,))

        product = cursor.fetchone()
        if not product:
            raise HTTPException(status_code=404, detail="产品不存在")

        material_id = product['id']
        current_stock = product['quantity']
        unit = product['unit']
        safe_stock = product['safe_stock']

        # 获取今天的日期
        today = datetime.now().strftime('%Y-%m-%d')
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

        # 查询今日入库
        cursor.execute('''
            SELECT COALESCE(SUM(quantity), 0) as total
            FROM inventory_records
            WHERE material_id = ? AND type = 'in' AND DATE(created_at) = ?
        ''', (material_id, today))
        today_in = cursor.fetchone()['total']

        # 查询昨日入库
        cursor.execute('''
            SELECT COALESCE(SUM(quantity), 0) as total
            FROM inventory_records
            WHERE material_id = ? AND type = 'in' AND DATE(created_at) = ?
        ''', (material_id, yesterday))
        yesterday_in = cursor.fetchone()['total']

        # 查询今日出库
        cursor.execute('''
            SELECT COALESCE(SUM(quantity), 0) as total
            FROM inventory_records
            WHERE material_id = ? AND type = 'out' AND DATE(created_at) = ?
        ''', (material_id, today))
        today_out = cursor.fetchone()['total']

        # 查询昨日出库
        cursor.execute('''
            SELECT COALESCE(SUM(quantity), 0) as total
            FROM inventory_records
            WHERE material_id = ? AND type = 'out' AND DATE(created_at) = ?
        ''', (material_id, yesterday))
        yesterday_out = cursor.fetchone()['total']

        # 查询总入库和总出库（用于饼图）
        cursor.execute('''
            SELECT COALESCE(SUM(quantity), 0) as total
            FROM inventory_records
            WHERE material_id = ? AND type = 'in'
        ''', (material_id,))
        total_in = cursor.fetchone()['total']

        cursor.execute('''
            SELECT COALESCE(SUM(quantity), 0) as total
            FROM inventory_records
            WHERE material_id = ? AND type = 'out'
        ''', (material_id,))
        total_out = cursor.fetchone()['total']

        # 计算变化百分比
        in_change = ((today_in - yesterday_in) / yesterday_in * 100) if yesterday_in > 0 else 0
        out_change = ((today_out - yesterday_out) / yesterday_out * 100) if yesterday_out > 0 else 0

        return ProductStats(
            name=name,
            sku=product['sku'],
            current_stock=current_stock,
            unit=unit,
            safe_stock=safe_stock,
            location=product['location'],
            today_in=today_in,
            today_out=today_out,
            in_change=round(in_change, 1),
            out_change=round(out_change, 1),
            total_in=total_in,
            total_out=total_out
        )


@app.get("/api/materials/batches")
def get_material_batches(name: str = Query(..., description="产品名称")):
    """获取物料的活跃批次列表"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM materials WHERE name = ?', (name,))
        material = cursor.fetchone()
        if not material:
            raise HTTPException(status_code=404, detail="产品不存在")

        cursor.execute('''
            SELECT b.batch_no, b.quantity, b.location, b.created_at, c.name as contact_name
            FROM batches b
            LEFT JOIN contacts c ON b.contact_id = c.id
            WHERE b.material_id = ? AND b.is_exhausted = 0
            ORDER BY b.created_at ASC
        ''', (material['id'],))
        batches = cursor.fetchall()

        total_quantity = sum(b['quantity'] for b in batches)
        return {
            "batches": [
                {
                    "batch_no": b['batch_no'],
                    "quantity": b['quantity'],
                    "location": b['location'] or '',
                    "contact_name": b['contact_name'] or '',
                    "created_at": b['created_at'],
                }
                for b in batches
            ],
            "total_quantity": total_quantity,
        }


@app.get("/api/materials/product-trend", response_model=WeeklyTrend)
def get_product_trend(name: str = Query(..., description="产品名称")):
    """获取单个产品的近7天趋势"""
    if not name:
        raise HTTPException(status_code=400, detail="缺少产品名称参数")

    with get_db() as conn:
        cursor = conn.cursor()

        # 查询产品ID
        cursor.execute('SELECT id FROM materials WHERE name = ?', (name,))
        product = cursor.fetchone()
        if not product:
            raise HTTPException(status_code=404, detail="产品不存在")

        material_id = product['id']

        # 获取近7天的日期
        dates = []
        for i in range(6, -1, -1):
            date = (datetime.now() - timedelta(days=i)).strftime('%m-%d')
            dates.append(date)

        # 查询每天的入库和出库数据
        in_data = []
        out_data = []

        for i in range(6, -1, -1):
            date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')

            # 查询当天入库
            cursor.execute('''
                SELECT COALESCE(SUM(quantity), 0) as total
                FROM inventory_records
                WHERE material_id = ? AND type = 'in' AND DATE(created_at) = ?
            ''', (material_id, date))
            in_data.append(cursor.fetchone()['total'])

            # 查询当天出库
            cursor.execute('''
                SELECT COALESCE(SUM(quantity), 0) as total
                FROM inventory_records
                WHERE material_id = ? AND type = 'out' AND DATE(created_at) = ?
            ''', (material_id, date))
            out_data.append(cursor.fetchone()['total'])

        return WeeklyTrend(dates=dates, in_data=in_data, out_data=out_data)


@app.get("/api/materials/product-records", response_model=PaginatedProductRecordsResponse)
def get_product_records(
    name: str = Query(..., description="产品名称"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=10, le=100, description="每页条数")
):
    """获取单个产品的出入库记录（分页）"""
    if not name:
        raise HTTPException(status_code=400, detail="缺少产品名称参数")

    with get_db() as conn:
        cursor = conn.cursor()

        # 查询产品ID
        cursor.execute('SELECT id FROM materials WHERE name = ?', (name,))
        product = cursor.fetchone()
        if not product:
            raise HTTPException(status_code=404, detail="产品不存在")

        material_id = product['id']

        # 获取总数
        cursor.execute('SELECT COUNT(*) as total FROM inventory_records WHERE material_id = ?', (material_id,))
        total = cursor.fetchone()['total']

        # 分页查询
        offset = (page - 1) * page_size
        cursor.execute('''
            SELECT type, quantity, operator, reason, created_at
            FROM inventory_records
            WHERE material_id = ?
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        ''', (material_id, page_size, offset))

        items = [
            ProductRecord(
                type=row['type'],
                quantity=row['quantity'],
                operator=row['operator'],
                reason=row['reason'],
                created_at=row['created_at']
            )
            for row in cursor.fetchall()
        ]

        total_pages = math.ceil(total / page_size) if total > 0 else 1

        return PaginatedProductRecordsResponse(
            items=items,
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages
        )


@app.get("/api/inventory/records")
def get_inventory_records_paginated(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=10, le=100, description="每页条数"),
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    product_name: Optional[str] = Query(None, description="产品名称/SKU模糊搜索"),
    category: Optional[str] = Query(None, description="商品类型/分类"),
    record_type: Optional[str] = Query(None, description="记录类型: in/out"),
    status: Optional[str] = Query(None, description="状态(逗号分隔: normal,warning,danger,disabled)"),
    contact_id: Optional[int] = Query(None, description="联系方ID筛选"),
    operator_user_id: Optional[int] = Query(None, description="操作员用户ID筛选"),
    reason: Optional[str] = Query(None, description="原因关键词搜索"),
    sort_by: str = Query("created_at", description="排序字段: created_at/quantity/material_name"),
    sort_order: str = Query("desc", description="排序方向: asc/desc"),
    format: Optional[str] = Query(None, description="brief时精简返回"),
):
    """获取所有进出库记录（分页+筛选）"""
    with get_db() as conn:
        cursor = conn.cursor()

        # 解析状态筛选
        status_filter = status.split(',') if status else None

        # 构建查询（含联系方、批次和操作员信息）
        base_query = '''
            SELECT r.id, m.name as material_name, m.sku as material_sku, m.category,
                   r.type, r.quantity, r.operator, r.operator_user_id, r.reason, r.created_at,
                   m.quantity as current_quantity, m.safe_stock, m.is_disabled,
                   r.contact_id, c.name as contact_name,
                   r.batch_id, b.batch_no,
                   u.display_name as operator_display_name, u.username as operator_username
            FROM inventory_records r
            JOIN materials m ON r.material_id = m.id
            LEFT JOIN contacts c ON r.contact_id = c.id
            LEFT JOIN batches b ON r.batch_id = b.id
            LEFT JOIN users u ON r.operator_user_id = u.id
            WHERE 1=1
        '''
        count_query = '''
            SELECT COUNT(*) as total
            FROM inventory_records r
            JOIN materials m ON r.material_id = m.id
            WHERE 1=1
        '''
        params = []

        # 时间范围筛选
        if start_date:
            base_query += ' AND DATE(r.created_at) >= ?'
            count_query += ' AND DATE(r.created_at) >= ?'
            params.append(start_date)
        if end_date:
            base_query += ' AND DATE(r.created_at) <= ?'
            count_query += ' AND DATE(r.created_at) <= ?'
            params.append(end_date)

        # 产品名称/SKU搜索
        if product_name:
            base_query += ' AND (m.name LIKE ? OR m.sku LIKE ?)'
            count_query += ' AND (m.name LIKE ? OR m.sku LIKE ?)'
            params.extend([f'%{product_name}%', f'%{product_name}%'])

        # 商品类型/分类筛选
        if category:
            base_query += ' AND m.category = ?'
            count_query += ' AND m.category = ?'
            params.append(category)

        # 记录类型筛选
        if record_type:
            base_query += ' AND r.type = ?'
            count_query += ' AND r.type = ?'
            params.append(record_type)

        # 联系方筛选
        if contact_id:
            base_query += ' AND r.contact_id = ?'
            count_query += ' AND r.contact_id = ?'
            params.append(contact_id)

        # 操作员筛选
        if operator_user_id:
            base_query += ' AND r.operator_user_id = ?'
            count_query += ' AND r.operator_user_id = ?'
            params.append(operator_user_id)

        # 原因关键词搜索
        if reason:
            base_query += ' AND r.reason LIKE ?'
            count_query += ' AND r.reason LIKE ?'
            params.append(f'%{reason}%')

        # 获取总数
        cursor.execute(count_query, params)
        total = cursor.fetchone()['total']

        # 排序和分页
        sort_column_map = {
            'created_at': 'r.created_at',
            'quantity': 'r.quantity',
            'material_name': 'm.name',
        }
        sort_col = sort_column_map.get(sort_by, 'r.created_at')
        sort_dir = 'ASC' if sort_order.lower() == 'asc' else 'DESC'
        base_query += f' ORDER BY {sort_col} {sort_dir} LIMIT ? OFFSET ?'
        offset = (page - 1) * page_size
        params.extend([page_size, offset])

        cursor.execute(base_query, params)
        rows = cursor.fetchall()

        result = []
        filtered_count = 0
        for row in rows:
            quantity = row['current_quantity']
            safe_stock = row['safe_stock']
            is_disabled = bool(row['is_disabled'])

            # 计算物料当前状态
            if is_disabled:
                material_status = 'disabled'
            elif quantity >= safe_stock:
                material_status = 'normal'
            elif quantity >= safe_stock * 0.5:
                material_status = 'warning'
            else:
                material_status = 'danger'

            # 状态筛选
            if status_filter and material_status not in status_filter:
                continue

            # 获取批次详情
            batch_details = None
            record_id = row['id']
            record_type_val = row['type']

            if record_type_val == 'out':
                # 出库记录：查询批次消耗详情
                cursor.execute('''
                    SELECT b.batch_no, bc.quantity
                    FROM batch_consumptions bc
                    JOIN batches b ON bc.batch_id = b.id
                    WHERE bc.record_id = ?
                    ORDER BY b.created_at ASC
                ''', (record_id,))
                consumptions = cursor.fetchall()
                if consumptions:
                    details = [f"{c['batch_no']}×{c['quantity']}" for c in consumptions]
                    batch_details = ', '.join(details)

            # 操作员名称：优先使用用户表中的显示名称，否则使用旧的operator字段
            operator_name = row['operator_display_name'] or row['operator_username'] or row['operator']

            if format == "brief":
                result.append({
                    "id": record_id,
                    "material_name": row['material_name'],
                    "type": record_type_val,
                    "quantity": row['quantity'],
                    "created_at": row['created_at'],
                })
            else:
                result.append(InventoryRecordItem(
                    id=record_id,
                    material_name=row['material_name'],
                    material_sku=row['material_sku'],
                    category=row['category'],
                    type=record_type_val,
                    quantity=row['quantity'],
                    operator=row['operator'],
                    operator_user_id=row['operator_user_id'],
                    operator_name=operator_name,
                    reason=row['reason'],
                    created_at=row['created_at'],
                    material_status=material_status,
                    is_disabled=is_disabled,
                    contact_id=row['contact_id'],
                    contact_name=row['contact_name'],
                    batch_id=row['batch_id'],
                    batch_no=row['batch_no'],
                    batch_details=batch_details
                ))
            filtered_count += 1

        # 如果有状态筛选，需要重新计算总数
        if status_filter:
            # 需要遍历所有数据来计算真实的筛选后总数
            count_base_query = '''
                SELECT m.quantity, m.safe_stock, m.is_disabled
                FROM inventory_records r
                JOIN materials m ON r.material_id = m.id
                WHERE 1=1
            '''
            count_params = []
            if start_date:
                count_base_query += ' AND DATE(r.created_at) >= ?'
                count_params.append(start_date)
            if end_date:
                count_base_query += ' AND DATE(r.created_at) <= ?'
                count_params.append(end_date)
            if product_name:
                count_base_query += ' AND (m.name LIKE ? OR m.sku LIKE ?)'
                count_params.extend([f'%{product_name}%', f'%{product_name}%'])
            if record_type:
                count_base_query += ' AND r.type = ?'
                count_params.append(record_type)

            cursor.execute(count_base_query, count_params)
            all_rows = cursor.fetchall()
            total = 0
            for r in all_rows:
                qty = r['quantity']
                ss = r['safe_stock']
                dis = bool(r['is_disabled'])
                if dis:
                    s = 'disabled'
                elif qty >= ss:
                    s = 'normal'
                elif qty >= ss * 0.5:
                    s = 'warning'
                else:
                    s = 'danger'
                if s in status_filter:
                    total += 1

        total_pages = math.ceil(total / page_size) if total > 0 else 1

        if format == "brief":
            return {"items": result, "page": page, "page_size": page_size, "total": total, "total_pages": total_pages}
        return PaginatedRecordsResponse(
            items=result,
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages
        )


# ============ Stock Operation APIs (for MCP) ============

@app.post("/api/materials/stock-in", response_model=StockInResponse)
async def stock_in(
    request: StockOperationRequest,
    current_user: CurrentUser = Depends(require_auth('operate'))
):
    """入库操作（需要operate权限）- 自动创建批次，支持模糊匹配"""
    product_name = request.product_name
    quantity = request.quantity
    reason = request.reason or "采购入库"
    operator = request.operator if request.operator and request.operator != "MCP系统" else current_user.get_operator_name()
    operator_user_id = current_user.id
    resolved_from = None

    if quantity <= 0:
        return StockInResponse(
            success=False,
            error="入库数量必须大于0",
            message=f"入库失败：数量 {quantity} 无效"
        )

    with get_db() as conn:
        cursor = conn.cursor()

        # 查询产品（先精确匹配）
        cursor.execute('SELECT id, unit FROM materials WHERE name = ?', (product_name,))
        row = cursor.fetchone()

        # 模糊匹配
        if not row and request.fuzzy:
            matcher = get_fuzzy_matcher()
            result = matcher.resolve(product_name, entity_type="material")

            if result['confident'] and result['best_match']:
                resolved_from = product_name
                product_name = result['best_match']['name']
                cursor.execute('SELECT id, unit FROM materials WHERE name = ?', (product_name,))
                row = cursor.fetchone()
            elif result['candidates']:
                names = [c['name'] for c in result['candidates'][:5]]
                return StockInResponse(
                    success=False,
                    error="ambiguous_name",
                    message=f"无法确定产品 '{product_name}'，候选：{', '.join(names)}",
                    candidates=result['candidates'],
                )

        if not row:
            return StockInResponse(
                success=False,
                error=f"产品不存在: {product_name}",
                message=f"入库失败：未找到产品 '{product_name}'"
            )

        material_id = row['id']
        unit = row['unit']

        # 原子化更新
        cursor.execute('UPDATE materials SET quantity = quantity + ? WHERE id = ?', (quantity, material_id))
        if cursor.rowcount == 0:
            return StockInResponse(success=False, error="入库失败", message="入库操作未生效，请重试")

        cursor.execute('SELECT quantity FROM materials WHERE id = ?', (material_id,))
        new_quantity = cursor.fetchone()['quantity']
        old_quantity = new_quantity - quantity

        batch_no = request.batch_no.strip() if request.batch_no and request.batch_no.strip() else generate_batch_no(material_id)
        cursor.execute('''
            INSERT INTO batches (batch_no, material_id, quantity, initial_quantity, contact_id, location, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (batch_no, material_id, quantity, quantity, request.contact_id, request.location, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        batch_id = cursor.lastrowid

        cursor.execute('''
            INSERT INTO inventory_records (material_id, type, quantity, operator, operator_user_id, reason, contact_id, batch_id, created_at)
            VALUES (?, 'in', ?, ?, ?, ?, ?, ?, ?)
        ''', (material_id, quantity, operator, operator_user_id, reason, request.contact_id, batch_id, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

        conn.commit()

        # 写操作后清除缓存
        get_fuzzy_matcher().invalidate_cache()

        audit_log("STOCK_IN", current_user.id, current_user.username, {
            "product": product_name,
            "quantity": quantity,
            "batch_no": batch_no,
            "old_qty": old_quantity,
            "new_qty": new_quantity,
            "resolved_from": resolved_from,
        })

        return StockInResponse(
            success=True,
            operation="stock_in",
            product=StockOperationProduct(
                name=product_name,
                old_quantity=old_quantity,
                in_quantity=quantity,
                new_quantity=new_quantity,
                unit=unit
            ),
            batch=BatchInfo(batch_no=batch_no, batch_id=batch_id, quantity=quantity),
            message=f"入库成功：{product_name} 入库 {quantity} {unit}（批次 {batch_no}），库存从 {old_quantity} 更新到 {new_quantity} {unit}",
            resolved_from=resolved_from,
        )


@app.post("/api/materials/stock-out", response_model=StockOutResponse)
@limiter.limit("60/minute")  # 出库速率限制
async def stock_out(
    request: Request,
    stock_data: StockOperationRequest,
    current_user: CurrentUser = Depends(require_auth('operate'))
):
    """出库操作（需要operate权限）- FIFO批次消耗，支持模糊匹配"""
    product_name = stock_data.product_name
    quantity = stock_data.quantity
    reason = stock_data.reason or "销售出库"
    operator = stock_data.operator if stock_data.operator and stock_data.operator != "MCP系统" else current_user.get_operator_name()
    operator_user_id = current_user.id
    resolved_from = None

    if quantity <= 0:
        return StockOutResponse(
            success=False,
            error="出库数量必须大于0",
            message=f"出库失败：数量 {quantity} 无效"
        )

    with get_db() as conn:
        cursor = conn.cursor()

        # 先精确匹配
        cursor.execute('SELECT id, unit, safe_stock FROM materials WHERE name = ?', (product_name,))
        row = cursor.fetchone()

        # 模糊匹配
        if not row and stock_data.fuzzy:
            matcher = get_fuzzy_matcher()
            result = matcher.resolve(product_name, entity_type="material")

            if result['confident'] and result['best_match']:
                resolved_from = product_name
                product_name = result['best_match']['name']
                cursor.execute('SELECT id, unit, safe_stock FROM materials WHERE name = ?', (product_name,))
                row = cursor.fetchone()
            elif result['candidates']:
                names = [c['name'] for c in result['candidates'][:5]]
                return StockOutResponse(
                    success=False,
                    error="ambiguous_name",
                    message=f"无法确定产品 '{product_name}'，候选：{', '.join(names)}",
                    candidates=result['candidates'],
                )

        if not row:
            return StockOutResponse(
                success=False,
                error=f"产品不存在: {product_name}",
                message=f"出库失败：未找到产品 '{product_name}'"
            )

        material_id = row['id']
        unit = row['unit']
        safe_stock = row['safe_stock']

        # 原子化更新，防止并发扣减导致负库存
        cursor.execute('''
            UPDATE materials SET quantity = quantity - ? WHERE id = ? AND quantity >= ?
        ''', (quantity, material_id, quantity))

        if cursor.rowcount == 0:
            cursor.execute('SELECT quantity FROM materials WHERE id = ?', (material_id,))
            current_qty_row = cursor.fetchone()
            current_qty = current_qty_row['quantity'] if current_qty_row else 0
            return StockOutResponse(
                success=False,
                error="库存不足",
                message=f"出库失败：{product_name} 库存不足，当前库存 {current_qty} {unit}，需要出库 {quantity} {unit}"
            )

        cursor.execute('SELECT quantity FROM materials WHERE id = ?', (material_id,))
        new_quantity = cursor.fetchone()['quantity']
        old_quantity = new_quantity + quantity

        cursor.execute('''
            INSERT INTO inventory_records (material_id, type, quantity, operator, operator_user_id, reason, contact_id, created_at)
            VALUES (?, 'out', ?, ?, ?, ?, ?, ?)
        ''', (material_id, quantity, operator, operator_user_id, reason, stock_data.contact_id, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        record_id = cursor.lastrowid

        # FIFO批次消耗
        batch_consumptions = []
        remaining_to_consume = quantity

        cursor.execute('''
            SELECT id, batch_no, quantity FROM batches
            WHERE material_id = ? AND is_exhausted = 0 AND quantity > 0
            ORDER BY created_at ASC
        ''', (material_id,))
        available_batches = cursor.fetchall()

        for batch in available_batches:
            if remaining_to_consume <= 0:
                break

            batch_id = batch['id']
            batch_no = batch['batch_no']
            batch_qty = batch['quantity']

            consume_qty = min(batch_qty, remaining_to_consume)
            new_batch_qty = batch_qty - consume_qty
            remaining_to_consume -= consume_qty

            is_exhausted = 1 if new_batch_qty == 0 else 0
            cursor.execute('UPDATE batches SET quantity = ?, is_exhausted = ? WHERE id = ?',
                           (new_batch_qty, is_exhausted, batch_id))

            cursor.execute('''
                INSERT INTO batch_consumptions (record_id, batch_id, quantity, created_at)
                VALUES (?, ?, ?, ?)
            ''', (record_id, batch_id, consume_qty, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

            batch_consumptions.append(BatchConsumption(
                batch_no=batch_no, batch_id=batch_id,
                quantity=consume_qty, remaining=new_batch_qty
            ))

        conn.commit()

        # 写操作后清除缓存
        get_fuzzy_matcher().invalidate_cache()

        audit_log("STOCK_OUT", current_user.id, current_user.username, {
            "product": product_name,
            "quantity": quantity,
            "old_qty": old_quantity,
            "new_qty": new_quantity,
            "resolved_from": resolved_from,
            "batches": [bc.batch_no for bc in batch_consumptions] if batch_consumptions else []
        })

        warning = ""
        if new_quantity < safe_stock:
            if new_quantity < safe_stock * 0.5:
                warning = f"⚠️ 警告：库存告急！当前库存 {new_quantity} {unit}，低于安全库存 {safe_stock} {unit} 的50%"
            else:
                warning = f"⚠️ 提醒：库存偏低，当前库存 {new_quantity} {unit}，低于安全库存 {safe_stock} {unit}"

        batch_details = ""
        if batch_consumptions:
            details = [f"{bc.batch_no}×{bc.quantity}" for bc in batch_consumptions]
            batch_details = f"（消耗批次: {', '.join(details)}）"

        return StockOutResponse(
            success=True,
            operation="stock_out",
            product=StockOperationProduct(
                name=product_name, old_quantity=old_quantity,
                out_quantity=quantity, new_quantity=new_quantity,
                unit=unit, safe_stock=safe_stock
            ),
            batch_consumptions=batch_consumptions if batch_consumptions else None,
            message=f"出库成功：{product_name} 出库 {quantity} {unit}{batch_details}，库存从 {old_quantity} 更新到 {new_quantity} {unit}",
            warning=warning if warning else None,
            resolved_from=resolved_from,
        )


# ============ Excel Import/Export APIs ============

@app.get("/api/materials/export-excel")
def export_materials_excel(
    name: Optional[str] = Query(None, description="名称/SKU模糊搜索"),
    category: Optional[str] = Query(None, description="分类"),
    status: Optional[str] = Query(None, description="状态(逗号分隔)")
):
    """导出库存数据为Excel — 一行一批次，含批次号、位置、联系方"""
    with get_db() as conn:
        cursor = conn.cursor()

        # 基础查询
        query = '''
            SELECT id, name, sku, category, quantity, unit, safe_stock, location, is_disabled
            FROM materials
            WHERE 1=1
        '''
        params = []

        # 解析状态筛选
        status_filter = status.split(',') if status else None

        # 如果没有指定状态筛选，或者状态筛选中不包含disabled，则只查询未禁用的
        if not status_filter or 'disabled' not in status_filter:
            query += ' AND is_disabled = 0'

        # 名称/SKU搜索
        if name:
            query += ' AND (name LIKE ? OR sku LIKE ?)'
            params.extend([f'%{name}%', f'%{name}%'])

        # 分类筛选
        if category:
            query += ' AND category = ?'
            params.append(category)

        query += ' ORDER BY name ASC'

        cursor.execute(query, params)
        rows = cursor.fetchall()

    # 构建导出行（一行一批次）
    export_rows = []
    with get_db() as conn:
        cursor = conn.cursor()
        for row in rows:
            quantity = row['quantity']
            safe_stock = row['safe_stock']
            is_disabled = bool(row['is_disabled'])

            # 计算状态
            if is_disabled:
                item_status = 'disabled'
            elif quantity >= safe_stock:
                item_status = 'normal'
            elif quantity >= safe_stock * 0.5:
                item_status = 'warning'
            else:
                item_status = 'danger'

            # 状态筛选
            if status_filter and item_status not in status_filter:
                continue

            material_base = {
                'name': row['name'],
                'sku': row['sku'],
                'category': row['category'],
                'unit': row['unit'],
                'safe_stock': row['safe_stock'],
            }

            # 查询活跃批次
            cursor.execute('''
                SELECT b.batch_no, b.quantity, b.location, c.name as contact_name
                FROM batches b
                LEFT JOIN contacts c ON b.contact_id = c.id
                WHERE b.material_id = ? AND b.is_exhausted = 0
                ORDER BY b.created_at ASC
            ''', (row['id'],))
            batches = cursor.fetchall()

            if batches:
                for batch in batches:
                    export_rows.append({
                        **material_base,
                        'batch_no': batch['batch_no'],
                        'quantity': batch['quantity'],
                        'location': batch['location'] or '',
                        'contact_name': batch['contact_name'] or '',
                    })
            else:
                # 无活跃批次（库存为0的物料）
                export_rows.append({
                    **material_base,
                    'batch_no': '',
                    'quantity': 0,
                    'location': row['location'] or '',
                    'contact_name': '',
                })

    wb = Workbook()
    ws = wb.active
    ws.title = "库存数据"

    # 表头
    headers = ['物料名称', '物料编码(SKU)', '分类', '单位', '安全库存', '批次号', '库存', '存放位置', '联系方']
    for col, header in enumerate(headers, 1):
        ws.cell(row=1, column=col, value=header)

    # 数据
    for row_idx, item in enumerate(export_rows, 2):
        ws.cell(row=row_idx, column=1, value=item['name'])
        ws.cell(row=row_idx, column=2, value=item['sku'])
        ws.cell(row=row_idx, column=3, value=item['category'])
        ws.cell(row=row_idx, column=4, value=item['unit'])
        ws.cell(row=row_idx, column=5, value=item['safe_stock'])
        ws.cell(row=row_idx, column=6, value=item['batch_no'])
        ws.cell(row=row_idx, column=7, value=item['quantity'])
        ws.cell(row=row_idx, column=8, value=item['location'])
        ws.cell(row=row_idx, column=9, value=item['contact_name'])

    # 设置列宽
    column_widths = [22, 18, 14, 8, 12, 18, 10, 16, 16]
    for i, width in enumerate(column_widths, 1):
        ws.column_dimensions[chr(64 + i)].width = width

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f"inventory_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.post("/api/materials/import-excel/preview", response_model=ExcelImportPreviewResponse)
@limiter.limit("10/minute")  # Excel导入速率限制
async def preview_import_excel(
    request: Request,
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(get_current_user)  # 需要登录
):
    """预览Excel导入内容，自动检测简化模式/批次模式"""
    def _error_resp(msg):
        return ExcelImportPreviewResponse(
            success=False, preview=[], new_skus=[], total_in=0, total_out=0, total_new=0, message=msg
        )

    # 文件大小检查
    contents = await file.read()
    file_size_mb = len(contents) / (1024 * 1024)
    if file_size_mb > MAX_UPLOAD_SIZE_MB:
        return _error_resp(f"文件大小 ({file_size_mb:.1f}MB) 超过限制 ({MAX_UPLOAD_SIZE_MB}MB)")

    try:
        wb = load_workbook(filename=BytesIO(contents))
        ws = wb.active
    except Exception as e:
        return _error_resp(f"文件解析失败: {str(e)}")

    # 读取表头，自动识别列位置
    header_row = [str(cell).strip() if cell else "" for cell in next(ws.iter_rows(min_row=1, max_row=1, values_only=True))]

    col_mapping = {
        'name': None, 'sku': None, 'category': None, 'quantity': None,
        'unit': None, 'safe_stock': None, 'location': None,
        'batch_no': None, 'contact_name': None,
    }

    for idx, header in enumerate(header_row):
        header_lower = header.lower()
        if '名称' in header or 'name' in header_lower:
            col_mapping['name'] = idx
        elif 'sku' in header_lower or '编码' in header:
            col_mapping['sku'] = idx
        elif '分类' in header or 'category' in header_lower:
            col_mapping['category'] = idx
        elif '库存' in header or 'quantity' in header_lower or '数量' in header:
            if '安全' not in header and '批次' not in header:
                col_mapping['quantity'] = idx
        elif '单位' in header or 'unit' in header_lower:
            col_mapping['unit'] = idx
        elif '安全库存' in header or 'safe' in header_lower:
            col_mapping['safe_stock'] = idx
        elif '位置' in header or 'location' in header_lower:
            col_mapping['location'] = idx
        elif '批次' in header or 'batch' in header_lower:
            col_mapping['batch_no'] = idx
        elif '联系方' in header or 'contact' in header_lower or '供应商' in header:
            col_mapping['contact_name'] = idx

    if col_mapping['sku'] is None:
        return _error_resp("Excel格式错误：找不到SKU/物料编码列")
    if col_mapping['quantity'] is None:
        return _error_resp("Excel格式错误：找不到库存/数量列")

    is_batch_mode = col_mapping['batch_no'] is not None

    def _read_cell(row, key):
        ci = col_mapping[key]
        if ci is None or ci >= len(row) or row[ci] is None:
            return None
        return str(row[ci]).strip()

    def _read_int(row, key, default=0):
        ci = col_mapping[key]
        if ci is None or ci >= len(row) or row[ci] is None:
            return default
        return int(row[ci])

    preview_items = []
    new_skus = []
    new_contacts_set = set()
    seen_skus_simple = set()  # 简化模式下追踪已见SKU，检测同SKU多行
    total_in = 0
    total_out = 0
    total_new = 0
    row_count = 0

    with get_db() as conn:
        cursor = conn.cursor()

        # 联系方解析辅助
        def resolve_contact(name):
            if not name:
                return None, None
            cursor.execute('SELECT id FROM contacts WHERE name = ? AND is_disabled = 0', (name,))
            r = cursor.fetchone()
            if r:
                return r['id'], name
            new_contacts_set.add(name)
            return None, name

        for idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if not row[col_mapping['sku']]:
                continue

            row_count += 1
            if row_count > MAX_IMPORT_ROWS:
                return _error_resp(f"数据行数 ({row_count}) 超过限制 ({MAX_IMPORT_ROWS}行)")

            name = _read_cell(row, 'name') or ""
            sku = _read_cell(row, 'sku')
            category = _read_cell(row, 'category') or "未分类"
            unit = _read_cell(row, 'unit') or "个"
            location = _read_cell(row, 'location') or ""
            batch_no_val = _read_cell(row, 'batch_no') or ""
            contact_name_val = _read_cell(row, 'contact_name') or ""

            try:
                import_qty = _read_int(row, 'quantity', 0)
            except (ValueError, TypeError):
                return _error_resp(f"第 {idx} 行【库存数量】格式错误：需要整数，当前值为 '{row[col_mapping['quantity']]}'")

            try:
                safe_stock = _read_int(row, 'safe_stock', 20)
            except (ValueError, TypeError):
                return _error_resp(f"第 {idx} 行【安全库存】格式错误：需要整数，当前值为 '{row[col_mapping['safe_stock']]}'")

            contact_id, contact_name = resolve_contact(contact_name_val) if contact_name_val else (None, None)

            # 查询物料
            cursor.execute('SELECT id, name, quantity FROM materials WHERE sku = ?', (sku,))
            material = cursor.fetchone()

            if is_batch_mode:
                # === 批次模式：每行 = 一个批次 ===
                if material:
                    material_id = material['id']
                    if batch_no_val:
                        # 查找已有批次
                        cursor.execute('SELECT id, quantity FROM batches WHERE batch_no = ? AND material_id = ?', (batch_no_val, material_id))
                        batch = cursor.fetchone()
                        if batch:
                            current_qty = batch['quantity']
                            difference = import_qty - current_qty
                            if difference > 0:
                                operation = 'in'
                                total_in += difference
                            elif difference < 0:
                                operation = 'out'
                                total_out += abs(difference)
                            else:
                                operation = 'none'
                            preview_items.append(ImportPreviewItem(
                                sku=sku, name=material['name'], category=category, unit=unit,
                                safe_stock=safe_stock, location=location,
                                current_quantity=current_qty, import_quantity=import_qty,
                                difference=difference, operation=operation,
                                batch_no=batch_no_val, contact_name=contact_name, contact_id=contact_id,
                            ))
                        else:
                            return _error_resp(f"第 {idx} 行：批次号 '{batch_no_val}' 在物料 '{sku}' 中不存在")
                    else:
                        # 新批次
                        total_in += import_qty
                        preview_items.append(ImportPreviewItem(
                            sku=sku, name=material['name'], category=category, unit=unit,
                            safe_stock=safe_stock, location=location,
                            current_quantity=0, import_quantity=import_qty,
                            difference=import_qty, operation='in',
                            is_batch_new=True, contact_name=contact_name, contact_id=contact_id,
                        ))
                else:
                    # 新SKU + 新批次
                    total_new += 1
                    total_in += import_qty
                    new_item = ImportPreviewItem(
                        sku=sku, name=name, category=category, unit=unit,
                        safe_stock=safe_stock, location=location,
                        current_quantity=None, import_quantity=import_qty,
                        difference=import_qty, operation='new', is_new=True,
                        is_batch_new=True, contact_name=contact_name, contact_id=contact_id,
                    )
                    preview_items.append(new_item)
                    new_skus.append(new_item)
            else:
                # === 简化模式 ===
                # 同一 SKU 出现多次 → 每行作为新批次（不同位置/联系方）
                sku_is_duplicate = sku in seen_skus_simple
                seen_skus_simple.add(sku)

                if sku_is_duplicate:
                    # 重复的 SKU 行：作为新批次入库
                    mat_name = material['name'] if material else name
                    total_in += import_qty
                    preview_items.append(ImportPreviewItem(
                        sku=sku, name=mat_name, category=category, unit=unit,
                        safe_stock=safe_stock, location=location,
                        current_quantity=0, import_quantity=import_qty,
                        difference=import_qty, operation='in',
                        is_batch_new=True,
                        contact_name=contact_name, contact_id=contact_id,
                    ))
                elif material:
                    current_qty = material['quantity']
                    difference = import_qty - current_qty
                    if difference > 0:
                        operation = 'in'
                        total_in += difference
                    elif difference < 0:
                        operation = 'out'
                        total_out += abs(difference)
                    else:
                        operation = 'none'
                    preview_items.append(ImportPreviewItem(
                        sku=sku, name=material['name'], category=category, unit=unit,
                        safe_stock=safe_stock, location=location,
                        current_quantity=current_qty, import_quantity=import_qty,
                        difference=difference, operation=operation,
                        contact_name=contact_name, contact_id=contact_id,
                    ))
                else:
                    total_new += 1
                    new_item = ImportPreviewItem(
                        sku=sku, name=name, category=category, unit=unit,
                        safe_stock=safe_stock, location=location,
                        current_quantity=None, import_quantity=import_qty,
                        difference=import_qty, operation='new', is_new=True,
                        contact_name=contact_name, contact_id=contact_id,
                    )
                    preview_items.append(new_item)
                    new_skus.append(new_item)

        # 查找缺失的SKU（系统中有但导入文件中没有的，且未被禁用的）
        import_skus = {item.sku for item in preview_items}
        cursor.execute('SELECT sku, name, category, quantity FROM materials WHERE is_disabled = 0')
        all_system_skus = cursor.fetchall()

        missing_skus = []
        for row in all_system_skus:
            if row['sku'] not in import_skus:
                missing_skus.append(MissingSkuItem(
                    sku=row['sku'], name=row['name'],
                    category=row['category'] or '未分类', current_quantity=row['quantity']
                ))

        total_missing = len(missing_skus)

    mode_label = "批次模式" if is_batch_mode else "简化模式"
    return ExcelImportPreviewResponse(
        success=True,
        preview=preview_items,
        new_skus=new_skus,
        missing_skus=missing_skus,
        total_in=total_in,
        total_out=total_out,
        total_new=total_new,
        total_missing=total_missing,
        is_batch_mode=is_batch_mode,
        new_contacts=sorted(new_contacts_set),
        message=f'[{mode_label}] 共解析 {len(preview_items)} 条记录，其中新增 {total_new} 条'
                + (f'，有 {total_missing} 个SKU不在导入文件中' if total_missing > 0 else '')
                + (f'，将创建 {len(new_contacts_set)} 个新联系方' if new_contacts_set else '')
    )


@app.post("/api/materials/import-excel/confirm", response_model=ExcelImportResponse)
async def confirm_import_excel(
    request: ExcelImportConfirm,
    current_user: CurrentUser = Depends(require_auth('operate'))
):
    """确认导入，执行变更单（需要operate权限）— 统一创建批次"""
    in_count = 0
    out_count = 0
    new_count = 0
    records_created = 0
    warnings = []
    operator_user_id = current_user.id
    operator = request.operator if request.operator else current_user.get_operator_name()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    with get_db() as conn:
        cursor = conn.cursor()

        # 收集导入文件中的所有SKU
        import_skus = set(item.sku for item in request.changes)

        # 将不在导入文件中的SKU标记为禁用（需显式确认）
        if import_skus:
            placeholders = ','.join(['?' for _ in import_skus])
            if request.confirm_disable_missing_skus:
                cursor.execute(f'UPDATE materials SET is_disabled = 1 WHERE sku NOT IN ({placeholders})', list(import_skus))
            else:
                warnings.append("已跳过禁用导入文件之外的SKU，如需禁用请勾选确认选项后重试。")
            cursor.execute(f'UPDATE materials SET is_disabled = 0 WHERE sku IN ({placeholders})', list(import_skus))

        # 前置：创建新联系方
        contact_name_to_id = {}
        for item in request.changes:
            if item.contact_name and not item.contact_id:
                if item.contact_name not in contact_name_to_id:
                    # 再查一次（可能预览后用户已手动创建）
                    cursor.execute('SELECT id FROM contacts WHERE name = ? AND is_disabled = 0', (item.contact_name,))
                    existing = cursor.fetchone()
                    if existing:
                        contact_name_to_id[item.contact_name] = existing['id']
                    else:
                        cursor.execute(
                            'INSERT INTO contacts (name, is_supplier, created_at) VALUES (?, 1, ?)',
                            (item.contact_name, now)
                        )
                        contact_name_to_id[item.contact_name] = cursor.lastrowid

        def _get_contact_id(item):
            if item.contact_id:
                return item.contact_id
            if item.contact_name and item.contact_name in contact_name_to_id:
                return contact_name_to_id[item.contact_name]
            return None

        def _create_batch(material_id, quantity, location, contact_id):
            """创建新批次并返回 batch_id"""
            batch_no = generate_batch_no(material_id, cursor=cursor)
            cursor.execute('''
                INSERT INTO batches (batch_no, material_id, quantity, initial_quantity, contact_id, location, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (batch_no, material_id, quantity, quantity, contact_id, location, now))
            return cursor.lastrowid

        def _create_record(material_id, rec_type, quantity, reason_suffix, batch_id=None, contact_id=None):
            """创建出入库记录"""
            cursor.execute('''
                INSERT INTO inventory_records
                (material_id, type, quantity, operator, operator_user_id, reason, contact_id, batch_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (material_id, rec_type, quantity, operator, operator_user_id,
                  f"Excel导入: {request.reason}{reason_suffix}", contact_id, batch_id, now))

        if request.is_batch_mode:
            # === 批次模式 ===
            for item in request.changes:
                if item.operation == 'none':
                    # 无变动，仅更新 batch location
                    if item.batch_no and item.location:
                        cursor.execute('SELECT id FROM materials WHERE sku = ?', (item.sku,))
                        mat = cursor.fetchone()
                        if mat:
                            cursor.execute('UPDATE batches SET location = ? WHERE batch_no = ? AND material_id = ?',
                                           (item.location, item.batch_no, mat['id']))
                    continue

                contact_id = _get_contact_id(item)

                if item.is_new:
                    # 新SKU + 新批次
                    if not request.confirm_new_skus:
                        continue
                    cursor.execute('''
                        INSERT OR IGNORE INTO materials (name, sku, category, quantity, unit, safe_stock, location, created_at)
                        VALUES (?, ?, ?, 0, ?, ?, ?, ?)
                    ''', (item.name, item.sku, item.category or '未分类', item.unit or '个',
                          item.safe_stock or 20, item.location or '', now))
                    if cursor.rowcount == 0:
                        cursor.execute('SELECT id FROM materials WHERE sku = ?', (item.sku,))
                        material_id = cursor.fetchone()['id']
                    else:
                        material_id = cursor.lastrowid
                        new_count += 1

                    if item.import_quantity > 0:
                        batch_id = _create_batch(material_id, item.import_quantity, item.location, contact_id)
                        cursor.execute('UPDATE materials SET quantity = quantity + ? WHERE id = ?',
                                       (item.import_quantity, material_id))
                        _create_record(material_id, 'in', item.import_quantity, ' (新建物料)', batch_id, contact_id)
                        in_count += 1
                        records_created += 1
                elif item.is_batch_new:
                    # 已有SKU，新批次
                    cursor.execute('SELECT id FROM materials WHERE sku = ?', (item.sku,))
                    mat = cursor.fetchone()
                    if not mat:
                        continue
                    material_id = mat['id']
                    batch_id = _create_batch(material_id, item.import_quantity, item.location, contact_id)
                    cursor.execute('UPDATE materials SET quantity = quantity + ? WHERE id = ?',
                                   (item.import_quantity, material_id))
                    _create_record(material_id, 'in', item.import_quantity, ' (新批次)', batch_id, contact_id)
                    in_count += 1
                    records_created += 1
                else:
                    # 已有批次有变动
                    cursor.execute('SELECT id FROM materials WHERE sku = ?', (item.sku,))
                    mat = cursor.fetchone()
                    if not mat:
                        continue
                    material_id = mat['id']
                    cursor.execute('SELECT id, quantity FROM batches WHERE batch_no = ? AND material_id = ?',
                                   (item.batch_no, material_id))
                    batch = cursor.fetchone()
                    if not batch:
                        continue

                    diff = item.difference
                    cursor.execute('UPDATE batches SET quantity = ?, location = ? WHERE id = ?',
                                   (item.import_quantity, item.location or '', batch['id']))
                    cursor.execute('UPDATE materials SET quantity = quantity + ? WHERE id = ?',
                                   (diff, material_id))

                    rec_type = 'in' if diff > 0 else 'out'
                    _create_record(material_id, rec_type, abs(diff), '', batch['id'], contact_id)
                    if diff > 0:
                        in_count += 1
                    else:
                        out_count += 1
                    records_created += 1

                # 更新 materials.location 为最新
                if item.location:
                    cursor.execute('UPDATE materials SET location = ? WHERE sku = ?', (item.location, item.sku))
        else:
            # === 简化模式（统一创建批次）===
            for item in request.changes:
                contact_id = _get_contact_id(item)

                if item.is_new:
                    if not request.confirm_new_skus:
                        continue

                    cursor.execute('''
                        INSERT OR IGNORE INTO materials (name, sku, category, quantity, unit, safe_stock, location, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (item.name, item.sku, item.category or '未分类', item.import_quantity,
                          item.unit or '个', item.safe_stock or 20, item.location or '', now))

                    if cursor.rowcount == 0:
                        # SKU已存在，按已有物料处理
                        cursor.execute('SELECT id, quantity FROM materials WHERE sku = ?', (item.sku,))
                        existing = cursor.fetchone()
                        if existing and item.import_quantity != existing['quantity']:
                            material_id = existing['id']
                            diff = item.import_quantity - existing['quantity']
                            cursor.execute('UPDATE materials SET quantity = ? WHERE id = ?', (item.import_quantity, material_id))
                            rec_type = 'in' if diff > 0 else 'out'
                            if diff > 0:
                                batch_id = _create_batch(material_id, abs(diff), item.location, contact_id)
                            else:
                                batch_id = None
                            _create_record(material_id, rec_type, abs(diff), ' (SKU已存在，调整库存)', batch_id, contact_id)
                            records_created += 1
                        new_count += 1
                        continue

                    material_id = cursor.lastrowid
                    if item.import_quantity > 0:
                        batch_id = _create_batch(material_id, item.import_quantity, item.location, contact_id)
                        _create_record(material_id, 'in', item.import_quantity, ' (新建物料)', batch_id, contact_id)
                        records_created += 1
                    new_count += 1
                else:
                    cursor.execute('SELECT id, quantity FROM materials WHERE sku = ?', (item.sku,))
                    material = cursor.fetchone()
                    if not material:
                        continue

                    material_id = material['id']
                    current_qty = material['quantity']

                    # 更新基本信息
                    cursor.execute('''
                        UPDATE materials SET safe_stock = ?, category = ?, unit = ?, location = ? WHERE id = ?
                    ''', (item.safe_stock if item.safe_stock is not None else 20,
                          item.category or '未分类', item.unit or '个', item.location or '', material_id))

                    if item.operation == 'none':
                        continue

                    abs_diff = abs(item.difference)

                    if item.operation == 'in':
                        batch_id = _create_batch(material_id, abs_diff, item.location, contact_id)
                        cursor.execute('UPDATE materials SET quantity = ? WHERE id = ?',
                                       (current_qty + abs_diff, material_id))
                        _create_record(material_id, 'in', abs_diff, '', batch_id, contact_id)
                        in_count += 1
                        records_created += 1
                    elif item.operation == 'out':
                        if current_qty - abs_diff < 0:
                            return ExcelImportResponse(
                                success=False, in_count=in_count, out_count=out_count,
                                new_count=new_count, records_created=records_created,
                                message=f"出库失败：SKU {item.sku} 出库 {abs_diff} 超过当前库存 {current_qty}，已终止导入。"
                            )
                        # FIFO 消耗批次
                        remaining = abs_diff
                        cursor.execute('''
                            SELECT id, quantity FROM batches
                            WHERE material_id = ? AND is_exhausted = 0 AND quantity > 0
                            ORDER BY created_at ASC
                        ''', (material_id,))
                        available_batches = cursor.fetchall()

                        cursor.execute('UPDATE materials SET quantity = ? WHERE id = ?',
                                       (current_qty - abs_diff, material_id))
                        cursor.execute('''
                            INSERT INTO inventory_records
                            (material_id, type, quantity, operator, operator_user_id, reason, contact_id, created_at)
                            VALUES (?, 'out', ?, ?, ?, ?, ?, ?)
                        ''', (material_id, abs_diff, operator, operator_user_id,
                              f"Excel导入: {request.reason}", contact_id, now))
                        record_id = cursor.lastrowid

                        for batch in available_batches:
                            if remaining <= 0:
                                break
                            consume = min(batch['quantity'], remaining)
                            new_batch_qty = batch['quantity'] - consume
                            remaining -= consume
                            cursor.execute('UPDATE batches SET quantity = ?, is_exhausted = ? WHERE id = ?',
                                           (new_batch_qty, 1 if new_batch_qty == 0 else 0, batch['id']))
                            cursor.execute('''
                                INSERT INTO batch_consumptions (record_id, batch_id, quantity, created_at)
                                VALUES (?, ?, ?, ?)
                            ''', (record_id, batch['id'], consume, now))

                        out_count += 1
                        records_created += 1

        conn.commit()
        get_fuzzy_matcher().invalidate_cache()

    warning_text = f" {' '.join(warnings)}" if warnings else ""
    return ExcelImportResponse(
        success=True,
        in_count=in_count,
        out_count=out_count,
        new_count=new_count,
        records_created=records_created,
        message=f'导入完成：{in_count}条入库，{out_count}条出库，{new_count}条新增物料。{warning_text}'.strip()
    )


@app.get("/api/inventory/export-excel")
def export_inventory_records(
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    product_name: Optional[str] = Query(None, description="产品名称"),
    record_type: Optional[str] = Query(None, description="记录类型(in/out)")
):
    """导出出入库记录为Excel（支持筛选，含批次信息）"""
    with get_db() as conn:
        cursor = conn.cursor()

        query = '''
            SELECT r.id, m.name, m.sku, m.category, r.type, r.quantity, r.operator, r.operator_user_id, r.reason, r.created_at,
                   c.name as contact_name, r.batch_id, b.batch_no,
                   u.display_name as operator_display_name, u.username as operator_username
            FROM inventory_records r
            JOIN materials m ON r.material_id = m.id
            LEFT JOIN contacts c ON r.contact_id = c.id
            LEFT JOIN batches b ON r.batch_id = b.id
            LEFT JOIN users u ON r.operator_user_id = u.id
            WHERE 1=1
        '''
        params = []

        if start_date:
            query += ' AND DATE(r.created_at) >= ?'
            params.append(start_date)
        if end_date:
            query += ' AND DATE(r.created_at) <= ?'
            params.append(end_date)
        if product_name:
            query += ' AND m.name LIKE ?'
            params.append(f'%{product_name}%')
        if record_type and record_type != 'all':
            query += ' AND r.type = ?'
            params.append(record_type)

        query += ' ORDER BY r.created_at DESC'
        cursor.execute(query, params)
        records = cursor.fetchall()

        # 为出库记录获取批次消耗详情
        batch_details_map = {}
        for record in records:
            if record['type'] == 'out':
                record_id = record['id']
                cursor.execute('''
                    SELECT b.batch_no, bc.quantity
                    FROM batch_consumptions bc
                    JOIN batches b ON bc.batch_id = b.id
                    WHERE bc.record_id = ?
                    ORDER BY b.created_at ASC
                ''', (record_id,))
                consumptions = cursor.fetchall()
                if consumptions:
                    details = [f"{c['batch_no']}×{c['quantity']}" for c in consumptions]
                    batch_details_map[record_id] = ', '.join(details)

    wb = Workbook()
    ws = wb.active
    ws.title = "出入库记录"

    headers = ['物料名称', '物料编码', '商品类型', '记录类型', '数量', '批次', '联系方', '操作人', '原因', '时间']
    for col, header in enumerate(headers, 1):
        ws.cell(row=1, column=col, value=header)

    for row_idx, record in enumerate(records, 2):
        # 批次信息：入库显示批次号，出库显示消耗详情
        batch_info = ''
        if record['type'] == 'in' and record['batch_no']:
            batch_info = record['batch_no']
        elif record['type'] == 'out':
            batch_info = batch_details_map.get(record['id'], '')

        ws.cell(row=row_idx, column=1, value=record['name'])
        ws.cell(row=row_idx, column=2, value=record['sku'])
        ws.cell(row=row_idx, column=3, value=record['category'])
        ws.cell(row=row_idx, column=4, value='入库' if record['type'] == 'in' else '出库')
        ws.cell(row=row_idx, column=5, value=record['quantity'])
        ws.cell(row=row_idx, column=6, value=batch_info)
        ws.cell(row=row_idx, column=7, value=record['contact_name'] or '')
        # 操作员：优先使用用户表中的显示名称，否则回退到旧的operator字段
        operator_name = record['operator_display_name'] or record['operator_username'] or record['operator']
        ws.cell(row=row_idx, column=8, value=operator_name)
        ws.cell(row=row_idx, column=9, value=record['reason'])
        ws.cell(row=row_idx, column=10, value=record['created_at'])

    # 设置列宽
    column_widths = [22, 18, 14, 12, 10, 28, 16, 14, 24, 22]
    for i, width in enumerate(column_widths, 1):
        ws.column_dimensions[chr(64 + i)].width = width

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f"inventory_records_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.post("/api/inventory/add-record")
async def add_inventory_record(
    http_request: Request,
    request: ManualRecordRequest,
    current_user: CurrentUser = Depends(require_auth('operate'))
):
    """手动新增出入库记录（需要operate权限）- 返回StockInResponse或StockOutResponse"""
    # 使用请求中的operator，如果为空则使用当前用户名
    operator = request.operator if request.operator else current_user.get_operator_name()

    if request.type == 'in':
        result = await stock_in(
            StockOperationRequest(
                product_name=request.product_name,
                quantity=request.quantity,
                reason=request.reason,
                operator=operator,
                contact_id=request.contact_id,
                location=request.location,
                batch_no=request.batch_no,
            ),
            current_user
        )
        # 入库成功且填写了库位时，更新产品汇总库位
        if result.success and request.location:
            with get_db() as conn:
                conn.execute(
                    'UPDATE materials SET location = ? WHERE name = ?',
                    (request.location, request.product_name)
                )
                conn.commit()
        return result
    elif request.type == 'out':
        return await stock_out(
            http_request,
            StockOperationRequest(
                product_name=request.product_name,
                quantity=request.quantity,
                reason=request.reason,
                operator=operator,
                contact_id=request.contact_id
            ),
            current_user
        )
    else:
        return StockOperationResponse(
            success=False,
            error="无效的操作类型",
            message="类型必须是 'in' 或 'out'"
        )


# ============ MCP 连接管理 ============

# 全局 MCP 进程管理器实例
mcp_manager = MCPProcessManager()


@app.on_event("startup")
async def startup_mcp_manager():
    """启动时恢复 auto_start 的 MCP 连接"""
    await mcp_manager.start_monitor()

    # 恢复 auto_start 的连接
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM mcp_connections WHERE auto_start = 1')
            rows = cursor.fetchall()
            for row in rows:
                logger.info(f"Auto-starting MCP connection: {row['name']}")
                await mcp_manager.start_connection(
                    row['id'], row['mcp_endpoint'], row['api_key']
                )
                # 更新数据库状态
                status_info = mcp_manager.get_connection_status(row['id'])
                cursor.execute(
                    'UPDATE mcp_connections SET status = ?, updated_at = ? WHERE id = ?',
                    (status_info['status'], datetime.now().isoformat(), row['id'])
                )
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to restore MCP connections: {e}")


@app.on_event("shutdown")
async def shutdown_mcp_manager():
    """关闭时停止所有 MCP 连接"""
    await mcp_manager.stop_all()


def _build_connection_item(row, status_info: dict) -> MCPConnectionItem:
    """从数据库行和实时状态构建响应对象"""
    return MCPConnectionItem(
        id=row['id'],
        name=row['name'],
        mcp_endpoint=row['mcp_endpoint'],
        role=row['role'] or 'operate',
        auto_start=bool(row['auto_start']),
        status=status_info.get('status', row['status'] or 'stopped'),
        error_message=status_info.get('error_message') or row['error_message'],
        restart_count=status_info.get('restart_count', row['restart_count'] or 0),
        pid=status_info.get('pid'),
        uptime_seconds=status_info.get('uptime_seconds'),
        created_at=row['created_at'],
        updated_at=row['updated_at']
    )


@app.get("/api/mcp/connections")
async def list_mcp_connections(
    current_user: CurrentUser = Depends(require_auth('admin'))
):
    """列出所有MCP连接（含实时状态）"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM mcp_connections ORDER BY created_at DESC')
        rows = cursor.fetchall()

    items = []
    for row in rows:
        status_info = mcp_manager.get_connection_status(row['id'])
        items.append(_build_connection_item(row, status_info))

    return items


@app.post("/api/mcp/connections", response_model=MCPConnectionResponse)
async def create_mcp_connection(
    request: CreateMCPConnectionRequest,
    current_user: CurrentUser = Depends(require_auth('admin'))
):
    """创建MCP连接（自动创建关联的API Key）"""
    conn_id = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()

    # 验证角色
    role = request.role if request.role in ('admin', 'operate', 'view') else 'operate'

    # 自动生成 API Key
    api_key_plain = generate_api_key()
    key_hash = hash_api_key(api_key_plain)

    with get_db() as conn:
        cursor = conn.cursor()

        # 创建关联的 API Key（is_system=1，不在用户管理中显示）
        cursor.execute('''
            INSERT INTO api_keys (key_hash, name, role, user_id, is_system, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
        ''', (key_hash, f'Agent: {request.name}', role, current_user.id, now))

        # 创建 MCP 连接记录
        cursor.execute('''
            INSERT INTO mcp_connections (id, name, mcp_endpoint, api_key, role, auto_start, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'stopped', ?, ?)
        ''', (conn_id, request.name, request.mcp_endpoint, api_key_plain, role,
              1 if request.auto_start else 0, now, now))
        conn.commit()

    # 如果 auto_start，立即启动
    if request.auto_start:
        await mcp_manager.start_connection(conn_id, request.mcp_endpoint, api_key_plain)
        status_info = mcp_manager.get_connection_status(conn_id)
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE mcp_connections SET status = ?, updated_at = ? WHERE id = ?',
                (status_info['status'], datetime.now().isoformat(), conn_id)
            )
            conn.commit()

    # 获取创建的记录
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM mcp_connections WHERE id = ?', (conn_id,))
        row = cursor.fetchone()

    status_info = mcp_manager.get_connection_status(conn_id)
    return MCPConnectionResponse(
        success=True,
        message="连接已创建",
        connection=_build_connection_item(row, status_info)
    )


@app.put("/api/mcp/connections/{conn_id}", response_model=MCPConnectionResponse)
async def update_mcp_connection(
    conn_id: str,
    request: UpdateMCPConnectionRequest,
    current_user: CurrentUser = Depends(require_auth('admin'))
):
    """修改MCP连接配置"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM mcp_connections WHERE id = ?', (conn_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="连接不存在")

        updates = []
        params = []
        key_hash = hash_api_key(row['api_key'])

        if request.name is not None:
            updates.append('name = ?')
            params.append(request.name)
            # 同步更新关联的 API Key 名称
            cursor.execute(
                'UPDATE api_keys SET name = ? WHERE key_hash = ?',
                (f'Agent: {request.name}', key_hash)
            )
        if request.mcp_endpoint is not None:
            updates.append('mcp_endpoint = ?')
            params.append(request.mcp_endpoint)
        if request.role is not None and request.role in ('admin', 'operate', 'view'):
            updates.append('role = ?')
            params.append(request.role)
            # 同步更新关联的 API Key 角色
            cursor.execute(
                'UPDATE api_keys SET role = ? WHERE key_hash = ?',
                (request.role, key_hash)
            )
        if request.auto_start is not None:
            updates.append('auto_start = ?')
            params.append(1 if request.auto_start else 0)

        if updates:
            updates.append('updated_at = ?')
            params.append(datetime.now().isoformat())
            params.append(conn_id)
            cursor.execute(
                f'UPDATE mcp_connections SET {", ".join(updates)} WHERE id = ?',
                params
            )
            conn.commit()

        cursor.execute('SELECT * FROM mcp_connections WHERE id = ?', (conn_id,))
        row = cursor.fetchone()

    status_info = mcp_manager.get_connection_status(conn_id)
    return MCPConnectionResponse(
        success=True,
        message="连接已更新",
        connection=_build_connection_item(row, status_info)
    )


@app.delete("/api/mcp/connections/{conn_id}")
async def delete_mcp_connection(
    conn_id: str,
    current_user: CurrentUser = Depends(require_auth('admin'))
):
    """删除MCP连接（先停止）"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM mcp_connections WHERE id = ?', (conn_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="连接不存在")

    # 先停止进程
    await mcp_manager.stop_connection(conn_id)
    mcp_manager.remove_connection(conn_id)

    # 删除数据库记录及关联的 API Key
    with get_db() as conn:
        cursor = conn.cursor()
        api_key_plain = row['api_key']
        if api_key_plain:
            key_hash = hash_api_key(api_key_plain)
            cursor.execute('DELETE FROM api_keys WHERE key_hash = ?', (key_hash,))
        cursor.execute('DELETE FROM mcp_connections WHERE id = ?', (conn_id,))
        conn.commit()

    return {"success": True, "message": "连接已删除"}


@app.post("/api/mcp/connections/{conn_id}/start", response_model=MCPConnectionResponse)
async def start_mcp_connection(
    conn_id: str,
    current_user: CurrentUser = Depends(require_auth('admin'))
):
    """启动MCP连接"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM mcp_connections WHERE id = ?', (conn_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="连接不存在")

    success = await mcp_manager.start_connection(
        conn_id, row['mcp_endpoint'], row['api_key']
    )

    status_info = mcp_manager.get_connection_status(conn_id)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE mcp_connections SET status = ?, error_message = ?, restart_count = 0, updated_at = ? WHERE id = ?',
            (status_info['status'], status_info.get('error_message'),
             datetime.now().isoformat(), conn_id)
        )
        conn.commit()
        cursor.execute('SELECT * FROM mcp_connections WHERE id = ?', (conn_id,))
        row = cursor.fetchone()

    return MCPConnectionResponse(
        success=success,
        message="连接已启动" if success else "启动失败",
        connection=_build_connection_item(row, status_info)
    )


@app.post("/api/mcp/connections/{conn_id}/stop", response_model=MCPConnectionResponse)
async def stop_mcp_connection(
    conn_id: str,
    current_user: CurrentUser = Depends(require_auth('admin'))
):
    """停止MCP连接"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM mcp_connections WHERE id = ?', (conn_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="连接不存在")

    await mcp_manager.stop_connection(conn_id)

    status_info = mcp_manager.get_connection_status(conn_id)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE mcp_connections SET status = ?, error_message = NULL, updated_at = ? WHERE id = ?',
            ('stopped', datetime.now().isoformat(), conn_id)
        )
        conn.commit()
        cursor.execute('SELECT * FROM mcp_connections WHERE id = ?', (conn_id,))
        row = cursor.fetchone()

    return MCPConnectionResponse(
        success=True,
        message="连接已停止",
        connection=_build_connection_item(row, status_info)
    )


@app.post("/api/mcp/connections/{conn_id}/restart", response_model=MCPConnectionResponse)
async def restart_mcp_connection(
    conn_id: str,
    current_user: CurrentUser = Depends(require_auth('admin'))
):
    """重启MCP连接"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM mcp_connections WHERE id = ?', (conn_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="连接不存在")

    success = await mcp_manager.restart_connection(
        conn_id, row['mcp_endpoint'], row['api_key']
    )

    status_info = mcp_manager.get_connection_status(conn_id)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE mcp_connections SET status = ?, error_message = ?, restart_count = 0, updated_at = ? WHERE id = ?',
            (status_info['status'], status_info.get('error_message'),
             datetime.now().isoformat(), conn_id)
        )
        conn.commit()
        cursor.execute('SELECT * FROM mcp_connections WHERE id = ?', (conn_id,))
        row = cursor.fetchone()

    return MCPConnectionResponse(
        success=success,
        message="连接已重启" if success else "重启失败",
        connection=_build_connection_item(row, status_info)
    )


@app.get("/api/mcp/connections/{conn_id}/logs")
async def get_mcp_connection_logs(
    conn_id: str,
    lines: int = Query(default=50, ge=1, le=200),
    current_user: CurrentUser = Depends(require_auth('admin'))
):
    """获取MCP连接的最近日志"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM mcp_connections WHERE id = ?', (conn_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="连接不存在")

    logs = mcp_manager.get_logs(conn_id, lines)
    return {"logs": logs}


# ============ 前端静态文件（all-in-one 部署）============

STATIC_DIR = os.environ.get('STATIC_DIR', '')
if not STATIC_DIR:
    # 自动检测：Docker 环境 /app/static 或开发环境 ../frontend/dist
    for candidate in ['/app/static', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'frontend', 'dist')]:
        if os.path.isdir(candidate) and os.path.exists(os.path.join(candidate, 'index.html')):
            STATIC_DIR = candidate
            break

if STATIC_DIR and os.path.isdir(STATIC_DIR):
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse

    _index_html = os.path.join(STATIC_DIR, 'index.html')

    # /assets 静态资源（带缓存）
    _assets_dir = os.path.join(STATIC_DIR, 'assets')
    if os.path.isdir(_assets_dir):
        app.mount("/assets", StaticFiles(directory=_assets_dir), name="static-assets")

    # SPA catch-all: 非 /api 非 /assets 的请求都返回 index.html
    @app.get("/{path:path}")
    async def serve_spa(path: str):
        # 先尝试精确匹配静态文件（如 favicon.ico）
        file_path = os.path.join(STATIC_DIR, path)
        if path and os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(_index_html)

    logger.info(f"Serving frontend from {STATIC_DIR}")


# ============ 启动配置 ============

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get('PORT', 2124)))
