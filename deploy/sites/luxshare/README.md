# 立讯仓库现场 · 定制 ERP Provider

`parts_wms.py` 是立讯现场（租户 1）实际部署的 `PartsWms` Provider，对接客户方备品 WMS 的 HTTP 接口。
`mcp/providers/custom/**` 在 `.gitignore` 里（按租户从 UI 上传），这里保留一份受版本控制的副本，
避免现场改动只存在于盒子上。

## 与默认 Provider 的差异
- 2026-09-16：语音误识别归一化（中文数字→阿拉伯、「杠」→`-`、同音表如 司通→四通）
- 2026-09-21：备品后端不可达熔断——传输层错误立即返回可播报的 `say`（"备品系统暂时连不上…"），
  20 秒内不再重试，不再做 partType/partName 的逐级退化（原来 3×5s=15s，设备约 10s 无音频即断线）

## 部署（两种方式，效果相同）
1. 系统 UI「ERP Provider」上传本文件并激活 → 后端自动重启该租户的 MCP 连接（`backend/routers/erp.py:_restart_tenant_mcp_connections`）
2. 直接覆盖现场 bind mount 文件 `~/mcp_warehouse/warehouse_deploy/providers_custom/1/parts_wms.py`，
   然后 `docker restart mcp_warehouse`（Provider 是懒加载单例，不重启不生效）

改动前请在现场先备份：`cp parts_wms.py parts_wms.py.bak-<date>`。
