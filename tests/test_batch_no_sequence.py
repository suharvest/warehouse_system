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

2026-09-06（fix/a2-concurrency）：取号从"读当天最大值 + 1"换成
``batch_no_sequences`` 上的原子自增（并发 409，见 test_batch_no_concurrency.py）。
序列会留空洞——取到号但没落库的调用不会把号还回去——所以断言从"等于最大值 + 1"
改为不变量："新号严格大于当天已存在的所有后缀，且按整数比较"。999 那条回归
依然被覆盖：字符串排序的实现会返回 <= 4 位最大值的号，这里会抓到。
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
    def test_never_reuses_an_existing_number(self, sample_material):
        from database import generate_batch_no
        wh_id = sample_material['warehouse_id']
        base = _current_max_seq(wh_id)
        _insert_batches(sample_material['id'], wh_id, [f'{base + 1:03d}'])

        got = generate_batch_no(sample_material['id'], warehouse_id=wh_id)
        prefix, _, suffix = got.rpartition('-')
        assert prefix == TODAY
        assert int(suffix) > base + 1, f'{got} 撞上了已存在的 {base + 1}'

    def test_consecutive_calls_strictly_increase(self, sample_material):
        from database import generate_batch_no
        wh_id = sample_material['warehouse_id']
        seq = [int(generate_batch_no(sample_material['id'],
                                     warehouse_id=wh_id).rpartition('-')[2])
               for _ in range(5)]
        assert seq == sorted(set(seq)), f'取号不单调或有重复：{seq}'

    def test_sequence_crosses_999_without_colliding(self, sample_material):
        """The regression: 3- and 4-digit suffixes must compare numerically.

        A 3-digit ``-999`` must not out-rank the 4-digit numbers above it.
        """
        from database import generate_batch_no
        wh_id = sample_material['warehouse_id']
        base = max(_current_max_seq(wh_id), 999)
        _insert_batches(sample_material['id'], wh_id,
                        ['999', f'{base + 1}', f'{base + 2}'])

        got = generate_batch_no(sample_material['id'], warehouse_id=wh_id)
        assert int(got.rpartition('-')[2]) > base + 2, (
            f'{got} 没有越过 4 位数的 {base + 2}：说明又在按字符串比较序号'
        )

    def test_malformed_suffixes_are_ignored(self, sample_material):
        from database import generate_batch_no
        wh_id = sample_material['warehouse_id']
        base = _current_max_seq(wh_id)
        _insert_batches(sample_material['id'], wh_id, ['ABC'])

        got = generate_batch_no(sample_material['id'], warehouse_id=wh_id)
        assert got.startswith(f'{TODAY}-')
        assert int(got.rpartition('-')[2]) > base

    def test_rejects_missing_warehouse_id(self, sample_material):
        from database import generate_batch_no
        with pytest.raises(ValueError):
            generate_batch_no(sample_material['id'], warehouse_id=None)
