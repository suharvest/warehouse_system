-- 诊断脚本：找出"总库存与批次库存分裂"的物料
-- 只读，不修改任何数据
-- 用法：sqlite3 your.db < diagnose_batch_divergence.sql
--      或 mysql ... < diagnose_batch_divergence.sql

-- 1) 列出所有 materials.quantity != sum(batches.quantity) 的物料
SELECT
    m.id              AS material_id,
    m.tenant_id,
    m.warehouse_id,
    m.sku,
    m.name,
    m.quantity        AS aggregate_qty,
    COALESCE(SUM(b.quantity), 0) AS batches_sum,
    m.quantity - COALESCE(SUM(b.quantity), 0) AS divergence
FROM materials m
LEFT JOIN batches b
    ON b.material_id = m.id
   AND b.is_exhausted = 0
GROUP BY m.id, m.tenant_id, m.warehouse_id, m.sku, m.name, m.quantity
HAVING m.quantity <> COALESCE(SUM(b.quantity), 0)
ORDER BY ABS(m.quantity - COALESCE(SUM(b.quantity), 0)) DESC;

-- 2) 找出 type='out' 但没有任何 batch_consumptions 的孤儿出库记录
SELECT
    r.id              AS record_id,
    r.material_id,
    m.sku,
    m.name,
    r.quantity        AS out_qty,
    r.created_at,
    r.warehouse_id,
    r.tenant_id,
    r.reason_category
FROM inventory_records r
JOIN materials m ON m.id = r.material_id
LEFT JOIN batch_consumptions bc ON bc.record_id = r.id
WHERE r.type = 'out'
  AND bc.id IS NULL
ORDER BY r.created_at DESC;
