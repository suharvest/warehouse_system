"""Orchestration: load config, capture, infer, match, log decision."""
from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime
from typing import List, Optional

from sqlalchemy import select, and_

from db import get_engine
from metadata import tenant_face_config, tenant_face_operation_rules

from . import endpoint_client
from .endpoint_client import FaceEndpointError
from .matcher import topk_match
from .models import Decision, FaceConfig, FaceRule

logger = logging.getLogger("warehouse.face")

# 「仅首次验证（之后免验）」= verify_frequency 'session' 的按对话缓存，两种
# mode（local 设备拉身份 / lan 端点比对）都生效。local 模式设备每轮对话把
# conv_seq 递增（CaptureCurrentSpeaker）；同一对话内首笔操作走现场验证（含屏幕
# 预览），之后同 conv_seq 的操作直接返回缓存（免验、不再拍照/预览）。lan 模式无
# conv_seq 概念（缓存条目 conv_seq=None），退化为 TTL 内免验。
# 键：设备 ip:port（一连接一设备）；无可解析设备时按租户。进程内内存，重启即
# 失效（首笔重验，安全）。TTL 上限兜底：即使一直不换对话，超过 N 秒也强制重验，
# 防长会话无限免验。verify_frequency='always' 时完全不读不写本缓存。
_verify_once_cache: dict = {}
_VERIFY_ONCE_TTL_S = 600  # 10 分钟安全上限

# 懒重算失败集合：(subject_id, model_tag) 首次重算失败（spoof/no_face/端点错）后
# 进程内不再重试，防止每次验证都对同一张坏照片/坏端点重试轰炸。进程重启自然清空
# （给修复后的照片/端点重试机会）。
_reembed_failed: set = set()
# 懒重算进行中集合：并发验证/后台任务对同一 (subject_id, model_tag) 去重，
# 防止重复推理 + 重复插行。单事件循环内 add/discard 无竞态。
_reembed_inflight: set = set()

# 验证路径单次请求最多兜底补算的 subject 数：人数多时首验不能被批量重算拖到
# 秒级（100 人 × ~30ms 推理就是 3s+），超出部分靠配置变更触发的后台批量任务。
LAZY_RECOMPUTE_PER_REQUEST = 3

# 配置变更触发的后台批量补算任务：key=(tenant_id, mode, endpoint)（任务内才探得
# model_tag），同 key 只跑一个（幂等可重入）；status 按 tenant 暴露给管理 API。
_recompute_tasks: dict = {}
_recompute_status: dict = {}


# ── data access helpers ──

def _load_config(conn, tenant_id: int) -> Optional[FaceConfig]:
    """Phase 2b: read via SQLAlchemy Core. ``conn`` retained for signature
    compatibility but unused here (SA reads share the same DB)."""
    stmt = select(
        tenant_face_config.c.tenant_id,
        tenant_face_config.c.enabled,
        tenant_face_config.c.mode,
        tenant_face_config.c.endpoint,
        tenant_face_config.c.auth_token,
        tenant_face_config.c.embedding_model_tag,
        tenant_face_config.c.min_confidence,
        # NOTE: verify_mode 列已 deprecated（仅为旧版本回滚保留），这里刻意不读。
        tenant_face_config.c.verify_frequency,
    ).where(tenant_face_config.c.tenant_id == tenant_id)
    with get_engine().connect() as sa_conn:
        row = sa_conn.execute(stmt).first()
    if not row:
        return None
    return FaceConfig(
        tenant_id=row.tenant_id,
        enabled=bool(row.enabled),
        mode=row.mode,
        endpoint=row.endpoint,
        auth_token=row.auth_token,
        embedding_model_tag=row.embedding_model_tag,
        min_confidence=float(row.min_confidence or 0.65),
        verify_frequency=row.verify_frequency or "always",
    )


def _parse_id_list(raw) -> List[int]:
    """Accept list | str | None. SA returns JSON columns already decoded as
    Python lists; the legacy sqlite3 path returned the raw JSON string."""
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        try:
            return [int(x) for x in raw]
        except Exception:
            return []
    if isinstance(raw, str):
        try:
            v = json.loads(raw)
            return [int(x) for x in v] if isinstance(v, list) else []
        except Exception:
            return []
    return []


def _pick_rule(conn, tenant_id: int, warehouse_id: Optional[int], operation: str) -> Optional[FaceRule]:
    """Warehouse-specific rule wins over the tenant default (warehouse_id IS NULL).

    Phase 2b: read via SQLAlchemy Core. ``conn`` retained for signature
    compatibility but unused here.
    """
    cols = (
        tenant_face_operation_rules.c.id,
        tenant_face_operation_rules.c.tenant_id,
        tenant_face_operation_rules.c.warehouse_id,
        tenant_face_operation_rules.c.operation,
        tenant_face_operation_rules.c.require_face,
        tenant_face_operation_rules.c.allowed_subject_ids,
        tenant_face_operation_rules.c.min_confidence_override,
    )
    with get_engine().connect() as sa_conn:
        if warehouse_id is not None:
            stmt = select(*cols).where(
                and_(
                    tenant_face_operation_rules.c.tenant_id == tenant_id,
                    tenant_face_operation_rules.c.operation == operation,
                    tenant_face_operation_rules.c.warehouse_id == warehouse_id,
                )
            ).limit(1)
            row = sa_conn.execute(stmt).first()
            if row:
                return _row_to_rule(row)
        stmt = select(*cols).where(
            and_(
                tenant_face_operation_rules.c.tenant_id == tenant_id,
                tenant_face_operation_rules.c.operation == operation,
                tenant_face_operation_rules.c.warehouse_id.is_(None),
            )
        ).limit(1)
        row = sa_conn.execute(stmt).first()
    return _row_to_rule(row) if row else None


def _row_to_rule(row) -> FaceRule:
    return FaceRule(
        id=row.id,
        tenant_id=row.tenant_id,
        warehouse_id=row.warehouse_id,
        operation=row.operation,
        require_face=bool(row.require_face),
        allowed_subject_ids=_parse_id_list(row.allowed_subject_ids),
        min_confidence_override=row.min_confidence_override,
    )


def _resolve_speaker_subject(
    conn,
    *,
    tenant_id: int,
    speaker_subject_id: Optional[int],
    speaker_name: Optional[str],
) -> Optional[int]:
    """Resolve the session-mode speaker to a tenant-scoped face_subjects.id.

    Precedence is strict, not a fallback chain: when ``speaker_subject_id`` is
    supplied it is the ONLY authority — if it fails to resolve (wrong tenant /
    deactivated) we return None (→ deny), we do NOT fall back to a name lookup.
    Falling back would let a device-reported id for tenant B's subject silently
    match a same-named subject in the caller's tenant. Name lookup is used only
    when no id was provided at all. Both paths require an active subject.
    Returns None when nothing resolves (e.g. device had no valid match).
    """
    cur = conn.cursor()
    if speaker_subject_id is not None:
        cur.execute(
            "SELECT id FROM face_subjects "
            "WHERE id = ? AND tenant_id = ? AND is_active = 1",
            (speaker_subject_id, tenant_id),
        )
        row = cur.fetchone()
        if row:
            return int(row["id"])
        # id given but unresolved → hard deny; never degrade to name matching.
        return None
    if speaker_name:
        cur.execute(
            "SELECT id FROM face_subjects "
            "WHERE tenant_id = ? AND name = ? AND is_active = 1 "
            "ORDER BY id ASC LIMIT 2",
            (tenant_id, speaker_name),
        )
        rows = cur.fetchall()
        # 同租户无姓名唯一约束：查到多条同名 → 无法确定是谁 → deny（绝不任取第一条，
        # 否则设备只回 name 时可能把甲授权成同名的乙）。恰好一条才放行。
        if len(rows) == 1:
            return int(rows[0]["id"])
    return None


def _log_decision(
    conn,
    *,
    request_id: Optional[str],
    user_id: int,
    matched_subject_id: Optional[int],
    tenant_id: int,
    warehouse_id: Optional[int],
    operation: str,
    confidence: Optional[float],
    decision: str,
    failure_reason: Optional[str],
) -> None:
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO face_auth_logs
                (request_id, user_id, matched_subject_id, tenant_id, warehouse_id,
                 operation, confidence, decision, failure_reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                user_id,
                matched_subject_id,
                tenant_id,
                warehouse_id,
                operation,
                confidence,
                decision,
                failure_reason,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()
    except Exception:
        logger.exception("failed to write face_auth_logs")


async def ensure_enrollments_for_model(
    conn, tenant_id: int, model_tag: str, infer_image,
    *, limit: Optional[int] = None, progress=None,
) -> int:
    """懒重算核心（双向通用）：为缺 ``model_tag`` embedding 的 subject 补行。

    统一原则：enrollment 按 (subject, model_tag) 多行共存；任何模型缺行、且该
    subject 存在任一带 ``source_image_b64`` 注册照片的 active enrollment，就用
    照片现算并缓存为新 enrollment（复制 applies_to_warehouse_ids，
    enrolled_by=NULL）。切换识别模式（local WE2 ↔ lan Hailo/Jetson）永不要求
    用户重录。两个调用方：

    * lan 验证前置（``_ensure_model_enrollments``）— 端点 /infer 重算；
    * push-faces 下发前置（routers/mcp_admin.py）— 进程内 WE2 模拟器重算
      （lan 模式注册的 subject 切回本机模式下发时补 128D 行）。

    ``infer_image``: async callable(image_b64) -> {"embedding": bytes,
    "model_tag": str}。

    新行的 source_image_b64 置 NULL：原始照片保留在源 enrollment 上即可——本函数
    扫描的是「任一带照片的 active enrollment」，未来再换模型仍能从源行重算；复制
    base64 大字段只会成倍膨胀 DB，无信息增益。

    失败（spoof / no_face / 端点错 / 模拟器缺失）warn 并跳过该 subject，同时记入
    进程内 ``_reembed_failed``，避免每次调用重复轰炸；进程重启自然重试。
    ``limit``：单次调用最多尝试补算的 subject 数（验证路径兜底限量用，防人数多时
    首次验证被拖到秒级）；None = 不限（后台批量 / push 下发）。
    ``progress``：可选 callable(done, total)，后台任务用来更新进度。
    正在被其他协程补算的 (subject, model) 直接跳过（进行中去重，防并发验证
    重复算同一 subject）。返回本次插入的行数。
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT e.subject_id AS subject_id,
               e.source_image_b64 AS img,
               e.applies_to_warehouse_ids AS applies
        FROM face_enrollments e
        JOIN face_subjects s ON s.id = e.subject_id
        WHERE e.id IN (
            SELECT MAX(id) FROM face_enrollments
            WHERE tenant_id = ? AND is_active = 1
              AND source_image_b64 IS NOT NULL AND source_image_b64 != ''
            GROUP BY subject_id
        )
          AND s.is_active = 1
          AND e.subject_id NOT IN (
              SELECT subject_id FROM face_enrollments
              WHERE tenant_id = ? AND model_tag = ? AND is_active = 1
          )
        """,
        (tenant_id, tenant_id, model_tag),
    )
    rows = cur.fetchall()
    if not rows:
        return 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    inserted = 0
    attempted = 0
    total = len(rows)
    for i, row in enumerate(rows):
        sid = int(row["subject_id"])
        key = (sid, model_tag)
        if key in _reembed_failed or key in _reembed_inflight:
            if progress:
                progress(i + 1, total)
            continue
        if limit is not None and attempted >= limit:
            break  # 剩余交给后台批量任务
        attempted += 1
        _reembed_inflight.add(key)
        try:
            result = await infer_image(row["img"])
        except FaceEndpointError as e:
            logger.warning(
                "lazy re-embed skipped: subject=%s target_model=%s reason=%s",
                sid, model_tag, e,
            )
            _reembed_failed.add(key)
            continue
        finally:
            _reembed_inflight.discard(key)
        got_tag = result["model_tag"]
        cur.execute(
            """
            INSERT INTO face_enrollments
                (subject_id, tenant_id, model_tag, embedding, source_image_b64,
                 applies_to_warehouse_ids, is_active, enrolled_at, enrolled_by)
            VALUES (?, ?, ?, ?, NULL, ?, 1, ?, NULL)
            """,
            (sid, tenant_id, got_tag, result["embedding"], row["applies"], now),
        )
        conn.commit()  # 每条独立提交：后台任务中途被杀不丢已算好的行
        inserted += 1
        if got_tag != model_tag:
            # 推理方返回的 model_tag 与请求侧不一致（端点又换了模型？）：插入的
            # 数据本身有效，但对当前 target 仍缺 → 标失败避免每次调用死循环重算。
            logger.warning(
                "lazy re-embed model_tag mismatch: subject=%s want=%s got=%s",
                sid, model_tag, got_tag,
            )
            _reembed_failed.add(key)
        if progress:
            progress(i + 1, total)
        if attempted % 10 == 0:
            logger.info(
                "lazy re-embed progress: tenant=%s model=%s %d/%d",
                tenant_id, model_tag, i + 1, total,
            )
    if inserted:
        logger.info(
            "lazy re-embed: tenant=%s model=%s inserted %d enrollment(s)",
            tenant_id, model_tag, inserted,
        )
    return inserted


async def _ensure_model_enrollments(conn, cfg: FaceConfig, tenant_id: int, model_tag: str) -> None:
    """lan 验证前置的懒重算兜底：限量（LAZY_RECOMPUTE_PER_REQUEST）现算，
    避免人数多时首次验证被拖爆；全量补算靠配置变更触发的后台任务。"""
    if cfg.mode == "local" or not cfg.endpoint:
        # 无端点重算通道。刻意不标失败集合：端点配置好后自动恢复重算。
        return

    async def infer_image(image_b64):
        return await endpoint_client.infer(cfg, image_b64)

    await ensure_enrollments_for_model(
        conn, tenant_id, model_tag, infer_image,
        limit=LAZY_RECOMPUTE_PER_REQUEST,
    )


def get_recompute_status(tenant_id: int) -> Optional[dict]:
    """后台批量补算进度：{model_tag, done, total, running}；从未跑过 → None。"""
    st = _recompute_status.get(tenant_id)
    return dict(st) if st is not None else None


def start_background_recompute(
    tenant_id: int, mode: Optional[str], endpoint: Optional[str],
    auth_token: Optional[str],
) -> bool:
    """配置变更（mode/endpoint 变化 → 生效 model_tag 可能变化）触发的后台批量
    补算（主路径）。串行逐 subject 重算，不挤占在线验证的 NPU；同
    (tenant, mode, endpoint) 只跑一个任务（幂等可重入）；进度写
    ``_recompute_status``，每 10 个 subject 记一条 info 日志（核心函数内）。
    返回是否真的启动了新任务。需在运行中的事件循环里调用（FastAPI handler）。
    """
    import asyncio

    key = (tenant_id, mode or "", endpoint or "")
    existing = _recompute_tasks.get(key)
    if existing is not None and not existing.done():
        return False
    task = asyncio.get_running_loop().create_task(
        _bg_recompute(tenant_id, mode, endpoint, auth_token)
    )
    _recompute_tasks[key] = task
    return True


async def _bg_recompute(
    tenant_id: int, mode: Optional[str], endpoint: Optional[str],
    auth_token: Optional[str],
) -> None:
    st = {"model_tag": None, "done": 0, "total": 0, "running": True}
    _recompute_status[tenant_id] = st
    try:
        # 解析当前生效的 model_tag + 推理通道
        if mode == "local":
            model_tag = endpoint_client.LOCAL_MODEL_TAG

            async def infer_image(img):
                return endpoint_client._infer_local(img)
        else:
            if not endpoint:
                return
            try:
                info = await endpoint_client.health(endpoint, auth_token)
            except FaceEndpointError as e:
                logger.warning(
                    "bg re-embed aborted, health probe failed: tenant=%s ep=%s (%s)",
                    tenant_id, endpoint, e,
                )
                return
            model_tag = info.get("model_tag")
            if not model_tag:
                logger.warning(
                    "bg re-embed aborted, endpoint reports no model_tag: %s", endpoint)
                return
            cfg = FaceConfig(
                tenant_id=tenant_id, enabled=True, mode=mode,
                endpoint=endpoint, auth_token=auth_token,
            )

            async def infer_image(img):
                return await endpoint_client.infer(cfg, img)

        st["model_tag"] = model_tag

        def progress(done, total):
            st["done"], st["total"] = done, total

        # 后台任务独立开自己的 sqlite 连接（不与请求 conn 共享线程）
        import database
        conn = database.get_db_connection()
        try:
            inserted = await ensure_enrollments_for_model(
                conn, tenant_id, model_tag, infer_image, progress=progress,
            )
            logger.info(
                "bg re-embed finished: tenant=%s model=%s inserted=%d (%d/%d)",
                tenant_id, model_tag, inserted, st["done"], st["total"],
            )
        finally:
            conn.close()
    except Exception:
        logger.exception("bg re-embed crashed: tenant=%s", tenant_id)
    finally:
        st["running"] = False


# ── public API ──

async def enroll_face(
    conn,
    *,
    subject_id: int,
    tenant_id: int,
    images_b64: Optional[List[str]] = None,
    precomputed: Optional[List[dict]] = None,
    applies_to_warehouse_ids: Optional[List[int]] = None,
    enrolled_by: Optional[int] = None,
) -> dict:
    """Persist face enrollments for a subject.

    Two ingress paths (pick one — pass both raises):

    * ``images_b64``: server-side path. Warehouse posts each image to the
      tenant's ``/infer`` (face_rec_api Hailo/Jetson/RKNN) and stores the
      returned embedding.
    * ``precomputed``: device-side path (e.g. Himax WE2). Each item is
      ``{"embedding_bytes": <bytes>, "model_tag": <str>}`` already computed
      on the device's NPU; warehouse just persists it. No /infer call,
      and ``cfg.endpoint`` is not required.

    Returns: ``{count, ids}``.
    Raises FaceEndpointError on config / subject / endpoint failures.
    """
    if images_b64 and precomputed:
        raise FaceEndpointError("ambiguous_enroll_input")
    images_b64 = images_b64 or []
    precomputed = precomputed or []
    if not images_b64 and not precomputed:
        return {"count": 0, "ids": []}

    cfg = _load_config(conn, tenant_id)
    if images_b64:
        # Server-side inference: local mode runs the bundled WE2 simulator
        # in-process (no endpoint); any other mode needs a configured endpoint.
        if cfg is None:
            raise FaceEndpointError("endpoint_not_configured")
        if cfg.mode != "local" and not cfg.endpoint:
            raise FaceEndpointError("endpoint_not_configured")

    # Verify the subject belongs to this tenant
    cur = conn.cursor()
    cur.execute(
        "SELECT tenant_id FROM face_subjects WHERE id = ?",
        (subject_id,),
    )
    row = cur.fetchone()
    if not row or int(row["tenant_id"]) != int(tenant_id):
        raise FaceEndpointError("subject_not_in_tenant")

    applies_raw = json.dumps(applies_to_warehouse_ids) if applies_to_warehouse_ids else None
    inserted_ids: List[int] = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for img in images_b64:
        result = await endpoint_client.infer(cfg, img)
        cur.execute(
            """
            INSERT INTO face_enrollments
                (subject_id, tenant_id, model_tag, embedding, source_image_b64,
                 applies_to_warehouse_ids, is_active, enrolled_at, enrolled_by)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (subject_id, tenant_id, result["model_tag"], result["embedding"],
             img, applies_raw, now, enrolled_by),
        )
        inserted_ids.append(cur.lastrowid)

    for item in precomputed:
        emb_bytes = item.get("embedding_bytes")
        model_tag = item.get("model_tag") or (cfg.embedding_model_tag if cfg else None) or "unknown"
        if not emb_bytes:
            raise FaceEndpointError("precomputed_missing_embedding")
        cur.execute(
            """
            INSERT INTO face_enrollments
                (subject_id, tenant_id, model_tag, embedding, source_image_b64,
                 applies_to_warehouse_ids, is_active, enrolled_at, enrolled_by)
            VALUES (?, ?, ?, ?, NULL, ?, 1, ?, ?)
            """,
            (subject_id, tenant_id, model_tag, emb_bytes,
             applies_raw, now, enrolled_by),
        )
        inserted_ids.append(cur.lastrowid)

    conn.commit()
    return {"count": len(inserted_ids), "ids": inserted_ids}


async def verify_mcp_face(
    conn,
    *,
    tenant_id: int,
    user_id: int,
    warehouse_id: Optional[int],
    operation: str,
    image_b64: str = "",
    embedding_bytes: Optional[bytes] = None,
    embedding_model_tag: Optional[str] = None,
    speaker_subject_id: Optional[int] = None,
    speaker_name: Optional[str] = None,
    pull_device: "Optional[object]" = None,
    request_id: Optional[str] = None,
) -> Decision:
    """Short-circuit verification ladder for an MCP tool call.

    Order: config -> rule -> obtain embedding -> match -> threshold ->
    allow-list. The matched **subject** is the authorization unit; the
    calling system user is just identified for audit purposes.

    Two ingress paths for the embedding (pick one):

    * ``image_b64``: warehouse calls the tenant's ``/infer`` (face_rec_api).
    * ``embedding_bytes`` + ``embedding_model_tag``: device already ran
      inference on its own NPU (e.g. Himax WE2) and posts the result
      directly. ``cfg.endpoint`` is not consulted on this path.
    """
    cfg = _load_config(conn, tenant_id)
    if cfg is None or not cfg.enabled:
        decision = Decision(status="skipped", failure_reason="feature_disabled")
        _log_decision(
            conn, request_id=request_id, user_id=user_id, matched_subject_id=None,
            tenant_id=tenant_id, warehouse_id=warehouse_id, operation=operation,
            confidence=None, decision=decision.status, failure_reason=decision.failure_reason,
        )
        return decision

    rule = _pick_rule(conn, tenant_id, warehouse_id, operation)
    if rule is None or not rule.require_face:
        decision = Decision(status="skipped", failure_reason="rule_not_required")
        _log_decision(
            conn, request_id=request_id, user_id=user_id, matched_subject_id=None,
            tenant_id=tenant_id, warehouse_id=warehouse_id, operation=operation,
            confidence=None, decision=decision.status, failure_reason=decision.failure_reason,
        )
        return decision

    # ── 验证链路只看 mode ────────────────────────────────────────────────
    #   mode='local' → 后端直连设备拉身份（B 方案，原 verify_mode='session' 分支）
    #   mode='lan'   → 端点 /infer 重比对（原 verify_mode='interface' 分支）
    # verify_frequency 只控制会话缓存（'session' 首验后免验 / 'always' 每次都验），
    # 对两种 mode 都生效。旧 verify_mode 列 deprecated，代码不再读。
    verify_once = cfg.verify_frequency == "session"
    cache_key = (
        f"{pull_device.ip}:{pull_device.port}" if pull_device is not None
        else f"tenant:{tenant_id}"
    )

    def _finish_pass(matched, confidence, reason):
        decision = Decision(
            status="pass", failure_reason=reason,
            matched_subject_id=matched, confidence=confidence,
        )
        _log_decision(
            conn, request_id=request_id, user_id=user_id, matched_subject_id=matched,
            tenant_id=tenant_id, warehouse_id=warehouse_id, operation=operation,
            confidence=confidence, decision=decision.status,
            failure_reason=decision.failure_reason,
        )
        return decision

    # ── 「仅首次验证」缓存命中检查（verify_frequency='session'，两种 mode 通用）──
    # local 模式缓存条目带设备 conv_seq：用 fresh=0（零硬件动作）廉价读当前
    # conv_seq 判断是否还是那轮对话；lan 模式条目 conv_seq=None → TTL 内免验。
    # 命中后仅复查该 subject 仍活跃 + 仍在白名单（规则可能变），不再现场验证；
    # 停用/移出白名单则作废缓存、走下面重验。
    if verify_once:
        cached = _verify_once_cache.get(cache_key)
        if cached is not None and (time.time() - cached["ts"]) <= _VERIFY_ONCE_TTL_S:
            same_session = True
            if pull_device is not None and cached.get("conv_seq") is not None:
                from . import device_pull
                ident0 = await device_pull.pull_current_speaker(pull_device, fresh=0)
                cur_seq = ident0.get("conv_seq") if isinstance(ident0, dict) else None
                same_session = isinstance(cur_seq, int) and cur_seq == cached["conv_seq"]
            if same_session:
                matched = _resolve_speaker_subject(
                    conn, tenant_id=tenant_id,
                    speaker_subject_id=cached["subject_id"], speaker_name=None,
                )
                if matched is not None and (
                    not rule.allowed_subject_ids or matched in rule.allowed_subject_ids
                ):
                    return _finish_pass(matched, None, "session_cached")
            _verify_once_cache.pop(cache_key, None)

    # ── mode='local'：后端直连设备拉身份（B 方案） ────────────────────────
    # The identity is NOT taken from LLM-forwarded speaker_* params (those are
    # LLM-visible → forgeable by prompt injection). Instead the backend pulls it
    # straight from the physical device over the LAN (fresh capture each op), so
    # the trust root is "the device's HTTP response", not "the model's word".
    # speaker_subject_id / speaker_name are ignored here on purpose.
    # Anything short of a live, resolvable, allowed identity → deny (fail-closed).
    # 仅 local 模式走"拉身份"（设备本地 Himax 识别 → 直接信任 subject_id）。
    # lan 模式无图不再回退到设备本地识别，而是下方"后端拉一张 JPEG → 端点强模型
    # 比对"（option 3：拍照决策统一在后端、按规则驱动，且 lan 坚持用端点强模型）。
    if cfg.mode == "local":
        def _session_deny(reason: str, matched=None) -> Decision:
            d = Decision(status="deny", failure_reason=reason, matched_subject_id=matched)
            _log_decision(
                conn, request_id=request_id, user_id=user_id, matched_subject_id=matched,
                tenant_id=tenant_id, warehouse_id=warehouse_id, operation=operation,
                confidence=None, decision=d.status, failure_reason=d.failure_reason,
            )
            return d

        if pull_device is None:
            # No device resolvable for this API key → cannot verify → deny.
            return _session_deny("device_unresolved")

        from . import device_pull

        # ── 现场验证（首笔 / 缓存未命中 / 缓存失效）：fresh=1 现拍 + 预览 ──────
        ident = await device_pull.pull_current_speaker(pull_device, fresh=1)
        # 严格判定：valid 必须是布尔 True（truthy 字符串/数字不算），否则一律 deny。
        if not isinstance(ident, dict) or ident.get("valid") is not True:
            # HTTP error / timeout / busy / no face / stranger → deny.
            return _session_deny("device_no_identity")

        # Resolve the device-reported identity to a tenant-scoped subject. Prefer
        # subject_id (local mode); fall back to name only when the device gave no
        # id (lan mode is name-keyed, subject_id=0). Same strict rule as elsewhere:
        # a provided id that fails to resolve does NOT degrade to name matching.
        dev_sid = ident.get("subject_id") or None
        matched = _resolve_speaker_subject(
            conn, tenant_id=tenant_id,
            speaker_subject_id=dev_sid,
            speaker_name=(ident.get("name") if not dev_sid else None),
        )
        if matched is None:
            return _session_deny("speaker_unresolved")
        if rule.allowed_subject_ids and matched not in rule.allowed_subject_ids:
            return _session_deny("not_in_allow_list", matched)

        raw_conf = ident.get("similarity")
        confidence = (
            float(raw_conf)
            if isinstance(raw_conf, (int, float))
            and not isinstance(raw_conf, bool)
            and math.isfinite(raw_conf)
            else None
        )
        # 仅 verify_frequency='session' 缓存本轮对话的验证结果，供后续操作免验；
        # 'always' 不写缓存（每次都现场验证）。conv_seq 缺失时退化为 TTL 内免验。
        if verify_once:
            conv_seq = ident.get("conv_seq")
            _verify_once_cache[cache_key] = {
                "conv_seq": conv_seq if isinstance(conv_seq, int) else None,
                "subject_id": matched, "ts": time.time(),
            }
        return _finish_pass(matched, confidence, "session_verified")

    # ── mode='lan'（含 mode 未设默认）：端点接口比对，fail-closed ─────────
    # Obtain embedding: either supplied by device (WE2 path) or via /infer.
    def _lan_deny(reason: str) -> Decision:
        d = Decision(status="deny", failure_reason=reason)
        _log_decision(
            conn, request_id=request_id, user_id=user_id, matched_subject_id=None,
            tenant_id=tenant_id, warehouse_id=warehouse_id, operation=operation,
            confidence=None, decision=d.status, failure_reason=d.failure_reason,
        )
        return d

    if embedding_bytes:
        model_tag = embedding_model_tag or cfg.embedding_model_tag or "unknown"
        query_bytes = embedding_bytes
    else:
        # 图片来源：优先调用方注入的 image_b64（旧 xiaozhi 运行时 face_* 注入，兼容保留），
        # 否则后端按规则现拉一张 JPEG（option 3：拍照决策在后端、跟规则走）。
        img_b64 = image_b64
        if not img_b64:
            if pull_device is None:
                return _lan_deny("device_unresolved")  # 无图又定位不到设备 → 无法验证
            from . import device_pull
            jpeg = await device_pull.pull_image(pull_device)
            if not jpeg:
                return _lan_deny("device_no_identity")  # 抓图失败（忙/无脸/超时/错误）
            import base64 as _b64
            img_b64 = _b64.b64encode(jpeg).decode("ascii")
        try:
            result = await endpoint_client.infer(cfg, img_b64)
        except FaceEndpointError as e:
            return _lan_deny(str(e) if str(e) else "endpoint_unreachable")
        model_tag = result["model_tag"]
        query_bytes = result["embedding"]

    # 懒重算兜底：换模型后老 subject 只有旧 model_tag 的 embedding 时，用注册照片
    # 现场重算出当前模型的 embedding 再比对。任何异常都不阻塞验证主链路。
    try:
        await _ensure_model_enrollments(conn, cfg, tenant_id, model_tag)
    except Exception:
        logger.exception("lazy re-embedding pass failed (non-fatal)")

    matches = topk_match(
        conn,
        tenant_id=tenant_id,
        warehouse_id=warehouse_id,
        model_tag=model_tag,
        query_emb_bytes=query_bytes,
        k=1,
    )
    if not matches:
        decision = Decision(status="deny", failure_reason="no_match")
        _log_decision(
            conn, request_id=request_id, user_id=user_id, matched_subject_id=None,
            tenant_id=tenant_id, warehouse_id=warehouse_id, operation=operation,
            confidence=None, decision=decision.status, failure_reason=decision.failure_reason,
        )
        return decision

    best = matches[0]
    threshold = rule.min_confidence_override if rule.min_confidence_override is not None else cfg.min_confidence

    if best.confidence < threshold:
        decision = Decision(
            status="deny", failure_reason="low_confidence",
            confidence=best.confidence, matched_subject_id=best.subject_id,
        )
        _log_decision(
            conn, request_id=request_id, user_id=user_id, matched_subject_id=best.subject_id,
            tenant_id=tenant_id, warehouse_id=warehouse_id, operation=operation,
            confidence=best.confidence, decision=decision.status, failure_reason=decision.failure_reason,
        )
        return decision

    if rule.allowed_subject_ids and best.subject_id not in rule.allowed_subject_ids:
        decision = Decision(
            status="deny", failure_reason="not_in_allow_list",
            confidence=best.confidence, matched_subject_id=best.subject_id,
        )
        _log_decision(
            conn, request_id=request_id, user_id=user_id, matched_subject_id=best.subject_id,
            tenant_id=tenant_id, warehouse_id=warehouse_id, operation=operation,
            confidence=best.confidence, decision=decision.status, failure_reason=decision.failure_reason,
        )
        return decision

    # lan 模式的「仅首次验证」：首验通过后写缓存（无 conv_seq 概念 → conv_seq=None，
    # TTL 内免验），后续操作在上方缓存命中检查处直接 session_cached 放行。
    if verify_once:
        _verify_once_cache[cache_key] = {
            "conv_seq": None, "subject_id": best.subject_id, "ts": time.time(),
        }
    decision = Decision(
        status="pass", failure_reason=None,
        confidence=best.confidence, matched_subject_id=best.subject_id,
    )
    _log_decision(
        conn, request_id=request_id, user_id=user_id, matched_subject_id=best.subject_id,
        tenant_id=tenant_id, warehouse_id=warehouse_id, operation=operation,
        confidence=best.confidence, decision=decision.status, failure_reason=None,
    )
    return decision
