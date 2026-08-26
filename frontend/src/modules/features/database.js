// ============ Database Management Module ============

import { API_BASE_URL } from '../api.js';
import { t } from '../../../i18n.js';
import { getDbFileOps } from '../state.js';

// MySQL 部署下整库导出/导入/整库清空恒返回 400（它们直接操作 .db 文件），
// 入口留着只会让人点了才知道不行。清空库存数据不受影响——它走
// /api/inventory/reset，方言无关。
export function applyDbFileOpsVisibility() {
    if (getDbFileOps()) return;
    ['db-export-card', 'db-import-card', 'export-then-clear-btn'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.style.display = 'none';
    });
}

// ============ Export Database ============
export function exportDatabase() {
    // 直接跳转到导出 URL，浏览器会自动下载
    window.location.href = `${API_BASE_URL}/database/export`;
}

// 后端的全局异常处理器（backend/app.py 的 http_exception_handler）把所有 HTTPException
// 改写成 {"error": ...}，不是 FastAPI 默认的 {"detail": ...}。只读 detail 会让每一种失败
// 都退化成通配文案——现场表现是客户点"清空"只看到"操作失败，请重试"，而真实原因
// （MySQL 部署不支持该接口 / 库只读 / 老库缺表）一个都看不到。带上 HTTP status 是为了
// 让现场无需翻服务端日志就能区分 4xx（用错了）和 5xx（服务端炸了）。
function extractApiError(data, response, fallback) {
    let msg = '';
    if (data) {
        if (typeof data.error === 'string') msg = data.error;
        else if (typeof data.detail === 'string') msg = data.detail;
        else if (Array.isArray(data.detail)) msg = data.detail.map(i => i.msg || JSON.stringify(i)).join('\n');
        else if (typeof data.message === 'string') msg = data.message;
    }
    if (!msg) msg = fallback;
    return (response && !response.ok) ? `${msg}（HTTP ${response.status}）` : msg;
}

// ============ Import Database Modal ============
export function showImportDatabaseModal() {
    const modal = document.getElementById('import-database-modal');
    modal.classList.add('show');

    // 重置表单状态
    document.getElementById('database-file').value = '';
    document.getElementById('database-file-name').textContent = '';
    document.getElementById('confirm-import-database-btn').disabled = true;
    document.getElementById('import-database-error').style.display = 'none';
}

export function closeImportDatabaseModal() {
    document.getElementById('import-database-modal').classList.remove('show');
}

export function handleDatabaseFileSelect(event) {
    const file = event.target.files[0];
    if (!file) return;

    document.getElementById('database-file-name').textContent = file.name;
    document.getElementById('confirm-import-database-btn').disabled = false;
}

export async function confirmImportDatabase() {
    const fileInput = document.getElementById('database-file');
    const file = fileInput.files[0];
    if (!file) return;

    const errorDiv = document.getElementById('import-database-error');
    const confirmBtn = document.getElementById('confirm-import-database-btn');

    // 禁用按钮并显示加载状态
    confirmBtn.disabled = true;
    const originalText = confirmBtn.textContent;
    confirmBtn.textContent = t('importing') || '导入中...';

    try {
        const formData = new FormData();
        formData.append('file', file);

        const response = await fetch(`${API_BASE_URL}/database/import`, {
            method: 'POST',
            body: formData,
            credentials: 'include'
        });

        if (response.status === 401 || response.status === 403) {
            errorDiv.textContent = t('noPermission') || '权限不足，请先登录或联系管理员';
            errorDiv.style.display = 'block';
            return;
        }

        // 用 catch(() => null) 兜底：未捕获异常时 Starlette 返回的是 text/plain 的
        // "Internal Server Error"，反向代理的 502/504 是 HTML，两者都会让 .json() 抛错
        // 而落进下面的 catch 分支——那里没有 response，status 就丢了。
        const data = await response.json().catch(() => null);

        if (data && data.success) {
            alert(data.message);
            closeImportDatabaseModal();
            // 刷新页面以重新加载所有数据
            window.location.reload();
        } else {
            errorDiv.textContent = extractApiError(data, response, t('importDatabaseFailed') || '导入失败');
            errorDiv.style.display = 'block';
        }
    } catch (error) {
        console.error('Import failed:', error);
        errorDiv.textContent = t('importDatabaseFailed') || '导入失败，请检查数据库文件格式';
        errorDiv.style.display = 'block';
    } finally {
        confirmBtn.disabled = false;
        confirmBtn.textContent = originalText;
    }
}

// ============ Clear Database Modal ============
export function showClearDatabaseModal() {
    const modal = document.getElementById('clear-database-modal');
    modal.classList.add('show');
    document.getElementById('clear-database-error').style.display = 'none';
}

export function closeClearDatabaseModal() {
    document.getElementById('clear-database-modal').classList.remove('show');
}

export async function exportThenClearDatabase() {
    // 先导出
    exportDatabase();

    // 等待一小段时间让下载开始，然后清空
    setTimeout(async () => {
        await executeClearDatabase();
    }, 1000);
}

export async function directClearDatabase() {
    // 再次确认
    if (!confirm(t('confirmDirectClear') || '确定要直接清空所有仓库数据吗？此操作不可撤销！')) {
        return;
    }
    await executeClearDatabase();
}

async function executeClearDatabase() {
    const errorDiv = document.getElementById('clear-database-error');

    try {
        // 打的是 /inventory/reset 而不是 /database/clear：后者是 sqlite-only（MySQL 部署
        // 直接 400），且会删掉仓库并把 API Key / MCP 连接的 warehouse_id 置 NULL，
        // 智能体的密钥失去仓库绑定后查不到任何物料。reset 只删业务数据。
        const response = await fetch(`${API_BASE_URL}/inventory/reset`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ confirm: true })
        });

        if (response.status === 401 || response.status === 403) {
            errorDiv.textContent = t('noPermission') || '权限不足，请先登录或联系管理员';
            errorDiv.style.display = 'block';
            return;
        }

        // 用 catch(() => null) 兜底：未捕获异常时 Starlette 返回的是 text/plain 的
        // "Internal Server Error"，反向代理的 502/504 是 HTML，两者都会让 .json() 抛错
        // 而落进下面的 catch 分支——那里没有 response，status 就丢了。
        const data = await response.json().catch(() => null);

        if (data && data.success) {
            alert(data.message);
            closeClearDatabaseModal();
            // 刷新页面以重新加载所有数据
            window.location.reload();
        } else {
            errorDiv.textContent = extractApiError(data, response, t('databaseOperationFailed') || '操作失败');
            errorDiv.style.display = 'block';
        }
    } catch (error) {
        console.error('Clear failed:', error);
        errorDiv.textContent = t('databaseOperationFailed') || '操作失败，请重试';
        errorDiv.style.display = 'block';
    }
}
