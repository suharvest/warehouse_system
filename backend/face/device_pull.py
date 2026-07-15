"""Backend-direct identity pull from the physical device (B 方案).

session 模式不再信任 LLM 转发的 speaker 身份参数（可被提示注入伪造）。改由后端
用 verify-mcp 请求头里的明文 API Key 唯一定位设备（api_key → mcp_connections →
mcp_agent_devices），再局域网直连设备 ``GET /api/face/current-speaker`` 现场取识别
结果。身份来源从"LLM 的话"变成"设备的 HTTP 响应"，注入面消除。

契约（plan §9）：
  GET http://<ip>:<port>/api/face/current-speaker?fresh=0|1
  Header X-Face-Token: <每设备 pull_token>
  200 {valid,name,subject_id,similarity,mode,age_ms} — 唯一"可用身份"出口
  401 token 不符 · 409 状态冲突 · 429/503 忙 · 超时 → 一律 fail-closed（deny）
"""
from __future__ import annotations

import ipaddress
import logging
from typing import Optional

import httpx
from sqlalchemy import and_, select

from db import get_engine
from metadata import mcp_agent_devices as _t_devices
from metadata import mcp_connections as _t_conns

logger = logging.getLogger(__name__)

# fresh=1 现场拍 ~6s（Himax 冷启 + 推理）。留在 MCP→后端 8s 预算内。
PULL_TIMEOUT = 6.5
DEVICE_HTTP_PORT_DEFAULT = 80


class PullDevice:
    __slots__ = ("ip", "port", "pull_token")

    def __init__(self, ip: str, port: int, pull_token: str):
        self.ip = ip
        self.port = port
        self.pull_token = pull_token


def _ip_is_safe(ip: str) -> bool:
    """拒绝回环/链路本地/组播等 SSRF 目标（与 push-faces 的设备校验同源）。"""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (addr.is_loopback or addr.is_link_local or addr.is_multicast
                or addr.is_unspecified or addr.is_reserved)


def resolve_pull_device(api_key_plain: Optional[str],
                        tenant_id: Optional[int],
                        device_id: Optional[str] = None) -> Optional[PullDevice]:
    """由 verify-mcp 请求的明文 API Key 唯一定位该连接下的设备。

    每个 MCP 连接创建时生成独立的明文 key 存于 ``mcp_connections.api_key``。但该列
    仅 NOT NULL、索引非唯一，为防脏数据/跨租户串绑，这里**同时按 tenant_id 过滤**
    （与认证得到的 current_user.tenant_id 一致），且**匹配到 >1 连接即视为异常 deny**。
    返回 None（→ 上游 fail-closed deny）当：无 key/无 tenant / 连接不存在或多于一条 /
    无设备 / 设备缺 ip 或 pull_token / ip 不安全 / 端口非法 / 同连接多设备但未透传
    可信 device_id 消歧。
    """
    if not api_key_plain or tenant_id is None:
        return None
    with get_engine().connect() as conn:
        crows = conn.execute(
            select(_t_conns.c.id).where(and_(
                _t_conns.c.api_key == api_key_plain,
                _t_conns.c.tenant_id == tenant_id,
            ))
        ).fetchall()
        if len(crows) != 1:
            # 0 条 → key/tenant 不匹配；>1 条 → api_key 非唯一的脏数据，一律 fail-closed。
            if len(crows) > 1:
                logger.warning("resolve_pull_device: api_key matched %d connections "
                               "for tenant %s — ambiguous, denying", len(crows), tenant_id)
            return None
        conn_id = crows[0].id
        preds = [_t_devices.c.connection_id == conn_id]
        if device_id is not None:
            preds.append(_t_devices.c.device_id == device_id)
        drows = conn.execute(
            select(_t_devices.c.ip, _t_devices.c.port, _t_devices.c.pull_token)
            .where(and_(*preds))
        ).fetchall()
    if len(drows) != 1:
        # 0 台 → 没设备；>1 台 → 歧义（须透传可信 device_id），都 fail-closed。
        if len(drows) > 1:
            logger.warning("resolve_pull_device: %d devices on connection, "
                           "need device_id to disambiguate", len(drows))
        return None
    d = drows[0]
    ip = (d.ip or "").strip()
    token = (d.pull_token or "").strip()
    if not ip or not token or not _ip_is_safe(ip):
        return None
    try:
        port = int(d.port or DEVICE_HTTP_PORT_DEFAULT)
    except (TypeError, ValueError):
        return None
    if not (1 <= port <= 65535):
        return None
    return PullDevice(ip, port, token)


async def pull_current_speaker(device: PullDevice, *, fresh: int = 1) -> Optional[dict]:
    """局域网直连设备取当前说话人身份。

    返回设备的 JSON（含 valid/name/subject_id/...）当 HTTP 200；否则返回 None
    （401/409/429/503/超时/传输错误/非法 JSON）→ 上游 fail-closed deny。绝不放行。
    trust_env=False：设备在 LAN，必须直连其 IP，不能走系统代理。
    """
    url = f"http://{device.ip}:{device.port}/api/face/current-speaker"
    try:
        async with httpx.AsyncClient(timeout=PULL_TIMEOUT, trust_env=False) as client:
            resp = await client.get(
                url,
                params={"fresh": fresh},
                headers={"X-Face-Token": device.pull_token},
            )
    except httpx.TimeoutException:
        logger.warning("pull_current_speaker timeout: %s", url)
        return None
    except httpx.RequestError as e:
        logger.warning("pull_current_speaker transport error %s: %s", url, e)
        return None
    if resp.status_code != 200:
        logger.info("pull_current_speaker %s -> HTTP %s", url, resp.status_code)
        return None
    try:
        return resp.json()
    except Exception:
        logger.warning("pull_current_speaker bad JSON from %s", url)
        return None
