// ============ 导入导出模块 ============
import { t } from '../../../i18n.js';
import { API_BASE_URL } from '../api.js';
import { currentProductName, currentTab } from '../state.js';

// 回调函数引用
let loadAllProductsFn = null;
let loadCategoriesFn = null;
let loadInventoryFn = null;
let loadDashboardDataFn = null;

// 设置回调
export function setImportExportCallbacks(callbacks) {
    loadAllProductsFn = callbacks.loadAllProducts;
    loadCategoriesFn = callbacks.loadCategories;
    loadInventoryFn = callbacks.loadInventory;
    loadDashboardDataFn = callbacks.loadDashboardData;
}

// 导入状态
let importPreviewData = null;
let pendingNewSkus = [];

// ============ 导出功能 ============
export function exportInventory() {
    const name = document.getElementById('filter-inventory-name').value.trim();
    const category = document.getElementById('filter-inventory-category').value;

    const statusContainer = document.getElementById('filter-inventory-status-dropdown');
    const selectedStatuses = [];
    if (statusContainer) {
        statusContainer.querySelectorAll('.dropdown-item.selected').forEach(item => {
            const value = item.getAttribute('data-value');
            if (value) selectedStatuses.push(value);
        });
    }

    const params = new URLSearchParams();
    if (name) params.set('name', name);
    if (category) params.set('category', category);
    if (selectedStatuses.length > 0) params.set('status', selectedStatuses.join(','));

    window.location.href = `${API_BASE_URL}/materials/export-excel?${params}`;
}

export function exportRecords() {
    const startDate = document.getElementById('filter-start-date').value;
    const endDate = document.getElementById('filter-end-date').value;
    const productName = document.getElementById('filter-records-product').value.trim();
    const type = document.getElementById('filter-record-type').value;

    const params = new URLSearchParams();
    if (startDate) params.set('start_date', startDate);
    if (endDate) params.set('end_date', endDate);
    if (productName) params.set('product_name', productName);
    if (type) params.set('record_type', type);

    window.location.href = `${API_BASE_URL}/inventory/export-excel?${params}`;
}

export function exportProductRecords() {
    if (!currentProductName) return;
    window.location.href = `${API_BASE_URL}/inventory/export-excel?product_name=${encodeURIComponent(currentProductName)}`;
}

// ============ 导入功能 ============
export function showImportModal() {
    document.getElementById('import-modal').classList.add('show');
    document.getElementById('preview-area').style.display = 'none';
    document.getElementById('excel-file').value = '';
    document.getElementById('confirm-import-btn').disabled = true;
    importPreviewData = null;
    pendingNewSkus = [];
    const disableCheckbox = document.getElementById('confirm-disable-missing');
    if (disableCheckbox) disableCheckbox.checked = false;
    // 隐藏缺失SKU区域
    const missingArea = document.getElementById('missing-skus-area');
    if (missingArea) missingArea.style.display = 'none';
}

export function closeImportModal() {
    document.getElementById('import-modal').classList.remove('show');
    importPreviewData = null;
    pendingNewSkus = [];
}

export async function handleFileSelect(event) {
    const file = event.target.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('file', file);

    try {
        const response = await fetch(`${API_BASE_URL}/materials/import-excel/preview`, {
            method: 'POST',
            body: formData
        });
        const data = await response.json();

        if (data.success) {
            importPreviewData = data;
            pendingNewSkus = data.new_skus || [];
            renderImportPreview(data);
        } else {
            alert(data.message || t('parseFileFailed'));
        }
    } catch (error) {
        console.error('预览失败:', error);
        alert(t('previewFailed'));
    }
}

function renderMissingSkus(missingSkus, totalMissing) {
    let container = document.getElementById('missing-skus-area');

    // 如果容器不存在，创建它
    if (!container) {
        container = document.createElement('div');
        container.id = 'missing-skus-area';
        container.className = 'missing-skus-area';
        // 插入到预览区域之前
        const previewArea = document.getElementById('preview-area');
        previewArea.parentNode.insertBefore(container, previewArea);
    }

    if (totalMissing === 0) {
        container.style.display = 'none';
        return;
    }

    container.style.display = 'block';
    container.innerHTML = `
        <div class="missing-skus-header" onclick="this.parentNode.classList.toggle('expanded')">
            <span class="missing-skus-title">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>
                    <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
                </svg>
                ${t('missingSku') || '缺失的SKU'}: <strong>${totalMissing}</strong> ${t('items') || '项'}
            </span>
            <span class="missing-skus-toggle">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="6 9 12 15 18 9"/>
                </svg>
            </span>
        </div>
        <div class="missing-skus-list">
            <table class="preview-table">
                <thead>
                    <tr>
                        <th>SKU</th>
                        <th>${t('materialName') || '物料名称'}</th>
                        <th>${t('productCategory') || '分类'}</th>
                        <th>${t('currentStockCol') || '当前库存'}</th>
                    </tr>
                </thead>
                <tbody>
                    ${missingSkus.map(item => `
                        <tr>
                            <td>${item.sku}</td>
                            <td>${item.name}</td>
                            <td>${item.category}</td>
                            <td>${item.current_quantity}</td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        </div>
    `;
}

function renderImportPreview(data) {
    document.getElementById('preview-area').style.display = 'block';
    document.getElementById('preview-in').textContent = data.total_in;
    document.getElementById('preview-out').textContent = data.total_out;
    document.getElementById('preview-new').textContent = data.total_new;

    const totalIn = parseInt(data.total_in) || 0;
    const totalOut = parseInt(data.total_out) || 0;
    const totalNew = parseInt(data.total_new) || 0;
    const totalMissing = parseInt(data.total_missing) || 0;
    const hasChanges = totalIn > 0 || totalOut > 0 || totalNew > 0;
    const hasMissing = totalMissing > 0;

    data._hasChanges = hasChanges;
    data._hasMissing = hasMissing;

    const reasonRow = document.getElementById('import-reason').closest('.form-row');
    const disableRow = document.getElementById('confirm-disable-missing').closest('.form-row');

    // 显示原因输入框（有变更时）
    if (hasChanges) {
        reasonRow.style.display = '';
    } else {
        reasonRow.style.display = 'none';
    }

    // 显示禁用选项（有缺失SKU时）
    if (hasMissing) {
        disableRow.style.display = '';
    } else {
        disableRow.style.display = 'none';
    }

    // 渲染缺失SKU列表
    renderMissingSkus(data.missing_skus || [], totalMissing);

    const tbody = document.getElementById('preview-tbody');
    tbody.innerHTML = '';

    data.preview.forEach(item => {
        const tr = document.createElement('tr');

        let opText = '', opClass = '';
        if (item.operation === 'in') {
            opText = t('inbound');
            opClass = 'type-in';
        } else if (item.operation === 'out') {
            opText = t('outbound');
            opClass = 'type-out';
        } else if (item.operation === 'new') {
            opText = t('newMaterial');
            opClass = 'type-new';
        } else {
            opText = t('noChange');
            opClass = 'type-none';
        }

        const currentQty = item.current_quantity !== null ? item.current_quantity : '-';
        const diffDisplay = item.difference > 0 ? `+${item.difference}` : item.difference;

        tr.innerHTML = `
            <td>${item.sku}</td>
            <td>${item.name}</td>
            <td>${currentQty}</td>
            <td>${item.import_quantity}</td>
            <td class="${item.difference > 0 ? 'diff-positive' : item.difference < 0 ? 'diff-negative' : ''}">${diffDisplay}</td>
            <td><span class="type-badge ${opClass}">${opText}</span></td>
        `;
        tbody.appendChild(tr);
    });

    document.getElementById('confirm-import-btn').disabled = false;
}

export async function confirmImport() {
    if (!importPreviewData) return;

    const hasChanges = importPreviewData._hasChanges;
    const hasMissing = importPreviewData._hasMissing;
    const wantDisableMissing = document.getElementById('confirm-disable-missing')?.checked || false;

    // 如果没有变更，也没有选择禁用缺失SKU，则无需导入
    if (!hasChanges && !wantDisableMissing) {
        alert(t('noChangesToImport') || '数据无变化，无需导入');
        closeImportModal();
        return;
    }

    // 如果有变更，需要填写原因
    if (hasChanges) {
        const reason = document.getElementById('import-reason').value.trim();
        if (!reason) {
            alert(t('fillAllFields'));
            return;
        }
    }

    if (pendingNewSkus.length > 0) {
        showNewSkuModal();
        return;
    }

    await executeImport(false);
}

function showNewSkuModal() {
    const list = document.getElementById('new-sku-list');
    list.innerHTML = '';

    pendingNewSkus.forEach(item => {
        const div = document.createElement('div');
        div.className = 'new-sku-item';
        div.innerHTML = `
            <span class="sku">${item.sku}</span>
            <span class="name">${item.name}</span>
            <span class="qty">${t('quantity')}: ${item.import_quantity}</span>
        `;
        list.appendChild(div);
    });

    document.getElementById('new-sku-modal').classList.add('show');
}

export function closeNewSkuModal() {
    document.getElementById('new-sku-modal').classList.remove('show');
}

export async function skipNewSkus() {
    closeNewSkuModal();
    await executeImport(false);
}

export async function confirmNewSkus() {
    closeNewSkuModal();
    await executeImport(true);
}

async function executeImport(confirmNewSkusFlag) {
    const reason = document.getElementById('import-reason').value.trim();
    const confirmDisableMissing = document.getElementById('confirm-disable-missing')?.checked || false;

    try {
        const response = await fetch(`${API_BASE_URL}/materials/import-excel/confirm`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({
                changes: importPreviewData.preview,
                reason: reason,
                confirm_new_skus: confirmNewSkusFlag,
                confirm_disable_missing_skus: confirmDisableMissing
            })
        });

        const data = await response.json();

        if (data.success) {
            alert(data.message);
            closeImportModal();
            if (loadAllProductsFn) loadAllProductsFn();
            if (loadCategoriesFn) loadCategoriesFn();
            if (currentTab === 'inventory' && loadInventoryFn) loadInventoryFn();
            if (currentTab === 'dashboard' && loadDashboardDataFn) loadDashboardDataFn();
        } else {
            alert(data.message || t('importFailed'));
        }
    } catch (error) {
        console.error('导入失败:', error);
        alert(t('importFailed'));
    }
}
