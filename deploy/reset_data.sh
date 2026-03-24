#!/bin/bash
# Smart WMS Data Reset Script
# 每日 0:00 运行，还原数据库到初始状态

set -e

# 配置
DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="${DEPLOY_DIR}/../data"
BACKUP_DB="${DATA_DIR}/warehouse_backup.db"
CURRENT_DB="${DATA_DIR}/warehouse.db"
LOG_FILE="${DEPLOY_DIR}/../logs/reset.log"
COMPOSE_FILE="${DEPLOY_DIR}/docker-compose.server.yml"

# 确保日志目录存在
mkdir -p "$(dirname "$LOG_FILE")"

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

# 停止容器（确保数据库不被锁定）
log "Stopping warehouse container..."
docker-compose -f "$COMPOSE_FILE" stop warehouse || true

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
log "Restored database from backup"

# 重启容器
log "Starting warehouse container..."
docker-compose -f "$COMPOSE_FILE" start warehouse

# 等待容器健康检查通过
log "Waiting for health check..."
for i in $(seq 1 12); do
    STATUS=$(docker inspect --format='{{.State.Health.Status}}' smart-wms 2>/dev/null || echo "unknown")
    if [ "$STATUS" = "healthy" ]; then
        log "Service is healthy"
        break
    fi
    [ "$i" -eq 12 ] && log "WARNING: Health check not passed after 60s, attempting restart..." && docker-compose -f "$COMPOSE_FILE" restart warehouse
    sleep 5
done

log "Database reset completed successfully!"
