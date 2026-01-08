// ============ 进出库记录模块 ============
import { t } from '../../../i18n.js';
import { recordsApi, contactsApi, operatorsApi } from '../api.js';
import {
    recordsCurrentPage, recordsPageSize, recordsTotalPages,
    setRecordsCurrentPage, setRecordsPageSize, setRecordsTotalPages,
    currentProductName, currentTab, allProducts
} from '../state.js';
import { getDropdownSelectedValues, resetDropdownSelection, clearRecordProductSelector } from '../ui/dropdown.js';

// 回调函数引用
let loadDashboardDataFn = null;
let loadInventoryFn = null;
let loadProductDetailFn = null;
let loadAllProductsFn = null;

// 设置回调
export function setRecordsCallbacks(callbacks) {
    loadDashboardDataFn = callbacks.loadDashboardData;
    loadInventoryFn = callbacks.loadInventory;
    loadProductDetailFn = callbacks.loadProductDetail;
    loadAllProductsFn = callbacks.loadAllProducts;
}

// 新增记录状态
let addRecordForProduct = false;

// ============ 记录列表加载 ============
export async function loadRecords() {
    const startDate = document.getElementById('filter-start-date').value;
    const endDate = document.getElementById('filter-end-date').value;
    const productName = document.getElementById('filter-records-product').value.trim();
    const category = document.getElementById('filter-records-category').value;
    const recordType = document.getElementById('filter-record-type').value;
    const selectedStatuses = getDropdownSelectedValues('filter-record-status-dropdown');
    const contactId = document.getElementById('filter-record-contact').value;
    const operatorUserId = document.getElementById('filter-record-operator').value;
    const reason = document.getElementById('filter-record-reason').value.trim();

    const params = {
        page: recordsCurrentPage,
        pageSize: recordsPageSize,
        startDate: startDate || undefined,
        endDate: endDate || undefined,
        productName: productName || undefined,
        category: category || undefined,
        recordType: recordType || undefined,
        contactId: contactId || undefined,
        operatorUserId: operatorUserId || undefined,
        reason: reason || undefined,
        status: selectedStatuses.length > 0 ? selectedStatuses : undefined
    };

    try {
        const data = await recordsApi.getList(params);
        renderRecordsTable(data.items);
        updateRecordsPagination(data);
    } catch (error) {
        console.error('加载进出库记录失败:', error);
    }
}

// ============ 表格渲染 ============
function renderRecordsTable(items) {
    const tbody = document.getElementById('records-tbody');
    tbody.innerHTML = '';

    if (items.length === 0) {
        tbody.innerHTML = `<tr><td colspan="11" style="text-align: center; color: #999;">${t('noRecords')}</td></tr>`;
        return;
    }

    items.forEach(item => {
        const tr = document.createElement('tr');

        const typeText = item.type === 'in' ? t('inbound') : t('outbound');
        const typeClass = item.type === 'in' ? 'type-in' : 'type-out';

        let statusText = '', statusClass = '';
        if (item.is_disabled) {
            statusText = t('statusDisabled');
            statusClass = 'status-disabled';
        } else if (item.material_status === 'normal') {
            statusText = t('statusNormal');
            statusClass = 'status-normal';
        } else if (item.material_status === 'warning') {
            statusText = t('statusWarning');
            statusClass = 'status-warning';
        } else {
            statusText = t('statusDanger');
            statusClass = 'status-danger';
        }

        // 批次信息
        let batchDisplay = '-';
        if (item.type === 'in' && item.batch_no) {
            batchDisplay = item.batch_no;
        } else if (item.type === 'out' && item.batch_details) {
            batchDisplay = `<span class="batch-details" title="${item.batch_details}">${item.batch_details}</span>`;
        }

        tr.innerHTML = `
            <td>${item.created_at}</td>
            <td>${item.material_name}</td>
            <td>${item.material_sku}</td>
            <td>${item.category || '-'}</td>
            <td><span class="type-badge ${typeClass}">${typeText}</span></td>
            <td><strong>${item.quantity}</strong></td>
            <td>${batchDisplay}</td>
            <td>${item.contact_name || '-'}</td>
            <td>${item.operator_name || item.operator}</td>
            <td>${item.reason || '-'}</td>
            <td><span class="status-badge ${statusClass}">${statusText}</span></td>
        `;

        tbody.appendChild(tr);
    });
}

// ============ 分页 ============
function updateRecordsPagination(data) {
    setRecordsTotalPages(data.total_pages);
    document.getElementById('records-total').textContent = data.total;
    document.getElementById('records-current-page').textContent = data.page;
    document.getElementById('records-total-pages').textContent = data.total_pages;
    document.getElementById('records-prev-btn').disabled = data.page <= 1;
    document.getElementById('records-next-btn').disabled = data.page >= data.total_pages;
}

export function recordsGoToPage(page) {
    if (page < 1 || page > recordsTotalPages) return;
    setRecordsCurrentPage(page);
    loadRecords();
}

export function changeRecordsPageSize(size) {
    setRecordsPageSize(parseInt(size));
    setRecordsCurrentPage(1);
    loadRecords();
}

// ============ 筛选 ============
export function applyRecordsFilter() {
    setRecordsCurrentPage(1);
    loadRecords();
}

export function resetRecordsFilter() {
    document.getElementById('filter-start-date').value = '';
    document.getElementById('filter-end-date').value = '';
    document.getElementById('filter-records-product').value = '';
    document.getElementById('filter-records-category').value = '';
    document.getElementById('filter-record-type').value = '';
    document.getElementById('filter-record-contact').value = '';
    document.getElementById('filter-record-operator').value = '';
    document.getElementById('filter-record-reason').value = '';
    resetDropdownSelection('filter-record-status-dropdown');
    setRecordsCurrentPage(1);
    loadRecords();
}

// 应用记录筛选器值（从 URL 参数）
export function applyRecordsFilters(filters) {
    if (filters.start_date) document.getElementById('filter-start-date').value = filters.start_date;
    if (filters.end_date) document.getElementById('filter-end-date').value = filters.end_date;
    if (filters.type) document.getElementById('filter-record-type').value = filters.type;
    if (filters.product_name) document.getElementById('filter-records-product').value = filters.product_name;
}

// ============ 加载筛选选项 ============
export async function loadRecordsFilterOptions() {
    try {
        const [contactsData, operators] = await Promise.all([
            contactsApi.getAll(null, false),
            operatorsApi.getList()
        ]);

        // 联系方选项
        const contactSelect = document.getElementById('filter-record-contact');
        contactSelect.innerHTML = `<option value="" data-i18n="allContacts">${t('allContacts')}</option>`;
        if (contactsData.items) {
            contactsData.items.forEach(contact => {
                const option = document.createElement('option');
                option.value = contact.id;
                option.textContent = contact.name;
                contactSelect.appendChild(option);
            });
        }

        // 操作员选项
        const operatorSelect = document.getElementById('filter-record-operator');
        operatorSelect.innerHTML = `<option value="" data-i18n="allOperators">${t('allOperators')}</option>`;
        operators.forEach(op => {
            const option = document.createElement('option');
            option.value = op.user_id;
            option.textContent = op.display_name || op.username;
            operatorSelect.appendChild(option);
        });
    } catch (error) {
        console.error('加载筛选选项失败:', error);
    }
}

// ============ 新增记录 ============
export function showAddRecordModal() {
    addRecordForProduct = false;
    document.getElementById('record-product-group').style.display = 'block';
    document.getElementById('record-product-display-group').style.display = 'none';
    document.getElementById('add-record-modal').classList.add('show');
    document.getElementById('add-record-form').reset();
    clearRecordProductSelector();
    loadContactsForRecord('in');
    document.querySelectorAll('input[name="record-type"]').forEach(radio => {
        radio.onchange = () => loadContactsForRecord(radio.value);
    });
}

export function showAddRecordModalForProduct() {
    if (!currentProductName) return;
    addRecordForProduct = true;
    document.getElementById('record-product-group').style.display = 'none';
    document.getElementById('record-product-display-group').style.display = 'block';
    document.getElementById('record-product-display').value = currentProductName;
    document.getElementById('add-record-modal').classList.add('show');
    document.getElementById('add-record-form').reset();
    document.getElementById('record-product-display').value = currentProductName;
    loadContactsForRecord('in');
    document.querySelectorAll('input[name="record-type"]').forEach(radio => {
        radio.onchange = () => loadContactsForRecord(radio.value);
    });
}

async function loadContactsForRecord(recordType) {
    const select = document.getElementById('record-contact');
    if (!select) return;

    try {
        const contactType = recordType === 'in' ? 'supplier' : 'customer';
        const data = await contactsApi.getAll(contactType, true);

        select.innerHTML = `<option value="">${t('pleaseSelect')}</option>`;
        if (data.items) {
            data.items.forEach(contact => {
                select.innerHTML += `<option value="${contact.id}">${contact.name}</option>`;
            });
        }
    } catch (error) {
        console.error('加载联系方列表失败:', error);
    }
}

export function closeAddRecordModal() {
    document.getElementById('add-record-modal').classList.remove('show');
    document.getElementById('add-record-form').reset();
    clearRecordProductSelector();
}

export async function submitAddRecord() {
    const productName = addRecordForProduct
        ? currentProductName
        : document.getElementById('record-product').value;
    const type = document.querySelector('input[name="record-type"]:checked')?.value;
    const quantity = parseInt(document.getElementById('record-quantity').value);
    const reason = document.getElementById('record-reason').value.trim();
    const contactId = document.getElementById('record-contact')?.value || null;

    if (!productName || !type || !document.getElementById('record-quantity').value || !reason) {
        alert(t('fillAllFields'));
        return;
    }

    if (isNaN(quantity) || quantity <= 0) {
        alert(t('quantityMustBePositive'));
        return;
    }

    try {
        const data = await recordsApi.create({
            product_name: productName,
            type: type,
            quantity: quantity,
            reason: reason,
            contact_id: contactId ? parseInt(contactId) : null
        });

        if (data.success) {
            alert(data.message);
            closeAddRecordModal();
            if (loadAllProductsFn) loadAllProductsFn();
            if (currentTab === 'records') loadRecords();
            if (currentTab === 'inventory' && loadInventoryFn) loadInventoryFn();
            if (currentTab === 'detail' && currentProductName && loadProductDetailFn) loadProductDetailFn();
            if (currentTab === 'dashboard' && loadDashboardDataFn) loadDashboardDataFn();
        } else {
            alert(data.error || data.message || t('operationFailed'));
        }
    } catch (error) {
        // 401 错误已由全局 session 过期处理器处理，不再重复提示
        if (error.status === 401) return;
        console.error('操作失败:', error);
        alert(t('operationFailed'));
    }
}
