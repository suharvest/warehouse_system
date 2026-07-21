// ============ 进出库记录模块 ============
import { t } from '../../../i18n.js';
import { recordsApi, contactsApi, operatorsApi, getCurrentWarehouseId } from '../api.js';
import {
    recordsCurrentPage, recordsPageSize, recordsTotalPages,
    setRecordsCurrentPage, setRecordsPageSize, setRecordsTotalPages,
    currentProductName, currentTab, allProducts, currentWarehouse
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

// In-flight guard：防止用户双击"确认"按钮重复提交导致库存被扣减两次
let isSubmittingRecord = false;

// 原因分类缓存
let reasonCategoriesCache = null;

// i18n 标签映射（key → i18n key）
const REASON_CATEGORY_I18N = {
    purchase: 'reasonPurchase', return: 'reasonReturn', refund: 'reasonRefund',
    produce: 'reasonProduce', transfer_in: 'reasonTransferIn', other_in: 'reasonOtherIn',
    sell: 'reasonSell', lend: 'reasonLend', consume: 'reasonConsume',
    loss: 'reasonLoss', transfer_out: 'reasonTransferOut', other_out: 'reasonOtherOut',
};

function getReasonCategoryLabel(key) {
    if (!key) return '-';
    const i18nKey = REASON_CATEGORY_I18N[key];
    return i18nKey ? t(i18nKey) : key;
}

async function loadReasonCategories() {
    if (reasonCategoriesCache) return reasonCategoriesCache;
    try {
        const resp = await fetch(`${window.API_BASE_URL || ''}/api/reason-categories`);
        reasonCategoriesCache = await resp.json();
        return reasonCategoriesCache;
    } catch (e) {
        console.error('加载原因分类失败:', e);
        return { in: [], out: [] };
    }
}

function populateReasonCategorySelect(selectId, type) {
    const select = document.getElementById(selectId);
    if (!select || !reasonCategoriesCache) return;
    const items = reasonCategoriesCache[type] || [];
    select.innerHTML = items.map(c =>
        `<option value="${c.key}">${getReasonCategoryLabel(c.key)}</option>`
    ).join('');
}

function populateReasonCategoryFilterSelect() {
    const select = document.getElementById('filter-reason-category');
    if (!select || !reasonCategoriesCache) return;
    const allItems = [...(reasonCategoriesCache.in || []), ...(reasonCategoriesCache.out || [])];
    select.innerHTML = `<option value="">${t('allReasonCategories')}</option>` +
        allItems.map(c => `<option value="${c.key}">${getReasonCategoryLabel(c.key)}</option>`).join('');
}

function parseBatchDetails(batchDetails) {
    if (!batchDetails) return [];
    return batchDetails.split(',').map(part => {
        const text = part.trim();
        const match = text.match(/^(.*?)[×xX]\s*(\d+)$/);
        if (!match) return { batchNo: text, quantity: null };
        return {
            batchNo: match[1].trim(),
            quantity: Number.parseInt(match[2], 10),
        };
    }).filter(item => item.batchNo);
}

function expandRecordRows(items) {
    const rows = [];
    items.forEach(item => {
        const consumptions = item.type === 'out' ? parseBatchDetails(item.batch_details) : [];
        if (consumptions.length === 0) {
            rows.push({ ...item, display_quantity: item.quantity, display_batch_no: item.batch_no || '-' });
            return;
        }
        consumptions.forEach(consumption => {
            rows.push({
                ...item,
                display_quantity: consumption.quantity ?? item.quantity,
                display_batch_no: consumption.batchNo,
            });
        });
    });
    return rows;
}

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
    const reasonCategory = document.getElementById('filter-reason-category')?.value || '';
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
        reasonCategory: reasonCategory || undefined,
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
        tbody.innerHTML = `<tr><td colspan="14" style="text-align: center; color: #999;">${t('noRecords')}</td></tr>`;
        return;
    }

    expandRecordRows(items).forEach(item => {
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

        tr.innerHTML = `
            <td>${item.created_at}</td>
            <td>${item.material_name}</td>
            <td>${item.variant || '-'}</td>
            <td>${item.material_sku}</td>
            <td>${item.category || '-'}</td>
            <td><span class="type-badge ${typeClass}">${typeText}</span></td>
            <td><strong>${item.display_quantity}</strong></td>
            <td>${item.display_batch_no}</td>
            <td>${item.contact_name || '-'}</td>
            <td>${item.operator_name || item.operator}</td>
            <td>${item.actual_operator || '-'}</td>
            <td>${getReasonCategoryLabel(item.reason_category)}</td>
            <td>${item.reason_note || '-'}</td>
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
    document.getElementById('filter-reason-category').value = '';
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
            operatorsApi.getList(),
            loadReasonCategories(),
        ]);

        // 原因分类筛选
        populateReasonCategoryFilterSelect();

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
export async function showAddRecordModal() {
    if (!currentWarehouse) {
        alert(t('writeRequiresWarehouse') || '写操作需要选择具体仓库');
        return;
    }
    addRecordForProduct = false;
    document.getElementById('record-product-group').style.display = 'block';
    document.getElementById('record-product-display-group').style.display = 'none';
    document.getElementById('add-record-modal').classList.add('show');
    document.getElementById('add-record-form').reset();
    clearRecordProductSelector();
    await loadReasonCategories();
    populateReasonCategorySelect('record-reason-category', 'in');
    loadContactsForRecord('in');
    updateLocationFieldVisibility('in');
    document.querySelectorAll('input[name="record-type"]').forEach(radio => {
        radio.onchange = () => {
            populateReasonCategorySelect('record-reason-category', radio.value);
            loadContactsForRecord(radio.value);
            updateLocationFieldVisibility(radio.value);
        };
    });
    setupFormEnterNavigation();
    setTimeout(() => document.getElementById('record-product-input')?.focus(), 100);
}

export async function showAddRecordModalForProduct() {
    if (!currentWarehouse) {
        alert(t('writeRequiresWarehouse') || '写操作需要选择具体仓库');
        return;
    }
    if (!currentProductName) return;
    addRecordForProduct = true;
    document.getElementById('record-product-group').style.display = 'none';
    document.getElementById('record-product-display-group').style.display = 'block';
    document.getElementById('record-product-display').value = currentProductName;
    document.getElementById('add-record-modal').classList.add('show');
    document.getElementById('add-record-form').reset();
    document.getElementById('record-product-display').value = currentProductName;
    await loadReasonCategories();
    populateReasonCategorySelect('record-reason-category', 'in');
    loadContactsForRecord('in');
    updateLocationFieldVisibility('in');
    document.querySelectorAll('input[name="record-type"]').forEach(radio => {
        radio.onchange = () => {
            populateReasonCategorySelect('record-reason-category', radio.value);
            loadContactsForRecord(radio.value);
            updateLocationFieldVisibility(radio.value);
        };
    });
    setupFormEnterNavigation();
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

// 根据操作类型切换字段可见性
function updateLocationFieldVisibility(type) {
    const locationGroup = document.getElementById('record-location-group');
    const batchNoGroup = document.getElementById('record-batch-group'); // 入库自定义批次号
    const variantGroup = document.getElementById('record-variant-group');
    const batchSelectGroup = document.getElementById('record-batch-select-group');

    // location 和 variant 两种操作都显示
    if (locationGroup) locationGroup.style.display = 'block';
    if (variantGroup) variantGroup.style.display = 'block';

    // 入库时显示"自定义批次号"输入，出库时显示"指定批次"下拉
    if (batchNoGroup) batchNoGroup.style.display = type === 'in' ? 'block' : 'none';
    if (batchSelectGroup) batchSelectGroup.style.display = type === 'out' ? 'block' : 'none';

    if (type === 'out') {
        populateBatchSelectForCurrentProduct();
    } else {
        const sel = document.getElementById('record-batch-select');
        if (sel) sel.value = '';
    }
}

export async function populateBatchSelectForCurrentProduct() {
    const sel = document.getElementById('record-batch-select');
    if (!sel) return;
    sel.innerHTML = `<option value="">${t('autoFIFO')}</option>`;

    const productName = addRecordForProduct
        ? currentProductName
        : document.getElementById('record-product').value;
    if (!productName) return;

    const whId = getCurrentWarehouseId();
    if (!whId) return;

    try {
        const params = new URLSearchParams({ name: productName, warehouse_id: String(whId) });
        const resp = await fetch(`/api/materials/batches?${params.toString()}`, {
            credentials: 'include',
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        const batches = data.batches || [];
        for (const b of batches) {
            if (!b.quantity || b.quantity <= 0) continue;
            const parts = [b.batch_no];
            if (b.location) parts.push(b.location);
            if (b.variant) parts.push(b.variant);
            parts.push(`余 ${b.quantity}`);
            const label = parts.join(' · ');
            const opt = document.createElement('option');
            opt.value = b.batch_no;
            opt.textContent = label;
            opt.dataset.location = b.location || '';
            opt.dataset.variant = b.variant || '';
            sel.appendChild(opt);
        }
        setupBatchSelectChangeListener();
    } catch (err) {
        console.error('[records] load batches failed:', err);
    }
}

function setupBatchSelectChangeListener() {
    const sel = document.getElementById('record-batch-select');
    const locationInput = document.getElementById('record-location');
    const variantInput = document.getElementById('record-variant');
    if (!sel) return;

    sel.removeEventListener('change', sel._batchChangeHandler || (() => {}));
    const handler = function () {
        if (sel.value && sel.selectedOptions[0]) {
            const opt = sel.selectedOptions[0];
            const batchLoc = opt.dataset.location || '';
            const batchVar = opt.dataset.variant || '';
            if (locationInput) {
                locationInput.value = batchLoc;
                locationInput.readOnly = true;
                locationInput.style.opacity = '0.6';
            }
            if (variantInput) {
                variantInput.value = batchVar;
                variantInput.readOnly = true;
                variantInput.style.opacity = '0.6';
            }
        } else {
            if (locationInput) {
                locationInput.value = '';
                locationInput.readOnly = false;
                locationInput.style.opacity = '1';
            }
            if (variantInput) {
                variantInput.value = '';
                variantInput.readOnly = false;
                variantInput.style.opacity = '1';
            }
        }
    };
    sel._batchChangeHandler = handler;
    sel.addEventListener('change', handler);
}

// Enter 键跳转到下一个输入框（兼容扫码机自动换行）
function setupFormEnterNavigation() {
    const form = document.getElementById('add-record-form');
    if (!form) return;

    // 移除旧监听器（防止重复绑定）
    if (form._enterNavHandler) {
        form.removeEventListener('keydown', form._enterNavHandler);
    }

    const handler = function (e) {
        if (e.key !== 'Enter') return;

        const active = document.activeElement;
        if (!active || !form.contains(active)) return;

        // 产品搜索框由 dropdown.js 处理 Enter（选中 + onSelect 回调跳转），这里不干预
        if (active.id === 'record-product-input') return;

        e.preventDefault();

        if (active.id === 'record-quantity') {
            document.getElementById('record-reason-category')?.focus();
        } else if (active.id === 'record-reason-category') {
            document.getElementById('record-reason-note')?.focus();
        } else if (active.id === 'record-reason-note') {
            document.getElementById('record-contact')?.focus();
        } else if (active.id === 'record-contact') {
            const locationGroup = document.getElementById('record-location-group');
            if (locationGroup && locationGroup.style.display !== 'none') {
                document.getElementById('record-location')?.focus();
            } else {
                document.querySelector('[data-action="submitAddRecord"]')?.click();
            }
        } else if (active.id === 'record-location') {
            document.querySelector('[data-action="submitAddRecord"]')?.click();
        }
    };

    form._enterNavHandler = handler;
    form.addEventListener('keydown', handler);
}

export function closeAddRecordModal() {
    document.getElementById('add-record-modal').classList.remove('show');
    document.getElementById('add-record-form').reset();
    clearRecordProductSelector();
}

export async function submitAddRecord() {
    // 双击/重复提交保护：函数级 flag + 按钮 disabled
    if (isSubmittingRecord) return;
    const submitBtn = document.querySelector('[data-action="submitAddRecord"]');
    isSubmittingRecord = true;
    if (submitBtn) submitBtn.disabled = true;
    try {
    const productName = addRecordForProduct
        ? currentProductName
        : document.getElementById('record-product').value;
    const type = document.querySelector('input[name="record-type"]:checked')?.value;
    const quantity = parseInt(document.getElementById('record-quantity').value);
    const reasonCategory = document.getElementById('record-reason-category').value;
    const reasonNote = document.getElementById('record-reason-note').value.trim() || null;
    const actualOperator = document.getElementById('record-actual-operator')?.value.trim() || null;
    const contactId = document.getElementById('record-contact')?.value || null;

    const location = document.getElementById('record-location')?.value.trim() || null;
    const variant = document.getElementById('record-variant')?.value.trim() || null;

    let batchNo = null;
    let selectedBatchLocation = null;
    let selectedBatchVariant = null;
    if (type === 'in') {
        const batchNoInput = document.getElementById('record-batch-no');
        batchNo = batchNoInput ? (batchNoInput.value.trim() || null) : null;
    } else {
        const sel = document.getElementById('record-batch-select');
        if (sel && sel.value) {
            batchNo = sel.value;
            const opt = sel.selectedOptions[0];
            selectedBatchLocation = opt?.dataset.location || null;
            selectedBatchVariant = opt?.dataset.variant || null;
        }
    }

    if (!productName || !type || !document.getElementById('record-quantity').value || !reasonCategory) {
        alert(t('fillAllFields'));
        return;
    }
    if (isNaN(quantity) || quantity <= 0) {
        alert(t('quantityMustBePositive'));
        return;
    }

    // 入库时检查 location 与现有产品库位是否冲突（原逻辑保留）
    if (type === 'in' && location) {
        const product = allProducts.find(p => p.name === productName);
        if (product && product.location && product.location !== location) {
            if (!confirm(`该产品当前库位为「${product.location}」，是否覆盖为「${location}」？`)) return;
        }
    }

    try {
        const requestData = {
            product_name: productName,
            type: type,
            quantity: quantity,
            reason_category: reasonCategory,
            reason_note: reasonNote,
            actual_operator: actualOperator,
            contact_id: contactId ? parseInt(contactId) : null,
            warehouse_id: getCurrentWarehouseId(),
        };
        const effectiveLocation = selectedBatchLocation || location;
        if (effectiveLocation) requestData.location = effectiveLocation;
        const effectiveVariant = selectedBatchVariant || variant;
        if (effectiveVariant) requestData.variant = effectiveVariant;
        if (batchNo) requestData.batch_no = batchNo;

        const data = await recordsApi.create(requestData);

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
        // 401 由全局 session 过期处理；其它错误兜底提示
        if (error && error.status !== 401) {
            console.error('[records] submitAddRecord error:', error);
            alert(t('operationFailed') || '操作失败');
        }
    }
    } finally {
        isSubmittingRecord = false;
        if (submitBtn) submitBtn.disabled = false;
    }
}
