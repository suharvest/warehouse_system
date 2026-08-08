"""
Regression tests for ``generate_batch_no`` sequence allocation.

Field bug (customer site, 2026-08-07): batch_no is ``YYYYMMDD-XXX`` zero-padded
to 3 digits, so once a warehouse creates more than 999 batches in one day the
suffix grows to 4 digits (``-1000``). The old implementation picked the "last"
batch with SQL ``ORDER BY batch_no DESC``, and lexicographically
``'20260807-999' > '20260807-1000'`` — so every subsequent call handed back
``-1000`` again, which already existed, and every import/stock-in that created a
batch died on ``UNIQUE constraint failed: batches.batch_no, batches.warehouse_id``.

Tests never assume an empty ``batches`` table: the suite shares one DB, so each
case reads the current max first and asserts relative to it.
"""
from datetime import datetime

import pytest

TODAY = datetime.now().strftime('%Y%m%d')


def _current_max_seq(warehouse_id):
    """Highest numeric suffix among today's batches in this warehouse (0 if none)."""
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT batch_no FROM batches WHERE batch_no LIKE ? AND warehouse_id = ?",
        (f'{TODAY}-%', warehouse_id),
    )
    rows = cur.fetchall()
    conn.close()
    best = 0
    for row in rows:
        try:
            best = max(best, int(row['batch_no'].split('-')[-1]))
        except ValueError:
            continue
    return best


def _insert_batches(material_id, warehouse_id, suffixes):
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    for suffix in suffixes:
        cur.execute(
            '''INSERT INTO batches (batch_no, material_id, quantity, initial_quantity,
                                    is_exhausted, warehouse_id)
               VALUES (?, ?, 1, 1, 0, ?)''',
            (f'{TODAY}-{suffix}', material_id, warehouse_id),
        )
    conn.commit()
    conn.close()


class TestGenerateBatchNoSequence:
    def test_continues_from_current_max(self, sample_material):
        from database import generate_batch_no
        wh_id = sample_material['warehouse_id']
        base = _current_max_seq(wh_id)
        _insert_batches(sample_material['id'], wh_id, [f'{base + 1:03d}'])

        assert generate_batch_no(sample_material['id'], warehouse_id=wh_id) == \
            f'{TODAY}-{base + 2:03d}'

    def test_sequence_crosses_999_without_colliding(self, sample_material):
        """The regression: 3- and 4-digit suffixes must compare numerically.

        A 3-digit ``-999`` must not out-rank the 4-digit numbers above it.
        """
        from database import generate_batch_no
        wh_id = sample_material['warehouse_id']
        base = max(_current_max_seq(wh_id), 999)
        _insert_batches(sample_material['id'], wh_id,
                        ['999', f'{base + 1}', f'{base + 2}'])

        assert generate_batch_no(sample_material['id'], warehouse_id=wh_id) == \
            f'{TODAY}-{base + 3}'

    def test_malformed_suffixes_are_ignored(self, sample_material):
        from database import generate_batch_no
        wh_id = sample_material['warehouse_id']
        base = _current_max_seq(wh_id)
        _insert_batches(sample_material['id'], wh_id, ['ABC'])

        assert generate_batch_no(sample_material['id'], warehouse_id=wh_id) == \
            f'{TODAY}-{base + 1:03d}'

    def test_rejects_missing_warehouse_id(self, sample_material):
        from database import generate_batch_no
        with pytest.raises(ValueError):
            generate_batch_no(sample_material['id'], warehouse_id=None)
