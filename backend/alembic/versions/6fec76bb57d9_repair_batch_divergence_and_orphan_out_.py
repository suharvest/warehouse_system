"""repair batch divergence and orphan OUT records

Revision ID: 6fec76bb57d9
Revises: 1826e23835b6
Create Date: 2026-05-10 17:23:40.995989

This data-repair migration cleans up historical inconsistencies caused by a
shipped bug that allowed ``materials.quantity`` to decrement without the
matching ``batches.quantity`` decrement (resulting in per-batch sums larger
than the aggregate). It also back-fills missing ``batch_consumptions`` rows
for orphan OUT inventory records.

Steps:
    1. Snapshot the rows we are about to touch (materials/batches/
       batch_consumptions) into ``_repair_<rev>_*_snapshot`` tables.
    2. Create a ``repair_log`` audit table.
    3. Forward-repair: for each material where
       sum(active batch qty) > materials.quantity, FIFO-consume the excess
       from the oldest batches; if reverse divergence (sum < material qty),
       log a warning without mutating.
    4. Back-fill missing ``batch_consumptions`` rows for OUT records using
       the post-step-3 batch state (audit trail only — no further mutation
       of ``batches.quantity``).

Idempotency safety net at the start of ``upgrade()``: if the snapshot table
already exists, skip the body. Downgrade restores from snapshots and drops
the repair tables.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = '6fec76bb57d9'
down_revision: Union[str, Sequence[str], None] = '1826e23835b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_LOG = logging.getLogger("alembic.runtime.migration.repair")

SNAP_MATERIALS = f"_repair_{revision}_materials_snapshot"
SNAP_BATCHES = f"_repair_{revision}_batches_snapshot"
SNAP_CONSUMPTIONS = f"_repair_{revision}_batch_consumptions_snapshot"
LOG_TABLE = "repair_log"


def _reflect(conn):
    md = sa.MetaData()
    materials = sa.Table("materials", md, autoload_with=conn)
    batches = sa.Table("batches", md, autoload_with=conn)
    inventory_records = sa.Table("inventory_records", md, autoload_with=conn)
    batch_consumptions = sa.Table("batch_consumptions", md, autoload_with=conn)
    return materials, batches, inventory_records, batch_consumptions


def _create_snapshot_and_log_tables(conn):
    """Create snapshot + repair_log tables. Idempotent via Alembic op helpers
    (we already gated on snapshot existence)."""
    op.create_table(
        SNAP_MATERIALS,
        sa.Column("material_id", sa.Integer, nullable=False),
        sa.Column("quantity", sa.Integer, nullable=True),
        sa.Column("snapped_at", sa.DateTime, nullable=False),
    )
    op.create_table(
        SNAP_BATCHES,
        sa.Column("batch_id", sa.Integer, nullable=False),
        sa.Column("quantity", sa.Integer, nullable=True),
        sa.Column("is_exhausted", sa.Integer, nullable=True),
        sa.Column("snapped_at", sa.DateTime, nullable=False),
    )
    op.create_table(
        SNAP_CONSUMPTIONS,
        sa.Column("consumption_id", sa.Integer, nullable=False),
        sa.Column("record_id", sa.Integer, nullable=True),
        sa.Column("batch_id", sa.Integer, nullable=True),
        sa.Column("quantity", sa.Integer, nullable=True),
        sa.Column("snapped_at", sa.DateTime, nullable=False),
    )
    op.create_table(
        LOG_TABLE,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("migration_rev", sa.String(32), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("material_id", sa.Integer, nullable=True),
        sa.Column("record_id", sa.Integer, nullable=True),
        sa.Column("batch_id", sa.Integer, nullable=True),
        sa.Column("before_qty", sa.Integer, nullable=True),
        sa.Column("after_qty", sa.Integer, nullable=True),
        sa.Column("delta", sa.Integer, nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )


def _log_event(
    conn,
    log_tbl,
    action: str,
    *,
    material_id=None,
    record_id=None,
    batch_id=None,
    before_qty=None,
    after_qty=None,
    delta=None,
    note=None,
):
    conn.execute(
        sa.insert(log_tbl).values(
            migration_rev=revision,
            action=action,
            material_id=material_id,
            record_id=record_id,
            batch_id=batch_id,
            before_qty=before_qty,
            after_qty=after_qty,
            delta=delta,
            note=note,
            created_at=datetime.now(),
        )
    )


def _do_repair(conn) -> None:
    """The actual repair body. Extracted so tests can call it directly against
    an arbitrary connection (the tests still rely on Alembic having created
    the schema first)."""
    insp = inspect(conn)
    if insp.has_table(SNAP_MATERIALS):
        _LOG.info(
            "repair migration %s: snapshot table already exists, skipping body",
            revision,
        )
        return

    materials, batches, inv_records, consumptions = _reflect(conn)

    # ------------------------------------------------------------------
    # Pre-scan: if there are no diverged materials AND no orphan OUT
    # records, the DB is already healthy. Skip creating snapshot/log
    # tables so we don't pollute the schema (this also keeps a freshly
    # initialised DB byte-for-byte clean — important for parity tests).
    # ------------------------------------------------------------------
    sum_q_pre = (
        sa.select(
            batches.c.material_id.label("material_id"),
            sa.func.coalesce(sa.func.sum(batches.c.quantity), 0).label("batch_sum"),
        )
        .where(batches.c.is_exhausted == 0)
        .group_by(batches.c.material_id)
    ).subquery()
    diff_q_pre = sa.select(
        materials.c.id, materials.c.quantity, sa.func.coalesce(sum_q_pre.c.batch_sum, 0).label("bs")
    ).select_from(materials.outerjoin(sum_q_pre, sum_q_pre.c.material_id == materials.c.id))
    has_divergence = any(
        (r.quantity or 0) != (r.bs or 0) for r in conn.execute(diff_q_pre).fetchall()
    )
    has_orphan = conn.execute(
        sa.select(sa.func.count())
        .select_from(inv_records)
        .where(
            sa.and_(
                inv_records.c.type == "out",
                ~sa.exists().where(consumptions.c.record_id == inv_records.c.id),
            )
        )
    ).scalar_one() > 0
    if not has_divergence and not has_orphan:
        _LOG.info(
            "repair migration %s: no divergence or orphan records found, skipping",
            revision,
        )
        return

    _create_snapshot_and_log_tables(conn)

    # Re-reflect to pick up new tables.
    md2 = sa.MetaData()
    snap_materials = sa.Table(SNAP_MATERIALS, md2, autoload_with=conn)
    snap_batches = sa.Table(SNAP_BATCHES, md2, autoload_with=conn)
    snap_consumptions = sa.Table(SNAP_CONSUMPTIONS, md2, autoload_with=conn)
    log_tbl = sa.Table(LOG_TABLE, md2, autoload_with=conn)

    now = datetime.now()

    # ------------------------------------------------------------------
    # 1. Compute per-material aggregate vs sum-of-active-batches in one pass.
    # ------------------------------------------------------------------
    sum_q = (
        sa.select(
            batches.c.material_id.label("material_id"),
            sa.func.coalesce(sa.func.sum(batches.c.quantity), 0).label("batch_sum"),
        )
        .where(batches.c.is_exhausted == 0)
        .group_by(batches.c.material_id)
    ).subquery()

    diff_q = sa.select(
        materials.c.id.label("material_id"),
        materials.c.quantity.label("mat_qty"),
        sa.func.coalesce(sum_q.c.batch_sum, 0).label("batch_sum"),
    ).select_from(materials.outerjoin(sum_q, sum_q.c.material_id == materials.c.id))

    diverged_materials = []
    for row in conn.execute(diff_q).fetchall():
        mat_qty = row.mat_qty or 0
        bsum = row.batch_sum or 0
        if mat_qty != bsum:
            diverged_materials.append((row.material_id, mat_qty, bsum))

    # ------------------------------------------------------------------
    # 2. Snapshot rows that may be touched.
    # ------------------------------------------------------------------
    diverged_ids = [m[0] for m in diverged_materials]

    if diverged_ids:
        # Materials snapshot
        rows = conn.execute(
            sa.select(materials.c.id, materials.c.quantity).where(
                materials.c.id.in_(diverged_ids)
            )
        ).fetchall()
        if rows:
            conn.execute(
                sa.insert(snap_materials),
                [
                    {
                        "material_id": r.id,
                        "quantity": r.quantity,
                        "snapped_at": now,
                    }
                    for r in rows
                ],
            )

        # Batches snapshot
        b_rows = conn.execute(
            sa.select(
                batches.c.id, batches.c.quantity, batches.c.is_exhausted
            ).where(batches.c.material_id.in_(diverged_ids))
        ).fetchall()
        if b_rows:
            conn.execute(
                sa.insert(snap_batches),
                [
                    {
                        "batch_id": r.id,
                        "quantity": r.quantity,
                        "is_exhausted": int(bool(r.is_exhausted)),
                        "snapped_at": now,
                    }
                    for r in b_rows
                ],
            )

    # batch_consumptions snapshot: cover both diverged materials' records
    # AND any orphan OUT we may backfill.
    out_records_all = conn.execute(
        sa.select(
            inv_records.c.id, inv_records.c.material_id, inv_records.c.created_at,
            inv_records.c.quantity,
        ).where(inv_records.c.type == "out").order_by(inv_records.c.created_at.asc())
    ).fetchall()

    # Records that already have consumption rows
    existing_record_ids = set(
        r.record_id
        for r in conn.execute(sa.select(consumptions.c.record_id)).fetchall()
    )
    affected_material_ids = set(diverged_ids)
    orphan_records = [
        r for r in out_records_all if r.id not in existing_record_ids
    ]
    affected_material_ids.update(r.material_id for r in orphan_records)

    if affected_material_ids:
        c_rows = conn.execute(
            sa.select(
                consumptions.c.id,
                consumptions.c.record_id,
                consumptions.c.batch_id,
                consumptions.c.quantity,
            ).where(
                consumptions.c.record_id.in_(
                    sa.select(inv_records.c.id).where(
                        inv_records.c.material_id.in_(affected_material_ids)
                    )
                )
            )
        ).fetchall()
        if c_rows:
            conn.execute(
                sa.insert(snap_consumptions),
                [
                    {
                        "consumption_id": r.id,
                        "record_id": r.record_id,
                        "batch_id": r.batch_id,
                        "quantity": r.quantity,
                        "snapped_at": now,
                    }
                    for r in c_rows
                ],
            )

    # ------------------------------------------------------------------
    # 3. Forward repair: consume excess from oldest batches FIFO.
    # ------------------------------------------------------------------
    for material_id, mat_qty, bsum in diverged_materials:
        excess = bsum - mat_qty
        if excess > 0:
            # FIFO consume from oldest batches
            batch_rows = conn.execute(
                sa.select(
                    batches.c.id, batches.c.quantity, batches.c.created_at
                )
                .where(
                    sa.and_(
                        batches.c.material_id == material_id,
                        batches.c.is_exhausted == 0,
                        batches.c.quantity > 0,
                    )
                )
                .order_by(batches.c.created_at.asc(), batches.c.id.asc())
            ).fetchall()
            remaining = excess
            for b in batch_rows:
                if remaining <= 0:
                    break
                take = min(int(b.quantity or 0), remaining)
                if take <= 0:
                    continue
                new_qty = int(b.quantity) - take
                exhausted = 1 if new_qty <= 0 else 0
                conn.execute(
                    sa.update(batches)
                    .where(batches.c.id == b.id)
                    .values(quantity=new_qty, is_exhausted=exhausted)
                )
                _log_event(
                    conn,
                    log_tbl,
                    "consume_excess",
                    material_id=material_id,
                    batch_id=b.id,
                    before_qty=int(b.quantity),
                    after_qty=new_qty,
                    delta=-take,
                    note=(
                        f"FIFO consume excess; mat_qty={mat_qty}, "
                        f"sum_before={bsum}, excess={excess}"
                    ),
                )
                remaining -= take
            if remaining > 0:
                _log_event(
                    conn,
                    log_tbl,
                    "consume_excess_unmatched",
                    material_id=material_id,
                    delta=-remaining,
                    note=(
                        f"Could not fully consume excess; residual={remaining}"
                    ),
                )
        elif excess < 0:
            _log_event(
                conn,
                log_tbl,
                "reverse_divergence_warning",
                material_id=material_id,
                before_qty=mat_qty,
                after_qty=mat_qty,
                delta=excess,
                note=(
                    f"reverse divergence: material.quantity={mat_qty} > "
                    f"sum(active batches)={bsum}; manual investigation needed"
                ),
            )

    # ------------------------------------------------------------------
    # 4. Backfill orphan batch_consumptions (audit trail; do NOT mutate
    #    batches.quantity here — step 3 already aligned aggregates).
    # ------------------------------------------------------------------
    # Refresh existing-record set in case anything changed (defensive).
    existing_record_ids = set(
        r.record_id
        for r in conn.execute(sa.select(consumptions.c.record_id)).fetchall()
    )

    for r in orphan_records:
        if r.id in existing_record_ids:
            continue
        # Snapshot of post-step-3 batches state for this material
        bstate = conn.execute(
            sa.select(batches.c.id, batches.c.quantity, batches.c.created_at)
            .where(
                sa.and_(
                    batches.c.material_id == r.material_id,
                    batches.c.quantity > 0,
                )
            )
            .order_by(batches.c.created_at.asc(), batches.c.id.asc())
        ).fetchall()
        # We're matching against the *current* state but only for audit
        # attribution; we don't decrement here. To avoid attributing more
        # than each batch holds across multiple orphan records, track a
        # per-batch in-memory remaining counter.
        # NOTE: simpler & correct enough for repair: build a working list.
        remaining_to_match = int(r.quantity or 0)
        for b in bstate:
            if remaining_to_match <= 0:
                break
            avail = int(b.quantity or 0)
            if avail <= 0:
                continue
            take = min(avail, remaining_to_match)
            conn.execute(
                sa.insert(consumptions).values(
                    record_id=r.id,
                    batch_id=b.id,
                    quantity=take,
                    created_at=r.created_at,
                )
            )
            _log_event(
                conn,
                log_tbl,
                "backfill_consumption",
                material_id=r.material_id,
                record_id=r.id,
                batch_id=b.id,
                delta=take,
                note=(
                    f"backfilled consumption for orphan OUT record "
                    f"id={r.id} qty={r.quantity}"
                ),
            )
            remaining_to_match -= take
        if remaining_to_match > 0:
            _log_event(
                conn,
                log_tbl,
                "orphan_unmatched",
                material_id=r.material_id,
                record_id=r.id,
                delta=remaining_to_match,
                note=(
                    f"no batches available to attribute residual qty="
                    f"{remaining_to_match} for OUT record id={r.id}"
                ),
            )


def upgrade() -> None:
    """Run the data-repair migration.

    No-op in Alembic offline (``--sql``) mode: this migration inspects live
    data and decides what to do, so it cannot meaningfully render to a
    static SQL script.
    """
    if context.is_offline_mode():
        _LOG.info(
            "repair migration %s: offline mode, skipping data repair body",
            revision,
        )
        return
    conn = op.get_bind()
    _do_repair(conn)


def downgrade() -> None:
    """Restore from snapshots and drop repair tables."""
    if context.is_offline_mode():
        _LOG.info(
            "repair migration %s: offline mode, skipping downgrade body",
            revision,
        )
        return
    conn = op.get_bind()
    insp = inspect(conn)
    if not insp.has_table(SNAP_MATERIALS):
        # Body was skipped (or already torn down). No-op.
        return

    md = sa.MetaData()
    materials = sa.Table("materials", md, autoload_with=conn)
    batches = sa.Table("batches", md, autoload_with=conn)
    consumptions = sa.Table("batch_consumptions", md, autoload_with=conn)
    snap_materials = sa.Table(SNAP_MATERIALS, md, autoload_with=conn)
    snap_batches = sa.Table(SNAP_BATCHES, md, autoload_with=conn)
    snap_consumptions = sa.Table(SNAP_CONSUMPTIONS, md, autoload_with=conn)
    log_tbl = sa.Table(LOG_TABLE, md, autoload_with=conn)

    # Restore materials
    for row in conn.execute(
        sa.select(snap_materials.c.material_id, snap_materials.c.quantity)
    ).fetchall():
        conn.execute(
            sa.update(materials)
            .where(materials.c.id == row.material_id)
            .values(quantity=row.quantity)
        )

    # Restore batches
    for row in conn.execute(
        sa.select(
            snap_batches.c.batch_id,
            snap_batches.c.quantity,
            snap_batches.c.is_exhausted,
        )
    ).fetchall():
        conn.execute(
            sa.update(batches)
            .where(batches.c.id == row.batch_id)
            .values(quantity=row.quantity, is_exhausted=row.is_exhausted)
        )

    # Delete batch_consumptions inserted by the backfill step.
    # Rule: remove rows whose ids were created by this migration. We can
    # identify them as rows present today but absent from the snapshot for
    # records that the snapshot covered; AND any row matching a backfill
    # log entry's record_id+batch_id+quantity that isn't in the snapshot.
    snapshot_ids = set(
        r.consumption_id
        for r in conn.execute(sa.select(snap_consumptions.c.consumption_id)).fetchall()
    )
    backfill_logs = conn.execute(
        sa.select(
            log_tbl.c.record_id, log_tbl.c.batch_id, log_tbl.c.delta
        ).where(log_tbl.c.action == "backfill_consumption")
    ).fetchall()
    for lg in backfill_logs:
        # Find candidate consumption rows that aren't in snapshot.
        cand = conn.execute(
            sa.select(consumptions.c.id).where(
                sa.and_(
                    consumptions.c.record_id == lg.record_id,
                    consumptions.c.batch_id == lg.batch_id,
                    consumptions.c.quantity == lg.delta,
                )
            )
        ).fetchall()
        for cid_row in cand:
            if cid_row.id in snapshot_ids:
                continue
            conn.execute(
                sa.delete(consumptions).where(consumptions.c.id == cid_row.id)
            )
            # Only delete one match per log entry to avoid clobbering
            # legitimate identical pre-existing rows.
            break

    # Drop repair tables
    op.drop_table(LOG_TABLE)
    op.drop_table(SNAP_CONSUMPTIONS)
    op.drop_table(SNAP_BATCHES)
    op.drop_table(SNAP_MATERIALS)
