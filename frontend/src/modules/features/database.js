// ============ Database Management Module ============

import { API_BASE_URL } from '../api.js';
import { t } from '../../../i18n.js';
import { getDbFileOps } from '../state.js';

// MySQL 部署下整库导出/导入恒返回 400（它们直接操作 .db 文件），入口留着只会让人
// 点了才知道不行。「清空库存数据」与「先导出再清空」不受影响——前者走
// /api/inventory/reset，后者走两个 Excel 导出，都是方言无关的。
export function applyDbFileOpsVisibility() {
    if (getDbFileOps()) return;
    ['db-export-card', 'db-import-card'].forEach(id => {
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

// 从 Content-Disposition 取文件名，取不到就用调用方给的兜底名。
function filenameFromResponse(response, fallback) {
    const cd = response.headers.get('Content-Disposition') || '';
    const match = /filename\*?=(?:UTF-8''|")?([^";]+)/i.exec(cd);
    return match ? decodeURIComponent(match[1].trim()) : fallback;
}

async function downloadExport(path, fallbackName) {
    const response = await fetch(`${API_BASE_URL}${path}`, { credentials: 'include' });
    if (!response.ok) {
        const data = await response.json().catch(() => null);
        throw new Error(extractApiError(data, response, t('exportFailed') || '导出失败'));
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filenameFromResponse(response, fallbackName);
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
}

export async function exportThenClearDatabase() {
    const errorDiv = document.getElementById('clear-database-error');

    // 备份走两个 Excel 导出而不是整库 .db 导出：后者是 sqlite-only，MySQL 部署下
    // 恒 400，"先导出再清空"在线上等于没有备份就清空。这两个导出都走 SA Core，
    // 方言无关，且合起来正好覆盖 reset 会删掉的东西——库存快照（一行一批次）
    // 加出入库流水。
    try {
        // status 必须显式列全四种：该导出默认只给未禁用物料（status 里没有 'disabled'
        // 就会加 is_disabled = 0 的谓词），而 reset 不区分状态、禁用物料照删，
        // 不带这个参数的备份会缺掉它们。
        await downloadExport(
            '/materials/export-excel?status=normal,warning,danger,disabled',
            'inventory_snapshot.xlsx',
        );
        await downloadExport('/inventory/export-excel', 'inventory_records.xlsx');
    } catch (error) {
        console.error('Export before clear failed:', error);
        // 导出失败就不清空。原实现是 setTimeout 1s 后无条件清空，导出成没成功都照删。
        errorDiv.textContent = `${t('exportBeforeClearFailed') || '导出失败，已取消清空'}：${error.message}`;
        errorDiv.style.display = 'block';
        return;
    }

    await executeClearDatabase();
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
