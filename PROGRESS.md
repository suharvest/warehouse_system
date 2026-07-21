# Development Progress Log

Cross-agent experience log for the warehouse_system repo.
Each entry: short title + date + context + takeaway.

---

## 2026-07-20 — 智能体配置页的偶发 401 是失效会话处理漏网

线上复现到：页面右上角仍保留全局管理员信息，但 `/api/mcp/connections` 每 10 秒持续返回 401，表格显示“加载数据失败…HTTP 401”。无 Cookie 直连线上接口确认 401 body 是应用返回的 `{"error":"请先登录"}`，不是 CDN/反向代理故障。会话失效的常规触发包括登录 session/Cookie 固定 24 小时，以及任一同账号客户端调用 logout 时当前实现会删除该用户的全部 sessions；失效后，已打开页面内存里的 `currentUser` 不会自行变化。仅凭截图和当前日志无法区分这两种触发。

通用 `frontend/src/modules/api.js::fetchJson` 会把非 auth API 的 401 转给 `handleSessionExpired`，清理旧用户状态、停止受限页展示并弹出重新登录；但 `frontend/src/modules/features/mcp.js` 从最初实现起一直保留独立 `mcpFetch()`，完全绕过这套全局处理。结果 MCP 自动刷新碰到失效 session 时，只把 401 当普通加载失败写入表格，同时页头仍显示旧管理员。整页刷新会重新跑 `/api/auth/status`，所以能重新同步登录状态，造成“刷新后好了”的表象。另一个次要契约问题是 `mcpFetch` 只读取错误 JSON 的 `detail`，而全局 HTTPException handler 实际返回 `error`，因此页面丢掉了“请先登录”信息，只显示重复的 `HTTP 401: HTTP 401`。

修复已让 MCP 全部请求复用统一 `fetchJson`，并让统一层同时识别后端的 `detail` / `error` 错误结构；MCP 各操作捕获到 401 后不再渲染普通错误。新增 E2E 覆盖已登录页面的 MCP 请求返回 401：旧登录态被清空、页面切回看板、重新登录弹窗出现，且等待超过一个 10 秒刷新周期后请求数仍不增加。另可把 24 小时硬编码提为配置或采用滑动续期，但那是会话策略优化，不是本次 UI 假登录的直接原因。

## 2026-07-06 — MCP 人脸校验必须继承 API Key 仓库作用域

排查 session 级 MCP 人脸链路时发现：`/api/face/verify-mcp` 原本只把 `payload.warehouse_id` 传给 `verify_mcp_face`。但 warehouse MCP wrapper 默认不传 `warehouse_id`，库存写接口是在后续 provider 调用里才通过 API key 推导仓库；因此人脸 gate 先执行时会使用 `warehouse_id=None`，只命中租户默认规则，可能跳过仓库级 `require_face` / `allowed_subject_ids`。

修复方式：在 `face_verify_mcp` 路由层调用 `resolve_warehouse_id(current_user, payload.warehouse_id)`，让 API key 绑定仓库也成为人脸规则上下文。新增 `tests/test_face_routes.py::TestFaceVerifyMcpWarehouseScope` 覆盖“API key 绑定仓库 + payload 不带 warehouse_id 时仍命中仓库级规则”。

## 2026-07-04 — 根目录临时产物整理

根目录散落了大量未跟踪的 Playwright 验证截图、页面快照 `*.yml`、`frontend/` 下的临时截图/脚本、`.playwright-cli` 缓存、`exec` 字节码文件和 `warehouse.db.bak.*` 备份。为避免误删，把这些高置信度临时产物移入 `.local-artifacts/root-cleanup-20260704/` 归档，而不是直接删除。

新增 `.gitignore` 规则忽略 `.playwright-cli/`、`.local-artifacts/`、根目录/`frontend/` 的临时截图快照、`warehouse.db.bak.*` 和 `/exec`。保留 `AGENTS.md`、`.agents/`、`.claude/`、`.superpowers/`、本地配置和当前数据库，因为它们可能是协作环境或运行态需要的上下文。

注意：本地 `progress-write` 命令不可用；并且当前 worktree 报告的分支是 `feat/face-verify-mode`，和 `AGENTS.md` 中写的 `wt/task-55--api-fuzzy-match-search-xiaozhi` 不一致，因此未执行任何分支操作。

## 2026-04-20 — 出库指定仓库/库位/批次 功能 & 前端部署踩坑

### 功能

新增出库时可指定 `warehouse_id` / `location` / `variant` / `batch_no`。未指定 → 现有 FIFO 跨批次拆分；指定 `batch_no` → 只从该批次扣减，不足报错，不 fallback。MCP 语音场景对 `location` 做作用域内模糊匹配（`resolve_location_in_scope`），REST 前端保持精确。前端出库弹窗用批次下拉联动（选产品后列出该产品在当前仓库的未耗尽批次），下拉里带 location/variant 元数据随 batch_no 一起提交以便后端做一致性校验。

分支：`feat/stock-out-batch-selection`。12 commits。Spec `docs/superpowers/specs/2026-04-20-...md`，Plan `docs/superpowers/plans/2026-04-20-...md`。

### 前端部署结构（重要，踩坑）

**后端 serve 的是 `frontend/dist/`（Vite build 产物），不是 `frontend/src/`**。

- 改完 `frontend/src/` 后必须 `cd frontend && npx vite build` 才能在 http://localhost:2124 看到变化
- 单独 `python -m http.server 8080` serve 裸 `frontend/` **不可行**——`design-system/components.css` 用 Tailwind `@apply`，必须经过 Vite 处理
- Playwright 测试要么打 backend 端口（2124，serve dist），要么跑 `npm run dev` 的 Vite dev server
- `frontend/dist/` 在 `.gitignore` 里，部署时靠构建流程重新生成

### Playwright 测试几个容易踩的坑

1. **登录弹窗不是自动弹出**——需要先点 `#login-btn`，单纯 `goto('/')` 看不到登录框
2. **单选框（入库/出库 radio）视觉上被美化 label 盖住**，Playwright 的 `click()` / `check()` 都会 reject。解决：用 `page.evaluate` 设 `.checked = true` 后 dispatch `change` 事件
3. **`showAddRecordModal` 有仓库守卫**（`records.js:261-264`）——没选仓库会 alert + return 而非弹窗。测试要注册 `page.on('dialog')` 捕获并显式选仓库
4. 测试里别 kill 端口：可能杀掉用户手动起的开发后端；先用 `lsof` 看 PID 再决定

### 已修复的登录 UX bug

初始 `loadWarehouses()` 比登录早触发，`GET /api/auth/warehouses` 返回空；`handleLogin` 成功后没重拉，导致登录后 `allWarehouses` 仍是空，仓库切换器不显示、`currentWarehouse` 守卫会卡住写操作。

修复：`setAuthCallbacks` 增加 `onLoginSuccess` 回调，`main.js` 传入 `loadWarehouses`，`handleLogin` 在 `setCurrentUser` 之后 `await onLoginSuccessFn()` 再关闭弹窗。commit `dbc4abc`。

### 模糊匹配两层串行（别搞成并行）

1. **Stage 1 产品名模糊**（全局索引，已有）→ 解析出 material_id；`fuzzy_match.py:69-86` 的 `"{name} {variant}"` 组合索引会把 variant 一起带回，走 `best_match['extra']['variant']`
2. **Stage 2 库位模糊**（仅 MCP，按产品+仓库作用域的 DISTINCT location 小集合）→ `resolve_location_in_scope`，复用 `_calc_score` + 拼音

串行的理由：库位候选集依赖产品，不先定产品就没法圈候选；错误提示也分层更清楚（ambiguous_name vs location_ambiguous vs location_not_found）。**不要**建全局 location 索引——跨产品合并会放大误差。

两层置信度判定抽到 `FuzzyMatcher._judge_confident(candidates)` 共享（commit `912c020`）。未来再加其它作用域模糊（如 contact、operator）可直接复用。

### batch_no 冲突校验（方案 A）

指定 batch_no 后若同时传 location/variant，和批次实际值不符 → 报 `batch_field_mismatch`，不静默忽略。余量不足 → `batch_insufficient_stock`，不 fallback FIFO 补齐。原因：用户说"出 B001 这批"就应该严格按 B001，静默补别的批次可能出错货。

**注意**：spec 原文 `if effective_location and batch['location'] and effective_location != batch['location']` 会让 batch.location 为 NULL 时冲突判定失效。实际实现收紧成 `if effective_location and effective_location != (batch['location'] or '')`——用户指定了 location 但批次没录 location 也算冲突，更安全。
