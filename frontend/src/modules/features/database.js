// ============ Database Management Module ============

import { API_BASE_URL } from '../api.js';
import { t } from '../../../i18n.js';

// ============ Export Database ============
export function exportDatabase() {
    // 直接跳转到导出 URL，浏览器会自动下载
    window.location.href = `${API_BASE_URL}/database/export`;
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
        const data = await response.json();

        if (data.success) {
            alert(data.message);
            closeImportDatabaseModal();
            // 刷新页面以重新加载所有数据
            window.location.reload();
        } else {
            errorDiv.textContent = data.detail || data.message || t('importFailed') || '导入失败';
            errorDiv.style.display = 'block';
        }
    } catch (error) {
        console.error('Import failed:', error);
        errorDiv.textContent = t('importFailed') || '导入失败，请检查文件格式';
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
        const response = await fetch(`${API_BASE_URL}/database/clear`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ confirm: true })
        });
        const data = await response.json();

        if (data.success) {
            alert(data.message);
            closeClearDatabaseModal();
            // 刷新页面以重新加载所有数据
            window.location.reload();
        } else {
            errorDiv.textContent = data.detail || data.message || t('operationFailed') || '操作失败';
            errorDiv.style.display = 'block';
        }
    } catch (error) {
        console.error('Clear failed:', error);
        errorDiv.textContent = t('operationFailed') || '操作失败，请重试';
        errorDiv.style.display = 'block';
    }
}
