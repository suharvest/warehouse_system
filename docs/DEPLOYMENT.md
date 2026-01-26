# Smart WMS 服务器部署指南

本文档说明如何将智能仓库管理系统部署到测试服务器。

## 目录

- [环境要求](#环境要求)
- [部署架构](#部署架构)
- [快速部署](#快速部署)
- [手动部署步骤](#手动部署步骤)
- [云平台配置](#云平台配置)
- [验证部署](#验证部署)
- [日常维护](#日常维护)
- [故障排查](#故障排查)

## 环境要求

### 服务器要求

| 项目 | 最低要求 | 推荐配置 |
|------|----------|----------|
| CPU | 1 核 | 2 核 |
| 内存 | 1 GB | 2 GB |
| 磁盘 | 10 GB | 20 GB |
| 系统 | Linux (CentOS/Ubuntu/Debian) | Ubuntu 20.04+ |

### 软件依赖

- Docker 20.10+
- docker-compose 1.28+
- Python 3.7+（用于初始化脚本）

检查 Docker 版本：
```bash
docker --version
docker-compose --version
```

## 部署架构

```
用户请求 → http://服务器IP:2125
                ↓
          Caddy (反向代理)
                ↓
       ┌────────┴────────┐
       ↓                 ↓
  Frontend:2125    Backend:2124
       └────────┬────────┘
                ↓
           SQLite DB
          (/data/warehouse.db)
```

### 端口说明

| 端口 | 服务 | 说明 |
|------|------|------|
| 2125 | Caddy | 统一入口（前端 + API） |
| 2124 | Caddy | API 直接访问（可选） |

## 快速部署

### 1. 上传项目到服务器

在本地执行：
```bash
rsync -avz --exclude='.venv' --exclude='node_modules' --exclude='.git' --exclude='__pycache__' \
  /path/to/warehouse_system/ root@YOUR_SERVER_IP:/opt/smart_wms/
```

### 2. 在服务器上执行部署脚本

```bash
ssh root@YOUR_SERVER_IP
cd /opt/smart_wms
chmod +x deploy/*.sh deploy/*.py
./deploy/deploy.sh
```

部署脚本会自动完成：
- 创建必要目录
- 初始化演示数据库
- 构建并启动 Docker 容器
- 配置每日数据还原定时任务

## 手动部署步骤

如果自动部署脚本失败，可按以下步骤手动部署：

### 1. 创建目录结构

```bash
mkdir -p /opt/smart_wms/data
mkdir -p /opt/smart_wms/logs
mkdir -p /opt/smart_wms/caddy_data
mkdir -p /opt/smart_wms/caddy_config
```

### 2. 上传项目文件

```bash
# 在本地执行
rsync -avz --exclude='.venv' --exclude='node_modules' --exclude='.git' \
  /path/to/warehouse_system/ root@YOUR_SERVER_IP:/opt/smart_wms/
```

### 3. 初始化数据库

```bash
# 在服务器上执行
cd /opt/smart_wms
DATABASE_PATH=/opt/smart_wms/data/warehouse.db python3 deploy/init_demo_db.py
```

这会创建：
- 用户：seeed（密码：seeed，角色：admin）
- 36 种模拟物料
- 7 天的出入库记录

### 4. 创建数据库备份

```bash
cp /opt/smart_wms/data/warehouse.db /opt/smart_wms/data/warehouse_backup.db
```

### 5. 设置文件权限

```bash
chmod 777 /opt/smart_wms/data
chmod 666 /opt/smart_wms/data/*.db
chmod +x /opt/smart_wms/deploy/*.sh
```

### 6. 启动 Docker 容器

```bash
cd /opt/smart_wms
docker-compose -f deploy/docker-compose.server.yml up -d --build
```

### 7. 配置定时任务

```bash
# 添加每日 00:00 数据还原任务
(crontab -l 2>/dev/null; echo '0 0 * * * /opt/smart_wms/deploy/reset_data.sh >> /opt/smart_wms/logs/cron.log 2>&1') | crontab -

# 验证
crontab -l
```

## 云平台配置

### 安全组/防火墙规则

需要在云平台控制台开放以下端口：

| 类型 | 协议 | 端口 | 来源 | 说明 |
|------|------|------|------|------|
| 入站 | TCP | 2125 | 0.0.0.0/0 | Web 访问 |
| 入站 | TCP | 2124 | 0.0.0.0/0 | API 访问（可选） |

#### 腾讯云配置步骤

1. 登录 [腾讯云控制台](https://console.cloud.tencent.com/)
2. 进入 **云服务器 CVM** → 选择实例
3. 点击 **安全组** 选项卡
4. 编辑入站规则，添加端口 `2125,2124`

#### 阿里云配置步骤

1. 登录 [阿里云控制台](https://ecs.console.aliyun.com/)
2. 进入 **云服务器 ECS** → 选择实例
3. 点击 **安全组** → **配置规则**
4. 添加入站规则，端口 `2125/2125` 和 `2124/2124`

### 域名配置（可选）

如需绑定域名，添加 DNS A 记录：

| 类型 | 主机记录 | 记录值 |
|------|----------|--------|
| A | your_subdomain | 服务器IP |

然后通过 Nginx/NPS 等反向代理将域名指向 `127.0.0.1:2125`。

## 验证部署

### 1. 检查容器状态

```bash
cd /opt/smart_wms
docker-compose -f deploy/docker-compose.server.yml ps
```

预期输出：
```
Name                    State         Ports
smart-wms-backend       Up (healthy)  2124/tcp
smart-wms-frontend      Up (healthy)  2125/tcp
smart-wms-caddy         Up            0.0.0.0:2124-2125->2124-2125/tcp
```

### 2. 测试 API

```bash
curl http://localhost:2125/api/dashboard/stats
```

预期返回 JSON 数据。

### 3. 测试登录

```bash
curl -X POST http://localhost:2125/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"seeed","password":"seeed"}'
```

预期返回：
```json
{"success":true,"message":"登录成功","user":{...}}
```

### 4. 浏览器访问

打开 `http://服务器IP:2125`，使用以下账号登录：
- 用户名：`seeed`
- 密码：`seeed`

## 日常维护

### 查看日志

```bash
# 后端日志
docker logs smart-wms-backend --tail 100

# Caddy 日志
docker logs smart-wms-caddy --tail 100

# 数据还原日志
cat /opt/smart_wms/logs/reset.log
```

### 重启服务

```bash
cd /opt/smart_wms
docker-compose -f deploy/docker-compose.server.yml restart
```

### 停止服务

```bash
cd /opt/smart_wms
docker-compose -f deploy/docker-compose.server.yml down
```

### 更新部署

```bash
# 1. 上传新代码
rsync -avz --exclude='.venv' --exclude='node_modules' --exclude='.git' \
  /path/to/warehouse_system/ root@YOUR_SERVER_IP:/opt/smart_wms/

# 2. 重新构建并启动
cd /opt/smart_wms
docker-compose -f deploy/docker-compose.server.yml up -d --build
```

### 手动还原数据

```bash
/opt/smart_wms/deploy/reset_data.sh
```

## 故障排查

### 问题：无法访问 2125 端口

**检查步骤：**

1. 确认服务运行正常：
   ```bash
   curl http://127.0.0.1:2125/
   ```

2. 如果本地访问正常，检查云平台安全组是否开放端口 2125

3. 检查服务器防火墙：
   ```bash
   # CentOS
   firewall-cmd --list-ports

   # Ubuntu
   ufw status
   ```

### 问题：数据库只读错误

**错误信息：**
```
sqlite3.OperationalError: attempt to write a readonly database
```

**解决方案：**
```bash
chmod 666 /opt/smart_wms/data/warehouse.db
docker-compose -f deploy/docker-compose.server.yml restart backend
```

### 问题：容器启动失败

**检查日志：**
```bash
docker logs smart-wms-backend
docker logs smart-wms-frontend
```

**常见原因：**
- 端口被占用：检查 `netstat -tlnp | grep 2125`
- 权限问题：确保 `/opt/smart_wms/data` 目录可写

### 问题：定时任务未执行

**检查 crontab：**
```bash
crontab -l | grep reset_data
```

**检查 cron 服务：**
```bash
systemctl status cron   # Ubuntu/Debian
systemctl status crond  # CentOS
```

**查看执行日志：**
```bash
cat /opt/smart_wms/logs/cron.log
```

## 部署文件清单

| 文件 | 说明 |
|------|------|
| `deploy/docker-compose.server.yml` | Docker 编排配置 |
| `deploy/Caddyfile` | Caddy 反向代理配置 |
| `deploy/init_demo_db.py` | 初始化演示数据库脚本 |
| `deploy/reset_data.sh` | 每日数据还原脚本 |
| `deploy/deploy.sh` | 一键部署脚本 |

## 演示账号

| 用户名 | 密码 | 角色 |
|--------|------|------|
| seeed | seeed | admin |

---

**注意：** 本部署方案适用于演示/测试环境。生产环境部署请额外考虑：
- 使用 HTTPS（配置 SSL 证书）
- 定期备份数据库
- 配置监控告警
- 限制 API 访问来源
