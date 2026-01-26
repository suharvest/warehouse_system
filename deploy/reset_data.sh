#!/bin/bash
# Smart WMS Data Reset Script
# 每日 0:00 运行，还原数据库到初始状态

set -e

# 配置
DATA_DIR="/opt/smart_wms/data"
BACKUP_DB="${DATA_DIR}/warehouse_backup.db"
CURRENT_DB="${DATA_DIR}/warehouse.db"
LOG_FILE="/opt/smart_wms/logs/reset.log"

# 确保日志目录存在
mkdir -p /opt/smart_wms/logs

# 记录日志
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

log "Starting database reset..."

# 检查备份是否存在
if [ ! -f "$BACKUP_DB" ]; then
    log "ERROR: Backup database not found: $BACKUP_DB"
    exit 1
fi

# 停止后端容器（确保数据库不被锁定）
log "Stopping backend container..."
cd /opt/smart_wms
docker-compose -f deploy/docker-compose.server.yml stop backend || true

# 等待容器完全停止
sleep 3

# 删除当前数据库
if [ -f "$CURRENT_DB" ]; then
    rm -f "$CURRENT_DB"
    rm -f "${CURRENT_DB}-shm"
    rm -f "${CURRENT_DB}-wal"
    log "Removed current database"
fi

# 复制备份到当前
cp "$BACKUP_DB" "$CURRENT_DB"
chmod 666 "$CURRENT_DB"
log "Restored database from backup (permissions set to 666)"

# 重启后端容器
log "Restarting backend container..."
docker-compose -f deploy/docker-compose.server.yml start backend

# 等待健康检查
sleep 10

# 验证服务
if curl -s http://localhost:2124/api/dashboard/stats > /dev/null 2>&1; then
    log "Backend service is healthy"
else
    log "WARNING: Backend health check failed, attempting full restart..."
    docker-compose -f deploy/docker-compose.server.yml restart backend
fi

log "Database reset completed successfully!"
