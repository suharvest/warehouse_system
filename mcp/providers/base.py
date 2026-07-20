"""WMS Provider 抽象基类

定义了 MCP 工具层与 WMS 后端之间的接口。
所有 Provider 必须实现 6 个抽象方法，返回统一的 dict 格式。

内建 Auth 支持：api_key / bearer / basic，
自定义签名类 auth 通过 override get_auth_headers() 或 http_get/http_post 实现。
"""

import base64
import logging
from abc import ABC, abstractmethod

import requests

logger = logging.getLogger("WarehouseMCP")


class BaseProvider(ABC):
    """WMS 后端适配器基类。

    子类只需实现 6 个业务方法，即可对接不同的 WMS 系统。
    通用的 HTTP 和 Auth 逻辑已在基类中实现，子类可按需 override。
    """

    # 子类设置此属性，用于 config.yml 的 provider 字段匹配
    PROVIDER_NAME: str = ""

    def __init__(self, config: dict):
        self.config = config
        self.base_url = config.get("api_base_url", "").rstrip("/")
        self.auth_config = config.get("auth", {})
        # (connect_timeout, read_timeout) — connect is fast (localhost/LAN),
        # read allows for slow DB queries without blocking the pipe indefinitely.
        connect_timeout = config.get("connect_timeout", 5)
        read_timeout = config.get("timeout", 10)
        self.timeout = (connect_timeout, read_timeout)

    # ── 通用 Auth ──

    def get_auth_headers(self) -> dict:
        """根据 config.auth 生成请求头。

        支持的 type：
        - api_key: 自定义 header（默认 X-API-Key）
        - bearer: Authorization: Bearer <token>
        - basic: Authorization: Basic <base64>
        - custom / 其他: 返回空 dict，由子类 override
        """
        auth = self.auth_config
        auth_type = auth.get("type", "")

        if auth_type == "api_key":
            header_name = auth.get("header", "X-API-Key")
            key = auth.get("key", "")
            if key:
                return {header_name: key}
            return {}

        if auth_type == "bearer":
            return {"Authorization": f"Bearer {auth.get('token', '')}"}

        if auth_type == "basic":
            cred = base64.b64encode(
                f"{auth['username']}:{auth['password']}".encode()
            ).decode()
            return {"Authorization": f"Basic {cred}"}

        return {}

    # ── 通用 HTTP ──

    def http_get(self, endpoint: str, params: dict = None) -> dict:
        """GET 请求，自动拼接 base_url、注入 auth headers、处理错误。"""
        try:
            headers = self.get_auth_headers()
            response = requests.get(
                f"{self.base_url}{endpoint}",
                params=params,
                headers=headers,
                timeout=self.timeout,
            )
            data = response.json()
            if response.status_code >= 400:
                return {
                    "success": False,
                    "error": data.get("detail", str(data)),
                    "message": f"API 返回错误 ({response.status_code})",
                }
            return data
        except requests.exceptions.ConnectionError:
            return {
                "success": False,
                "error": "无法连接到后端服务",
                "message": f"请确保后端服务已启动: {self.base_url}",
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"API 请求失败: {e}",
            }

    def http_post(self, endpoint: str, data: dict = None) -> dict:
        """POST 请求，自动拼接 base_url、注入 auth headers、处理错误。"""
        try:
            headers = self.get_auth_headers()
            response = requests.post(
                f"{self.base_url}{endpoint}",
                json=data,
                headers=headers,
                timeout=self.timeout,
            )
            resp_data = response.json()
            if response.status_code >= 400:
                return {
                    "success": False,
                    "error": resp_data.get("detail", str(resp_data)),
                    "detail": resp_data.get("detail"),
                    "message": f"API 返回错误 ({response.status_code})",
                }
            return resp_data
        except requests.exceptions.ConnectionError:
            return {
                "success": False,
                "error": "无法连接到后端服务",
                "message": f"请确保后端服务已启动: {self.base_url}",
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"API 请求失败: {e}",
            }

    # ── 6 个业务方法（子类必须实现） ──

    @abstractmethod
    def resolve_name(self, text: str, entity_type: str = "all") -> dict:
        """模糊名称解析。

        返回: {best_match, confident, candidates}
        """
        ...

    @abstractmethod
    def query_stock(self, product_name: str, show_batches: bool = False) -> dict:
        """查询产品库存。

        返回: {success, product, message}
        show_batches=True 时额外返回 batches 列表
        """
        ...

    @abstractmethod
    def stock_in(
        self,
        product_name: str,
        quantity: int,
        reason_category: str,
        reason_note: str,
        operator: str,
        fuzzy: bool,
        location: str | None = None,
        contact_id: int | None = None,
        variant: str | None = None,
        allow_new_variant: bool = False,
    ) -> dict:
        """产品入库。

        返回: {success, ...}
        """
        ...

    @abstractmethod
    def stock_out(
        self,
        product_name: str,
        quantity: int,
        reason_category: str,
        reason_note: str,
        operator: str,
        fuzzy: bool,
        variant: str | None = None,
        location: str | None = None,
        batch_no: str | None = None,
        location_fuzzy: bool = False,
    ) -> dict:
        """产品出库。

        batch_no 非空时只从该批次扣减（不足报错，不 fallback）。
        location_fuzzy=True 时对 location 做作用域模糊（仅 MCP 使用）。
        返回: {success, ...}
        """
        ...

    @abstractmethod
    def search(
        self,
        query: str | None,
        entity_type: str,
        category: str | None,
        status: str | None,
        contact_type: str | None,
        fuzzy: bool,
        include_batches: bool = False,
        max_results: int = 0,
    ) -> dict:
        """统一搜索。

        返回: {success, count, total, items, message}
        include_batches=True 时物料结果附带 batches 字段
        max_results=0 表示使用配置默认值
        """
        ...

    @abstractmethod
    def get_today_statistics(self) -> dict:
        """当天统计。

        返回: {success, date, statistics, message}
        """
        ...

    # ↓↓↓ 以下两个方法是后续扩展（query_batch / move_batch_location），
    # 提供"未实现"默认值而**不**用 @abstractmethod，以兼容 mcp/providers/custom/
    # 下已存在的第三方 provider（否则它们因 ABC 强制无法实例化）。
    # 新 provider 应当 override 这两个方法；不 override 时 MCP 工具会拿到
    # success=False 的结构化失败响应，由 LLM 走 speak_failed 告知用户。

    def query_batch(self, batch_no: str) -> dict:
        """按批次号查询批次详情（只读）。

        返回: {success, batch, message} 或 {success: false, error, message}
        error="batch_not_found" 表示作用域内确实没有该批次。
        默认实现返回 not_implemented；子类应当 override。
        """
        return {
            "success": False,
            "error": "not_implemented",
            "message": f"当前 Provider 未实现按批次号查询（query_batch）",
        }

    def move_batch_location(
        self,
        batch_no: str,
        new_location: str,
        quantity: int | None = None,
        from_location: str | None = None,
        product_name: str | None = None,
        operator: str = "MCP系统",
    ) -> dict:
        """批次库位移动（支持部分数量拆分移位）。

        quantity 为 None 或等于批次余量 → 整批移位
        quantity 小于批次余量 → 拆分：源批次扣减，目标库位创建同物料新批次
        返回: {success, operation, moved_quantity, source_batch, target_batch, ...}
        默认实现返回 not_implemented；子类应当 override。
        """
        return {
            "success": False,
            "error": "not_implemented",
            "message": f"当前 Provider 未实现批次库位移动（move_batch_location）",
        }
