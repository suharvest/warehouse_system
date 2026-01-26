#!/bin/bash
# Smart WMS Server Deployment Script
# 在服务器上运行此脚本进行一键部署

set -e

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 配置
DEPLOY_DIR="/opt/smart_wms"
DATA_DIR="${DEPLOY_DIR}/data"
LOGS_DIR="${DEPLOY_DIR}/logs"

echo "======================================"
echo "Smart WMS Server Deployment"
echo "======================================"

# 检查是否在正确的目录
if [ ! -f "${DEPLOY_DIR}/deploy/docker-compose.server.yml" ]; then
    log_error "Deploy files not found. Please run from ${DEPLOY_DIR}"
    exit 1
fi

# 1. 创建必要目录
log_info "Creating directories..."
mkdir -p "$DATA_DIR"
mkdir -p "$LOGS_DIR"
mkdir -p "${DEPLOY_DIR}/caddy_data"
mkdir -p "${DEPLOY_DIR}/caddy_config"

# 2. 设置脚本执行权限
log_info "Setting permissions..."
chmod +x "${DEPLOY_DIR}/deploy/reset_data.sh"
chmod +x "${DEPLOY_DIR}/deploy/init_demo_db.py"

# 3. 停止旧容器（如果存在）
log_info "Stopping existing containers..."
cd "$DEPLOY_DIR"
docker-compose -f deploy/docker-compose.server.yml down 2>/dev/null || true

# 4. 初始化数据库
log_info "Initializing demo database..."
if command -v python3 &> /dev/null; then
    # 安装 bcrypt 如果可用
    pip3 install bcrypt 2>/dev/null || log_warn "bcrypt not installed, using SHA256"
    python3 "${DEPLOY_DIR}/deploy/init_demo_db.py"
else
    log_warn "Python3 not found on host, will initialize in container..."
fi

# 5. 创建数据库备份（用于每日还原）
if [ -f "${DATA_DIR}/warehouse.db" ]; then
    cp "${DATA_DIR}/warehouse.db" "${DATA_DIR}/warehouse_backup.db"
    log_info "Created database backup for daily reset"
fi

# 6. 构建并启动容器
log_info "Building and starting containers..."
docker-compose -f deploy/docker-compose.server.yml up -d --build

# 7. 等待服务启动
log_info "Waiting for services to start..."
sleep 15

# 8. 健康检查
log_info "Running health checks..."

# 检查后端
BACKEND_HEALTH=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:2124/api/dashboard/stats 2>/dev/null || echo "000")
if [ "$BACKEND_HEALTH" = "200" ]; then
    log_info "Backend: OK"
else
    log_warn "Backend: HTTP $BACKEND_HEALTH (may still be starting...)"
fi

# 检查前端
FRONTEND_HEALTH=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:2125/ 2>/dev/null || echo "000")
if [ "$FRONTEND_HEALTH" = "200" ]; then
    log_info "Frontend: OK"
else
    log_warn "Frontend: HTTP $FRONTEND_HEALTH (may still be starting...)"
fi

# 9. 设置定时任务
log_info "Setting up cron job for daily data reset..."
CRON_JOB="0 0 * * * ${DEPLOY_DIR}/deploy/reset_data.sh >> ${LOGS_DIR}/cron.log 2>&1"

# 检查是否已存在
if crontab -l 2>/dev/null | grep -q "reset_data.sh"; then
    log_info "Cron job already exists"
else
    (crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -
    log_info "Cron job added: Daily reset at 00:00"
fi

# 10. 显示状态
echo ""
echo "======================================"
echo "Deployment Complete!"
echo "======================================"
echo ""
log_info "Containers:"
docker-compose -f deploy/docker-compose.server.yml ps

echo ""
echo "======================================"
echo "Access Information"
echo "======================================"
echo ""
echo "HTTPS (after DNS configured):"
echo "  https://smart_wms.harvestlife.xyz"
echo ""
echo "Direct IP access (for testing):"
echo "  http://$(curl -s ifconfig.me 2>/dev/null || echo 'YOUR_SERVER_IP'):2125"
echo ""
echo "Login credentials:"
echo "  Username: seeed"
echo "  Password: seeed"
echo ""
echo "======================================"
echo "DNS Configuration Required"
echo "======================================"
echo ""
echo "Add this A record to your DNS:"
echo "  Host: smart_wms"
echo "  Value: $(curl -s ifconfig.me 2>/dev/null || echo 'YOUR_SERVER_IP')"
echo "  Domain: harvestlife.xyz"
echo ""
log_info "Daily data reset is scheduled at 00:00"
echo ""
