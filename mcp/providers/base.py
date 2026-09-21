"""WMS Provider 抽象基类

定义了 MCP 工具层与 WMS 后端之间的接口。
所有 Provider 必须实现 6 个抽象方法，返回统一的 dict 格式。

内建 Auth 支持：api_key / bearer / basic，
自定义签名类 auth 通过 override get_auth_headers() 或 http_get/http_post 实现。

内建「后端不可达熔断」：http_get / http_post 遇到传输层错误（连接被拒、
超时、DNS 解析失败）时记一个冷却截止时间，冷却期内的调用不再发请求，直接
返回同一个可播报的失败结构。不熔断时后端整个挂掉，每次工具调用都要先卡满
connect_timeout 才失败，语音侧表现为长时间无响应。
"""

import base64
import logging
import threading
import time
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
        # 后端不可达熔断：冷却秒数可配，0 或负数表示关闭熔断。
        try:
            self.backend_down_cooldown_sec = float(
                config.get("backend_down_cooldown_sec", 20)
            )
        except (TypeError, ValueError):
            self.backend_down_cooldown_sec = 20.0
        # monotonic 时钟的截止时刻；0 表示当前没在熔断中。
        # 用 monotonic 而不是 time.time()：系统时钟被 NTP 回拨时不会把冷却
        # 期拉成几个小时。
        #
        # Provider 实例被多个工具调用线程共享（FastMCP 用 to_thread 跑同步
        # 工具），读-改-写必须加锁：无锁时「过期清零」可能覆盖另一线程刚
        # 写进去的新熔断截止时刻，冷却期直接失效。
        self._backend_down_until = 0.0
        self._backend_lock = threading.Lock()

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

    # ── 后端不可达熔断 ──
    #
    # 只有**传输层**错误才熔断：连接被拒、超时、DNS 解析失败 —— 这些说明
    # 对方整个不在，重试没有意义。HTTP 4xx/5xx 和业务失败（返回体里
    # success=false / code!=0）不熔断：对方是活的，只是这一次请求不成立，
    # 换个参数下一次可能就成功。

    # 两类传输层失败对用户的含义完全不同，不能混成一句话：
    #   - 连不上（ConnectionError / ConnectTimeout）：请求**没发出去**，
    #     后端一定没动过，可以放心重试。
    #   - 读超时（ReadTimeout）：请求已经送达，只是没等到响应。写操作可能
    #     已经在后端执行完了，此时说"未执行"是在撒谎，必须让用户去核对。
    BACKEND_UNREACHABLE_ERROR = "backend_unreachable"
    BACKEND_UNREACHABLE_SAY = "外部系统暂时连不上，请稍后再试或联系管理员"
    BACKEND_TIMEOUT_ERROR = "backend_timeout"
    BACKEND_TIMEOUT_SAY = (
        "外部系统响应超时，执行结果未知，请到系统里核对后再决定是否重试"
    )

    def _backend_unreachable_response(self) -> dict:
        """统一的不可达失败结构。``message`` 会被播报层当作 say 念出去。"""
        return {
            "success": False,
            "error": self.BACKEND_UNREACHABLE_ERROR,
            "message": self.BACKEND_UNREACHABLE_SAY,
        }

    def _backend_timeout_response(self) -> dict:
        """读超时失败结构。``executed="unknown"`` 让播报层别说"未执行"。"""
        return {
            "success": False,
            "error": self.BACKEND_TIMEOUT_ERROR,
            "message": self.BACKEND_TIMEOUT_SAY,
            "executed": "unknown",
        }

    def _backend_down_remaining(self) -> float:
        """还剩多少秒冷却；不在熔断中返回 0。"""
        with self._backend_lock:
            return self._backend_down_remaining_locked()

    def _backend_down_remaining_locked(self) -> float:
        """``_backend_lock`` 已持有时的实现。"""
        until = self._backend_down_until
        if not until:
            return 0.0
        remaining = until - time.monotonic()
        if remaining <= 0:
            # 只在值没被别的线程改过时清零，否则会抹掉刚写进去的新熔断。
            if self._backend_down_until == until:
                self._backend_down_until = 0.0
            return 0.0
        return remaining

    def _short_circuit(self, endpoint: str) -> dict | None:
        """冷却期内返回失败结构（调用方据此跳过请求），否则返回 None。"""
        remaining = self._backend_down_remaining()
        if remaining <= 0:
            return None
        logger.warning(
            f"后端不可达熔断中，跳过请求 {self.base_url}{endpoint}"
            f"（剩余 {remaining:.1f}s）"
        )
        # 短路时请求确实没发出去，沿用 unreachable 语义（明确未执行）。
        return self._backend_unreachable_response()

    def _trip_backend_down(self, endpoint: str, exc: Exception) -> dict:
        """记录熔断截止时刻并返回对应语义的失败结构。"""
        cooldown = self.backend_down_cooldown_sec
        if cooldown > 0:
            with self._backend_lock:
                until = time.monotonic() + cooldown
                # 取较晚的截止时刻：并发失败时不让先写的短冷却被覆盖掉。
                if until > self._backend_down_until:
                    self._backend_down_until = until
        read_timeout = isinstance(exc, requests.exceptions.ReadTimeout)
        logger.warning(
            f"后端{'响应超时' if read_timeout else '不可达'}："
            f"{self.base_url}{endpoint} ({exc})，熔断 {cooldown:.0f}s"
        )
        if read_timeout:
            return self._backend_timeout_response()
        return self._backend_unreachable_response()

    # ── 通用 HTTP ──

    def http_get(self, endpoint: str, params: dict = None) -> dict:
        """GET 请求，自动拼接 base_url、注入 auth headers、处理错误。

        后端处于不可达冷却期时直接返回失败结构，不发请求。
        """
        short = self._short_circuit(endpoint)
        if short is not None:
            return short
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
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            return self._trip_backend_down(endpoint, e)
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"API 请求失败: {e}",
            }

    def http_post(self, endpoint: str, data: dict = None) -> dict:
        """POST 请求，自动拼接 base_url、注入 auth headers、处理错误。

        后端处于不可达冷却期时直接返回失败结构，不发请求。
        """
        short = self._short_circuit(endpoint)
        if short is not None:
            return short
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
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            return self._trip_backend_down(endpoint, e)
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

        ``product`` 各字段的含义由 MCP 播报层（``_wrap_response``）决定，
        自写 Provider 必须照此填，填反了不会报错、只会播错：

        ============== ================================================
        字段            用途（手表/语音端如何呈现）
        ============== ================================================
        name           物料名称。播报主语
        sku            物料编码。用户报编码查询时会被回读
        current_stock  当前库存数量
        unit           单位，缺省"个"
        location       **物理库位**，播报成「位于 XXX」
        variant        **规格/型号**，播报成「名称（XXX）」
        safe_stock     安全库存。低于它时追加告警语
        variant_scoped 可选。仅当 current_stock 是「按规格过滤后的子集」时
                       置 True —— 此时 safe_stock 若是整料阈值，两者不可比，
                       播报层会跳过低库存告警。外接系统若 variant 只是该件
                       的型号、current_stock 就是它自己的库存，**不要设**
        ============== ================================================

        没有 ``spec`` 字段 —— 播报层从不读它，填进去会被静默丢弃。
        规格一律用 ``variant``。

        歧义候选走 ``candidates[].extra``，其中 ``extra.variant`` 同样是
        规格/型号，``extra.location`` 是物理库位。
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
        actual_operator: str | None = None,
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
        allow_partial_fallback: bool = False,
        actual_operator: str | None = None,
    ) -> dict:
        """产品出库。

        batch_no 非空时只从该批次扣减（不足报错，不 fallback）。
        location_fuzzy=True 时对 location 做作用域模糊（仅 MCP 使用）。
        allow_partial_fallback=True 时允许指定批次/库位不足时从其余库存补足；
        默认 False —— 工具层先返回 awaiting_confirm 让用户确认，同意后才带上
        该参数重发。**必须声明**：warehouse_mcp.py 的 stock_out 无条件按关键字
        传入本参数，第三方 Provider 漏掉它会在每次出库时 TypeError。
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

    # ↓↓↓ 外部 ERP 模式下的「作用域探测」（同样是可选扩展，非 @abstractmethod）。
    #
    # 背景：接了外部 WMS 之后，库存数据全在对方，我们这边的租户/仓库跟对方的
    # 租户/仓库**没有任何对应关系**。硬要在本地镜像一套对方的组织结构，只会带来
    # 双重维护和必然的数据漂移。所以改成反过来——让 Provider 把"对方有什么"报上来，
    # 用户在配置智能体时直接选，我们只存选中的原始编码并原样透传，不做任何翻译。
    #
    # 不实现也完全没问题：返回 not_implemented 时，前端会退化成手工填写编码。

    def list_tenants(self) -> dict:
        """列出当前凭据可访问的外部租户/组织（只读探测）。

        返回: {success, items: [{"id": str, "name": str}], message}
        对方系统若没有租户概念，可以不实现，或返回单条占位。
        默认实现返回 not_implemented；需要多租户绑定时子类应当 override。
        """
        return {
            "success": False,
            "error": "not_implemented",
            "message": "当前 Provider 未实现外部租户探测（list_tenants）",
        }

    def list_warehouses(self, tenant_id: str | None = None) -> dict:
        """列出外部仓库（只读探测）。

        Args:
            tenant_id: 已选定的外部租户 ID；对方无租户概念时为 None。

        返回: {success, items: [{"id": str, "name": str}], message}
        默认实现返回 not_implemented；子类应当 override。
        """
        return {
            "success": False,
            "error": "not_implemented",
            "message": "当前 Provider 未实现外部仓库探测（list_warehouses）",
        }

    def list_users(self, tenant_id: str | None = None) -> dict:
        """列出外部系统的用户/账号（只读探测）。

        用途与租户/仓库探测不同：**授权是我方的责任，推不出去**。
        谁能登录、谁能配哪个智能体、谁能改人脸规则，都由我方的
        users(role, tenant_id) + user_warehouses 判定。外部 ERP 模式下库存
        数据虽然全在对方，这份「用户 → 租户/角色」的归属数据仍然必须落在我方，
        否则整个权限体系是空的。本方法用于把对方的账号**导入**为我方用户，
        避免管理员手工照抄一遍。

        注意：导入进来的用户**只承载权限**，与出入库的 `operator`、人脸库都没有
        关联。`operator` 是自由填写的文本，人脸是单独录入的；用户的作用是决定
        谁有权修改这些配置。不要在三者之间建隐式关联。

        Args:
            tenant_id: 已选定的外部租户 ID；对方无租户概念时为 None。

        返回: {success, items: [{"id": str, "name": str, "display_name"?: str}], message}
        默认实现返回 not_implemented；子类应当 override。
        """
        return {
            "success": False,
            "error": "not_implemented",
            "message": "当前 Provider 未实现外部用户探测（list_users）",
        }
