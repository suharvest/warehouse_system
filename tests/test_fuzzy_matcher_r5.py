"""R5: FuzzyMatcher incremental invalidation + thread safety tests.

Correctness tests pass against the current code as a baseline.
Granularity / thread-safety / pinyin-cache tests are R5-only and will
fail prior to implementation.
"""
import threading
import time
import uuid

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_matcher():
    from fuzzy_match import FuzzyMatcher
    from database import get_db_connection
    return FuzzyMatcher(get_db_connection)


def _seed_material(name, sku=None, warehouse_id=1, tenant_id=1):
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    sku = sku or f"S-{uuid.uuid4().hex[:8]}"
    cur.execute(
        "INSERT INTO materials (name, sku, category, quantity, unit, "
        "safe_stock, location, warehouse_id, tenant_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (name, sku, "T", 0, "pcs", 0, "", warehouse_id, tenant_id),
    )
    mid = cur.lastrowid
    conn.commit()
    conn.close()
    return mid


def _seed_contact(name, tenant_id=1, warehouse_id=1):
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO contacts (name, is_supplier, is_customer, tenant_id, warehouse_id) "
        "VALUES (?, 1, 0, ?, ?)",
        (name, tenant_id, warehouse_id),
    )
    cid = cur.lastrowid
    conn.commit()
    conn.close()
    return cid


# ---------------------------------------------------------------------------
# 1. Invalidation correctness (must pass on current code AND post-R5)
# ---------------------------------------------------------------------------

class TestInvalidationCorrectness:
    def test_full_invalidate_picks_up_new_material(self, admin_client, default_warehouse_id):
        m = _fresh_matcher()
        # warm cache
        m.search("nonexistent", threshold=70)
        name = f"R5Mat-{uuid.uuid4().hex[:6]}"
        _seed_material(name, warehouse_id=default_warehouse_id)
        m.invalidate_cache()
        results = m.search(name, threshold=70)
        assert any(r["name"] == name for r in results)

    def test_full_invalidate_picks_up_new_contact(self, admin_client, default_warehouse_id):
        m = _fresh_matcher()
        m.search("nonexistent", threshold=70)
        name = f"R5Contact-{uuid.uuid4().hex[:6]}"
        _seed_contact(name, warehouse_id=default_warehouse_id)
        m.invalidate_cache()
        results = m.search(name, entity_type="contact", threshold=70)
        assert any(r["name"] == name for r in results)

    def test_no_args_means_full_invalidate(self, admin_client, default_warehouse_id):
        """Backwards compat: invalidate_cache() with no args = full invalidate."""
        m = _fresh_matcher()
        m.search("warmup", threshold=70)
        name = f"R5Compat-{uuid.uuid4().hex[:6]}"
        _seed_material(name, warehouse_id=default_warehouse_id)
        m.invalidate_cache()  # no args
        results = m.search(name, threshold=70)
        assert any(r["name"] == name for r in results)


# ---------------------------------------------------------------------------
# 2. Granular invalidation (R5-only — baseline-skipped if signature lacks kwargs)
# ---------------------------------------------------------------------------

def _matcher_supports_granular():
    """Detect whether invalidate_cache accepts entity_type kwarg."""
    import inspect
    from fuzzy_match import FuzzyMatcher
    sig = inspect.signature(FuzzyMatcher.invalidate_cache)
    return "entity_type" in sig.parameters


r5_only = pytest.mark.skipif(
    not _matcher_supports_granular(),
    reason="R5 granular invalidate_cache not yet implemented (baseline)",
)


class TestGranularInvalidation:
    @r5_only
    def test_entity_type_only_drops_only_that_partition(self, admin_client, default_warehouse_id):
        m = _fresh_matcher()
        # Seed and warm
        n1 = f"R5GranMat-{uuid.uuid4().hex[:6]}"
        n2 = f"R5GranContact-{uuid.uuid4().hex[:6]}"
        _seed_material(n1, warehouse_id=default_warehouse_id)
        _seed_contact(n2, warehouse_id=default_warehouse_id)
        m.invalidate_cache()
        m.search("warmup", threshold=70)

        # Spy on _build_index_for to count partition rebuilds
        calls = {"material": 0, "contact": 0, "operator": 0}
        original = m._build_partition

        def spy(entity_type, **kw):
            calls[entity_type] = calls.get(entity_type, 0) + 1
            return original(entity_type, **kw)

        m._build_partition = spy

        m.invalidate_cache(entity_type="operator")
        # Force rebuild by searching
        m.search(n1, threshold=70)
        assert calls["operator"] >= 1
        assert calls["material"] == 0, "material partition should NOT have rebuilt"
        assert calls["contact"] == 0, "contact partition should NOT have rebuilt"

    @r5_only
    def test_entity_id_drops_single_row(self, admin_client, default_warehouse_id):
        m = _fresh_matcher()
        name = f"R5SingleDrop-{uuid.uuid4().hex[:6]}"
        mid = _seed_material(name, warehouse_id=default_warehouse_id)
        m.invalidate_cache()
        results = m.search(name, threshold=70)
        assert any(r["entity_id"] == mid for r in results)

        # delete the row, drop just it
        from database import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE materials SET is_disabled = 1 WHERE id = ?", (mid,))
        conn.commit()
        conn.close()

        m.invalidate_cache(entity_type="material", entity_id=mid)
        results = m.search(name, threshold=70)
        assert not any(r["entity_id"] == mid for r in results)


# ---------------------------------------------------------------------------
# 3. Thread safety stress (R5-only)
# ---------------------------------------------------------------------------

class TestThreadSafety:
    @r5_only
    def test_concurrent_search_and_invalidate(self, admin_client, default_warehouse_id):
        m = _fresh_matcher()
        sentinel = f"R5Sentinel-{uuid.uuid4().hex[:8]}"
        _seed_material(sentinel, warehouse_id=default_warehouse_id)
        m.invalidate_cache()
        m.search("warmup", threshold=70)

        errors = []
        stop = threading.Event()

        def reader():
            try:
                while not stop.is_set():
                    res = m.search(sentinel, threshold=70)
                    # sentinel must always survive
                    assert any(r["name"] == sentinel for r in res), "sentinel disappeared"
            except Exception as e:
                errors.append(e)

        def writer():
            try:
                while not stop.is_set():
                    m.invalidate_cache()
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(4)]
        threads += [threading.Thread(target=writer) for _ in range(2)]
        for t in threads:
            t.start()
        time.sleep(1.0)
        stop.set()
        for t in threads:
            t.join(timeout=5)
        assert not errors, f"thread errors: {errors[:3]}"


# ---------------------------------------------------------------------------
# 4. Pinyin cache (R5-only)
# ---------------------------------------------------------------------------

class TestPinyinCache:
    @r5_only
    def test_pinyin_cached_across_searches(self, admin_client, default_warehouse_id, monkeypatch):
        m = _fresh_matcher()
        name = f"R5Pinyin-{uuid.uuid4().hex[:6]}"
        _seed_material(name, warehouse_id=default_warehouse_id)
        m.invalidate_cache()
        m.search(name, threshold=70)  # build pinyin

        import fuzzy_match as fm
        counter = {"n": 0}
        original = fm.lazy_pinyin

        def spy(text, *a, **kw):
            counter["n"] += 1
            return original(text, *a, **kw)

        monkeypatch.setattr(fm, "lazy_pinyin", spy)

        # second & third search shouldn't recompute pinyin for indexed entries
        before = counter["n"]
        m.search(name, threshold=70)
        m.search(name, threshold=70)
        # only the query pinyin should be computed (twice), not per-entry
        # Allow a small slack for query-side pinyin calls.
        assert counter["n"] - before <= 4, (
            f"pinyin called too many times: {counter['n'] - before} (expected <=4)"
        )


# ---------------------------------------------------------------------------
# 5. Performance smoke
# ---------------------------------------------------------------------------

class TestPerformanceSmoke:
    def test_search_latency_under_threshold(self, admin_client, default_warehouse_id):
        from database import get_db_connection
        m = _fresh_matcher()
        # seed a few hundred materials (1000 makes test slow on CI sqlite)
        seeded_ids = []
        for i in range(300):
            seeded_ids.append(_seed_material(
                f"PerfMat{i:04d}-{uuid.uuid4().hex[:4]}",
                warehouse_id=default_warehouse_id,
            ))
        try:
            m.invalidate_cache()
            m.search("warmup", threshold=70)  # build index

            import statistics
            latencies = []
            for _ in range(50):
                t0 = time.perf_counter()
                m.search("PerfMat0123", threshold=70)
                latencies.append((time.perf_counter() - t0) * 1000)
            latencies.sort()
            p95 = latencies[int(len(latencies) * 0.95)]
            # generous threshold; this is a sanity check
            assert p95 < 500, f"p95 latency too high: {p95:.1f}ms (lat={latencies[-3:]})"
        finally:
            # 清理这 300 条 PerfMat：会话级 sqlite 不做 per-test 截断，残留会污染
            # 后续测试的模糊解析置信度（如 test_stock_out 的同名消歧）。
            conn = get_db_connection()
            cur = conn.cursor()
            cur.executemany("DELETE FROM materials WHERE id = ?", [(i,) for i in seeded_ids])
            conn.commit()
            conn.close()
