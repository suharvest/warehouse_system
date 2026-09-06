"""并发入库不再撞批次号（2026-09-05 harvest-pi 压测暴露的 409）。

压测记录见 ``evaluation/runs/2026-09-05-load/results.md`` §库存更新：并发 5 时
77.4% 的 stock-in 返回 409，并发 ≥10 时 100%。根因是 ``generate_batch_no``
"SELECT 当天最大序号 → +1 → INSERT"：并发请求读到的是同一份已提交状态
（竞争者的 INSERT 还在未提交事务里），于是都算出同一个号，撞
``(batch_no, warehouse_id)`` 唯一约束；调用方那 5 次重试只是把同一次读重复
5 遍，一次也躲不开。

修法是把取号换成 ``batch_no_sequences`` 上的原子 ``UPDATE last_seq + 1``。
本用例用 50 个线程打同一个物料的 stock-in，断言一个 409 都没有、且 50 个批次号
互不相同。跑在旧实现上会有大量 409。
"""
import os
import threading

import pytest

_IS_MYSQL = bool(os.environ.get('DATABASE_URL')) and not os.environ.get(
    'DATABASE_URL', ''
).startswith('sqlite')

CONCURRENCY = 50


def test_concurrent_stock_in_same_material_has_no_409(
    app_instance, admin_client, sample_material, monkeypatch
):
    """50 线程打同一个物料的 stock-in，一个 409 都不能有。

    取号被一个 barrier 包住：50 个线程都拿到号之后才允许往下走去 INSERT。这是
    harvest-pi 上单请求 64ms~1.9s 的延迟放大出来的交错（results.md §库存更新），
    在本机毫秒级往返里靠自然竞争碰不稳定。

    这条是**端到端不变量守卫**，不是根因判别用例：旧实现在 Mac + SQLite 上也能过
    ——SQLite 把写事务串行化，重试那一轮读到的已提交状态里已经有前一个线程落库的
    号，重试把它救回来了；results.md 也写明这个边界只在目标设备的真实延迟下暴露。
    判别用例是下面的 ``test_allocator_hands_out_distinct_numbers_under_threads``：
    旧实现在那里 200 次并发取号只产出 1 个号。
    """
    import app as app_module
    from fastapi.testclient import TestClient

    real_gen = app_module.generate_batch_no
    alloc_barrier = threading.Barrier(CONCURRENCY)
    first_call = threading.local()

    def gen_then_wait(*args, **kwargs):
        bn = real_gen(*args, **kwargs)
        if not getattr(first_call, "done", False):
            first_call.done = True
            try:
                alloc_barrier.wait(timeout=30)
            except threading.BrokenBarrierError:
                pass
        return bn

    monkeypatch.setattr(app_module, "generate_batch_no", gen_then_wait)

    cookies = dict(admin_client.cookies)
    material_name = sample_material['name']
    wh_id = sample_material['warehouse_id']

    results = [None] * CONCURRENCY
    barrier = threading.Barrier(CONCURRENCY)

    def worker(idx):
        # 每个线程一个 TestClient：TestClient 内部的 portal 不适合跨线程共享。
        c = TestClient(app_instance)
        c.cookies.update(cookies)
        try:
            barrier.wait(timeout=30)  # 尽量让 50 个请求同时进入取号
            resp = c.post("/api/materials/stock-in", json={
                "product_name": material_name,
                "quantity": 1,
                "reason_category": "purchase",
                "warehouse_id": wh_id,
            })
            body = None
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            results[idx] = (resp.status_code, body)
        except Exception as exc:  # noqa: BLE001 - 记录下来给断言看
            results[idx] = ("EXC", repr(exc))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(CONCURRENCY)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)

    statuses = [r[0] if r else "NORESULT" for r in results]
    conflicts = [r for r in results if r and r[0] == 409]
    assert not conflicts, (
        f"并发 {CONCURRENCY} 下出现 {len(conflicts)} 个 409 批次号冲突："
        f"{conflicts[:3]}"
    )
    assert all(s == 200 for s in statuses), (
        f"并发 {CONCURRENCY} 下有非 200 响应：状态分布="
        f"{ {s: statuses.count(s) for s in set(statuses)} }；样例="
        f"{[r for r in results if not r or r[0] != 200][:3]}"
    )

    batch_nos = [r[1]['batch']['batch_no'] for r in results
                 if isinstance(r[1], dict) and (r[1].get('batch') or {}).get('batch_no')]
    assert len(batch_nos) == CONCURRENCY, (
        f"只有 {len(batch_nos)}/{CONCURRENCY} 个响应带回 batch_no"
    )
    assert len(set(batch_nos)) == CONCURRENCY, (
        f"批次号重复：{CONCURRENCY} 次入库只产生 {len(set(batch_nos))} 个不同批次号"
    )


@pytest.mark.skipif(_IS_MYSQL, reason="直接读 sqlite 计数器表；MySQL 上由上面的端到端用例覆盖")
def test_allocator_hands_out_distinct_numbers_under_threads(default_warehouse_id):
    """取号器本身的并发断言：200 次并发取号必须拿到 200 个不同序号。"""
    from database import generate_batch_no

    n = 200
    out = []
    lock = threading.Lock()
    barrier = threading.Barrier(20)

    def worker():
        barrier.wait(timeout=30)
        for _ in range(n // 20):
            bn = generate_batch_no(1, warehouse_id=default_warehouse_id)
            with lock:
                out.append(bn)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)

    assert len(out) == n, f"只完成了 {len(out)}/{n} 次取号"
    assert len(set(out)) == n, f"取号重复：{n} 次只产生 {len(set(out))} 个不同批次号"
