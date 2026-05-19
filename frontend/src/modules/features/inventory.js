// ============ 库存列表模块 ============
import { t } from '../../../i18n.js';
import { inventoryApi } from '../api.js';
import {
    inventoryCurrentPage, inventoryPageSize, inventoryTotalPages,
    setInventoryCurrentPage, setInventoryPageSize, setInventoryTotalPages
} from '../state.js';
import { getDropdownSelectedValues, resetDropdownSelection } from '../ui/dropdown.js';

// 回调函数引用
let switchTabFn = null;

// 视图模式：是否按 SKU 聚合
let groupBySku = false;

// 设置回调
export function setInventoryCallbacks(callbacks) {
    switchTabFn = callbacks.switchTab;
}

// ============ 库存列表加载 ============
export async function loadInventory() {
    const name = document.getElementById('filter-inventory-name').value.trim();
    const category = document.getElementById('filter-inventory-category').value;
    const selectedStatuses = getDropdownSelectedValues('filter-inventory-status-dropdown');

    const params = {
        page: inventoryCurrentPage,
        pageSize: inventoryPageSize,
        name: name || undefined,
        category: category || undefined,
        status: selectedStatuses.length > 0 ? selectedStatuses : undefined,
        groupBySku
    };

    try {
        const data = await inventoryApi.getList(params);
        renderInventoryTable(data.items);
        updateInventoryPagination(data);
    } catch (error) {
        console.error('加载库存列表失败:', error);
    }
}

// ============ 表格渲染 ============
function renderInventoryTable(items) {
    const tbody = document.getElementById('inventory-tbody');
    tbody.innerHTML = '';

    if (items.length === 0) {
        tbody.innerHTML = `<tr><td colspan="10" style="text-align: center; color: #999;">${t('noData')}</td></tr>`;
        return;
    }

    items.forEach(item => {
        const tr = document.createElement('tr');
        tr.className = 'clickable';

        let statusText = '', statusClass = '';
        if (item.is_disabled) {
            statusText = t('statusDisabled');
            statusClass = 'status-disabled';
        } else if (item.status === 'normal') {
            statusText = t('statusNormal');
            statusClass = 'status-normal';
        } else if (item.status === 'warning') {
            statusText = t('statusWarning');
            statusClass = 'status-warning';
        } else {
            statusText = t('statusDanger');
            statusClass = 'status-danger';
        }

        // 聚合视图下的多值展示：批次号 / 变体 / 位置
        const mixedLabel = t('inventoryMixedValues') || '多个';
        let variantCell, batchCell, locationCell;
        if (groupBySku) {
            const count = item.batch_count || 0;
            if (count === 0) batchCell = '-';
            else if (count === 1) batchCell = item.batch_no || '-';
            else batchCell = `<span class="agg-badge">${count} ${t('inventoryBatchesUnit') || '个批次'}</span>`;

            variantCell = item.variant_mixed
                ? `<span class="agg-badge agg-badge--muted">${mixedLabel}</span>`
                : (item.variant || '-');
            locationCell = item.location_mixed
                ? `<span class="agg-badge agg-badge--muted">${mixedLabel}</span>`
                : (item.location || '-');
        } else {
            variantCell = item.variant || '-';
            batchCell = item.batch_no || '-';
            locationCell = item.location || '-';
        }

        tr.innerHTML = `
            <td>${item.name}</td>
            <td>${variantCell}</td>
            <td>${item.sku}</td>
            <td>${item.category}</td>
            <td>${batchCell}</td>
            <td><strong>${item.quantity}</strong></td>
            <td>${item.unit}</td>
            <td>${item.safe_stock != null ? item.safe_stock : '-'}</td>
            <td><span class="status-badge ${statusClass}">${statusText}</span></td>
            <td>${locationCell}</td>
        `;

        tr.addEventListener('click', function () {
            if (switchTabFn) switchTabFn('detail', { product: item.name });
        });

        tbody.appendChild(tr);
    });
}

// ============ 分页 ============
function updateInventoryPagination(data) {
    setInventoryTotalPages(data.total_pages);
    document.getElementById('inventory-total').textContent = data.total;
    document.getElementById('inventory-current-page').textContent = data.page;
    document.getElementById('inventory-total-pages').textContent = data.total_pages;
    document.getElementById('inventory-prev-btn').disabled = data.page <= 1;
    document.getElementById('inventory-next-btn').disabled = data.page >= data.total_pages;
}

export function inventoryGoToPage(page) {
    if (page < 1 || page > inventoryTotalPages) return;
    setInventoryCurrentPage(page);
    loadInventory();
}

export function changeInventoryPageSize(size) {
    setInventoryPageSize(parseInt(size));
    setInventoryCurrentPage(1);
    loadInventory();
}

// 搜索框 Enter 触发筛选
const nameInput = document.getElementById('filter-inventory-name');
if (nameInput) {
    nameInput.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            applyInventoryFilter();
        }
    });
}

// ============ 筛选 ============
export function applyInventoryFilter() {
    setInventoryCurrentPage(1);
    loadInventory();
}

export function resetInventoryFilter() {
    document.getElementById('filter-inventory-name').value = '';
    document.getElementById('filter-inventory-category').value = '';
    resetDropdownSelection('filter-inventory-status-dropdown');
    setInventoryCurrentPage(1);
    loadInventory();
}

// 切换"按 SKU 聚合"视图
export function toggleInventoryGroupBySku(checked) {
    groupBySku = !!checked;
    setInventoryCurrentPage(1);
    loadInventory();
}

// 应用库存筛选器值（从 URL 参数）
export function applyInventoryFilters(filters) {
    if (filters.name) document.getElementById('filter-inventory-name').value = filters.name;
    if (filters.category) document.getElementById('filter-inventory-category').value = filters.category;
    if (filters.status) {
        resetDropdownSelection('filter-inventory-status-dropdown', filters.status.split(','));
    }
}
