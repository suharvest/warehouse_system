# 部署指南

仓库管理系统的两种部署姿势——团队共享部署 / 个人本地开发。

镜像已推到：`sensecraft-missionpack.seeed.cn/solution/warehouse:latest`，**multi-arch (amd64 + arm64)**，docker 会按主机架构自动拉对应版本。

> ⚠️ **安全提醒**：本文档中所有 `<...>` 占位符都需要你从内部渠道获取真实值后填入 `.env` 文件，**绝对不要**把真值写进任何会提交到 git 的文件里（`.env` 和 `docker-compose.prod.yml` 都已在 `.gitignore` 中，可以放心写本地）。

---

## 团队部署（生产环境，pull 预构建镜像）

适用：把仓库系统部署到团队共用的服务器 / 测试机 / Jetson / 树莓派。

### 前置

- 服务器装好 `docker` + `docker compose`
- 能访问 `sensecraft-missionpack.seeed.cn`（公司内网或外网均可，已是公开 pullable）
- 若用 MySQL 后端：MySQL server 已就位且能从部署机访问
- 至少 512 MB 可用内存，1 个 CPU 核

### 步骤

```bash
# 1) 拉代码（如果只部署，可以只拷贝 docker-compose.prod.yml.example）
git clone https://github.com/suharvest/warehouse_system.git
cd warehouse_system

# 2) 复制 compose 模板（真实 compose 已 gitignore）
cp docker-compose.prod.yml.example docker-compose.prod.yml

# 3) 按需调整 docker-compose.prod.yml 里的不敏感配置
#    - ports（默认 "1024:2125"，容器内 2125 不能改）
#    - deploy.resources（按宿主能力）
#    - pull_policy（生产稳定后可改 missing 减少拉取）

# 4) 创建 .env 文件（已 gitignore），填敏感信息
cat > .env <<'EOF'
# ── 必填 / 推荐填 ──
FACTORY_API_KEY=<sensecraft factory 后台拷贝>
DEPLOY_MODE=multi_tenant            # 或 single_tenant
CORS_ORIGINS=https://your-domain.com  # 生产强烈建议改成具体域名而非 *

# ── 数据库（二选一）──

# 选 A：SQLite（默认，零配置）
# 不用填 DATABASE_URL，数据自动落 /data 卷（命名 volume）

# 选 B：MySQL（外部数据库）
# DATABASE_URL=mysql+pymysql://<user>:<password>@<host>:3306/<dbname>?charset=utf8mb4

# ── 可选 ──
# MAX_UPLOAD_SIZE_MB=10
# MAX_IMPORT_ROWS=10000
EOF

# 5) 拉镜像 + 起容器
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d

# 6) 验证
curl http://localhost:1024/api/dashboard/stats     # 应返回 200 + JSON
docker compose -f docker-compose.prod.yml logs -f  # 看到 alembic 迁移 + schema 校验 + uvicorn 启动
```

### 升级到新版本

```bash
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

`pull_policy: always` 会自动拉最新镜像，启动期 `alembic upgrade head` 自动跑新迁移，命名 volume 数据不丢。

### 滚动回滚

```bash
# 在 docker-compose.prod.yml 临时把 image tag 改到具体 sha 或旧版本
# image: sensecraft-missionpack.seeed.cn/solution/warehouse:<old-sha>
docker compose -f docker-compose.prod.yml up -d
```

### 数据备份（SQLite 模式）

```bash
# 在主机上把命名卷里的 db 文件拷出来
docker run --rm -v warehouse_data:/data -v $(pwd):/backup alpine \
  sh -c "cp /data/warehouse.db /backup/warehouse-$(date +%Y%m%d).db"
```

### 数据备份（MySQL 模式）

按你 MySQL 服务器的常规备份策略（mysqldump / xtrabackup / 云厂商快照），与本服务无关。

---

## 个人开发部署

适用：在自己 Mac / Linux 上跑一份用于开发、调试、试用。

### 选项 A：直接拉镜像跑（最快，跟生产一样）

```bash
cp docker-compose.prod.yml.example docker-compose.prod.yml
# 改 ports 避免和别的服务冲突，比如 8080:2125
cat > .env <<'EOF'
DEPLOY_MODE=single_tenant
EOF
docker compose -f docker-compose.prod.yml up -d
open http://localhost:8080
```

### 选项 B：本地源码 + Docker 构建（修代码时用）

```bash
# 编辑 docker-compose.prod.yml：注释 image 行，解开 build 行
#   image: sensecraft-missionpack.seeed.cn/solution/warehouse:latest
#   build: .

# 先确保前端 dist 已 build（避免容器内 npm 内存不足）
cd frontend && npm install && npm run build && cd ..

docker compose -f docker-compose.prod.yml up -d --build
```

### 选项 C：纯本地运行（最快迭代，无 Docker）

```bash
# Python 端
uv sync
uv run python run_backend.py

# 前端开发服务器（如果改前端）
cd frontend
npm install
npm run dev    # vite dev server，热重载
```

环境变量直接 export 或写到 shell 配置：

```bash
export PORT=2125
export DEPLOY_MODE=single_tenant
export FACTORY_API_KEY=<你的 token>   # 不会提交到 git
```

---

## 配置参考

### .env 完整变量表

| 变量 | 默认 | 必填 | 说明 |
|---|---|---|---|
| `FACTORY_API_KEY` | 空 | 否 | sensecraft factory 后台拷贝；不填则 `/factory/devices` 返 401，仓库主流程不受影响 |
| `DEPLOY_MODE` | `single_tenant` | 否 | `single_tenant` / `multi_tenant`；切换会触发 backend 启动期不变式校验 |
| `CORS_ORIGINS` | `*` | 否 | 逗号分隔；**生产强烈建议改具体域名** |
| `DATABASE_URL` | 空 | 否 | 非空走 MySQL/Postgres，空走 SQLite。格式：`mysql+pymysql://user:pass@host:3306/dbname?charset=utf8mb4` |
| `DATABASE_PATH` | `/data/warehouse.db` | 否 | 仅 SQLite 模式有效；`DATABASE_URL` 非空时自动忽略 |
| `MAX_UPLOAD_SIZE_MB` | 10 | 否 | 上传大小上限 |
| `MAX_IMPORT_ROWS` | 10000 | 否 | Excel 导入行数上限 |
| `PORT` | 2125 | 否 | 容器内监听端口；改了要同步改 compose 的 ports 右侧 |

### MySQL 前置（仅 MySQL 模式）

backend 启动时会 `alembic upgrade head` 自动建表，但 **不会建 database 本身**。需要先在 MySQL server 上：

```sql
CREATE DATABASE warehouse_prod CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'warehouse'@'%' IDENTIFIED BY '<强密码>';
GRANT ALL PRIVILEGES ON warehouse_prod.* TO 'warehouse'@'%';
FLUSH PRIVILEGES;
```

注意：
- `mysql+pymysql` 是固定 driver，不能写成 `mysql://`
- `?charset=utf8mb4` 必带，否则中文物料名 / 仓库名乱码
- 库的 charset/collation 必须是 utf8mb4

### 端口说明

```
docker-compose.prod.yml:
  ports:
    - "1024:2125"
       ↑    ↑
       │    └─ 容器内端口，固定 2125（backend 监听这个），别动
       └────── 宿主对外端口，按需改
```

- < 1024 端口（如 80/443）需 root 权限或前面套 nginx/caddy 反代
- 同主机多实例：改 `container_name` 和宿主端口

---

## 启动期不变式

backend 启动时会按顺序跑：

1. **Alembic 迁移**：`alembic upgrade head` 自动应用所有迁移
2. **种子数据补齐**：确保 tenant 1 + 默认仓库存在
3. **Schema 不变式校验**：对比 SQLAlchemy metadata 与实际 db schema，**缺列直接拒启动**（避免运行时 500）
4. **DEPLOY_MODE 不变式校验**：`single_tenant` 模式下若发现多 tenant 数据，拒启动并提示

任一不变式失败，容器会退出并打印诊断。**这是设计——把潜在数据问题暴露在启动期，而不是某个用户请求触发 500**。

---

## 常见问题

### 1. 启动报 "no such column: xxx"

**不该出现**。如果出现，说明项目里有人改了 `metadata.py` 加列但忘写 alembic 迁移。看错误消息提示，按它说的 `cd backend && alembic revision -m '<描述>'` 补迁移，在 `upgrade()` 里 `op.add_column(...)`，再 `alembic upgrade head`。

### 2. CORS 报错（浏览器 console: "blocked by CORS policy"）

`.env` 里加 `CORS_ORIGINS=https://你的前端域名`（多个用逗号），重启。

### 3. MCP 智能体连接不上云端

- 检查 `mcp_connections` 表中的 `mcp_endpoint`（含 token URL），token 是否过期
- 检查 `device_id` 列：同一物理设备只能注册一次（`UNIQUE` 约束）
- 看 backend 日志的 `MCP_PIPE` 相关行

### 4. 升级后端导致数据迁移失败

- `docker compose logs warehouse` 看 alembic 输出
- 严重情况：`docker compose down`（数据卷不删），从备份恢复，或 `alembic downgrade -1` 回滚一步

### 5. ARM 设备（Jetson / 树莓派）拉不到镜像

确认 `docker manifest inspect sensecraft-missionpack.seeed.cn/solution/warehouse:latest` 含 `linux/arm64`。若 manifest list 完整但仍拉不到，检查 docker 版本（需 ≥ 20.10）和网络访问 registry 的能力。

---

## 镜像构建（仅维护者）

需要 push 新镜像时（修了代码后）：

```bash
# 1) 先把 frontend dist build 出来（避免容器内 npm 内存不足）
cd frontend && npm install && npm run build && cd ..

# 2) buildx multi-arch 一把推（amd64 + arm64 manifest list 同时建好）
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -f Dockerfile.prod \
  -t sensecraft-missionpack.seeed.cn/solution/warehouse:latest \
  --push .

# 3) 验证 manifest 含两个 arch
docker buildx imagetools inspect sensecraft-missionpack.seeed.cn/solution/warehouse:latest
```

**禁止**用 `docker push` 分两次推单 arch——会覆盖 manifest 导致部分设备拉不到。

---

## 文件位置参考

| 路径 | 用途 | git 跟踪？ |
|---|---|---|
| `docker-compose.prod.yml.example` | compose 模板 | ✅ |
| `docker-compose.prod.yml` | 你的本地真实 compose（端口/资源） | ❌ gitignore |
| `.env` | 敏感配置（token / 密码） | ❌ gitignore |
| `Dockerfile.prod` | 生产镜像 Dockerfile（多阶段 + 前端 dist 预构建） | ✅ |
| `Dockerfile` | 开发镜像（容器内 npm build） | ✅ |
| `start.sh` | 本地开发启动脚本（非 Docker） | ✅ |
