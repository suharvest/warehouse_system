// ============ 仓库管理模块 ============
import { t } from '../../../i18n.js';
import { warehousesApi } from '../api.js';
import { getCurrentUser } from '../state.js';

// 刷新仓库切换器的回调
let refreshSwitcherFn = null;
let pendingTenantId = null;

function tt(key, fallback) {
    const value = t(key);
    return value === key ? fallback : value;
}

function escapeHtml(value) {
    const div = document.createElement('div');
    div.textContent = value == null ? '' : String(value);
    return div.innerHTML;
}

export function setWarehousesCallbacks(callbacks) {
    refreshSwitcherFn = callbacks.refreshSwitcher;
}

// ============ 仓库列表 ============
export async function loadWarehouses() {
    try {
        const data = await warehousesApi.getList(true); // include disabled
        renderWarehousesTable(data);
    } catch (error) {
        if (error.status === 401 || error.status === 403) return;
        console.error('加载仓库列表失败:', error);
    }
}

function renderWarehousesTable(warehouses) {
    const tbody = document.getElementById('warehouses-tbody');
    const table = document.getElementById('warehouses-table');
    if (!tbody || !table) return;

    const user = getCurrentUser();
    const isGlobalAdmin = user && !user.tenant_id;
    const dm = localStorage.getItem('deploy_mode') || 'single_tenant';
    const showTenant = isGlobalAdmin && dm === 'multi_tenant';

    // 更新表头
    const thead = table.querySelector('thead tr');
    if (thead) {
        const hasTenantCol = thead.innerHTML.includes('data-i18n="tenant"') || thead.innerHTML.includes('所属租户');
        if (showTenant && !hasTenantCol) {
            const th = document.createElement('th');
            th.setAttribute('data-i18n', 'tenant');
            th.textContent = t('tenant') || '所属租户';
            thead.insertBefore(th, thead.children[1]); // 插在名称后面
        } else if (!showTenant && hasTenantCol) {
            const cols = thead.querySelectorAll('th');
            for (let i = 0; i < cols.length; i++) {
                if (cols[i].getAttribute('data-i18n') === 'tenant' || cols[i].textContent === (t('tenant') || '所属租户')) {
                    cols[i].remove();
                    break;
                }
            }
        }
    }

    if (!Array.isArray(warehouses) || warehouses.length === 0) {
        tbody.innerHTML = `<tr><td colspan="${showTenant ? 7 : 6}" style="text-align:center;color:#999;">${t('noData')}</td></tr>`;
        return;
    }

    tbody.innerHTML = warehouses.map(wh => `
        <tr>
            <td>${escapeHtml(wh.name)}</td>
            ${showTenant ? `<td>${escapeHtml(wh.tenant_name || '-')}</td>` : ''}
            <td><code>${escapeHtml(wh.slug)}</code></td>
            <td>${escapeHtml(wh.address || '-')}</td>
            <td>${wh.is_default ? '★' : '-'}</td>
            <td>${wh.is_disabled ? `<span style="color:#ff4d4f;">${t('disabled')}</span>` : `<span style="color:#52c41a;">${t('enabled')}</span>`}</td>
            <td>
                <button class="action-btn-small" data-action="showEditWarehouseModal"
                    data-wh-id="${wh.id}" data-wh-name="${wh.name}" data-wh-slug="${wh.slug}"
                    data-wh-address="${wh.address || ''}" data-wh-is-default="${wh.is_default}">
                    ${t('edit')}
                </button>
                ${!wh.is_default ? `
                    <button class="action-btn-small ${wh.is_disabled ? '' : 'danger'}"
                        data-action="toggleWarehouseStatus" data-wh-id="${wh.id}" data-is-disabled="${wh.is_disabled}">
                        ${wh.is_disabled ? t('enable') : t('disable')}
                    </button>
                    <button class="action-btn-small danger" data-action="deleteWarehouse"
                        data-wh-id="${wh.id}" data-wh-name="${wh.name}">
                        ${t('delete')}
                    </button>
                ` : ''}
            </td>
        </tr>
    `).join('');
}

// ============ 添加仓库 ============
export async function showAddWarehouseModal(tenantId = null) {
    pendingTenantId = tenantId ? parseInt(tenantId, 10) : null;
    document.getElementById('warehouse-edit-id').value = '';
    document.getElementById('warehouse-name').value = '';
    document.getElementById('warehouse-slug').value = '';
    document.getElementById('warehouse-address').value = '';
    document.getElementById('warehouse-slug').disabled = false;
    document.getElementById('warehouse-modal-title').textContent = t('addWarehouse');
    await setupWarehouseTenantSelect();
    const errEl = document.getElementById('warehouse-error');
    if (errEl) errEl.style.display = 'none';
    document.getElementById('warehouse-modal').classList.add('show');
}

// ============ 编辑仓库 ============
export function showEditWarehouseModal(id, name, slug, address, isDefault) {
    document.getElementById('warehouse-edit-id').value = id;
    document.getElementById('warehouse-name').value = name;
    document.getElementById('warehouse-slug').value = slug;
    document.getElementById('warehouse-slug').disabled = true; // slug 不可修改
    document.getElementById('warehouse-address').value = address;
    document.getElementById('warehouse-modal-title').textContent = t('editWarehouse');
    hideWarehouseTenantSelect();
    const errEl = document.getElementById('warehouse-error');
    if (errEl) errEl.style.display = 'none';
    document.getElementById('warehouse-modal').classList.add('show');
}

export function closeWarehouseModal() {
    document.getElementById('warehouse-modal').classList.remove('show');
    pendingTenantId = null;
}

async function setupWarehouseTenantSelect() {
    const user = getCurrentUser();
    const group = document.getElementById('warehouse-tenant-group');
    const select = document.getElementById('warehouse-tenant');
    const deployMode = localStorage.getItem('deploy_mode') || 'single_tenant';
    if (!group || !select) return;
    if (!user || user.tenant_id || deployMode !== 'multi_tenant') {
        hideWarehouseTenantSelect();
        return;
    }
    group.style.display = '';
    select.disabled = true;
    select.innerHTML = `<option>${tt('loading', '加载中...')}</option>`;
    try {
        const resp = await fetch('/api/tenants', { credentials: 'include' });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const tenants = (await resp.json()).filter(tenant => tenant.is_active !== false);
        select.innerHTML = tenants.map(tenant =>
            `<option value="${escapeHtml(tenant.id)}">${escapeHtml(tenant.name)} (${escapeHtml(tenant.slug)})</option>`
        ).join('');
        if (pendingTenantId && tenants.some(tenant => tenant.id === pendingTenantId)) {
            select.value = String(pendingTenantId);
        }
        select.disabled = tenants.length === 0;
    } catch (error) {
        console.error('加载租户列表失败:', error);
        select.innerHTML = `<option>${tt('tenantLoadFailed', '租户加载失败')}</option>`;
    }
}

function hideWarehouseTenantSelect() {
    const group = document.getElementById('warehouse-tenant-group');
    const select = document.getElementById('warehouse-tenant');
    if (group) group.style.display = 'none';
    if (select) {
        select.innerHTML = '';
        select.disabled = false;
    }
}

// ============ 保存仓库（新建/编辑） ============
export async function handleSaveWarehouse() {
    const id = document.getElementById('warehouse-edit-id').value;
    const name = document.getElementById('warehouse-name').value.trim();
    const slug = document.getElementById('warehouse-slug').value.trim();
    const address = document.getElementById('warehouse-address').value.trim();
    const errEl = document.getElementById('warehouse-error');

    if (!name) {
        errEl.textContent = t('warehouseNameRequired') || '请输入仓库名称';
        errEl.style.display = 'block';
        return;
    }

    if (!id && !slug) {
        errEl.textContent = t('warehouseSlugRequired') || '请输入仓库标识';
        errEl.style.display = 'block';
        return;
    }

    try {
        if (id) {
            await warehousesApi.update(id, { name, address: address || null });
        } else {
            const tenantSelect = document.getElementById('warehouse-tenant');
            const user = getCurrentUser();
            const deployMode = localStorage.getItem('deploy_mode') || 'single_tenant';
            const payload = { slug, name, address: address || null };
            if (user && !user.tenant_id && deployMode === 'multi_tenant') {
                const tenantId = parseInt(tenantSelect?.value, 10);
                if (!tenantId) {
                    errEl.textContent = tt('tenantRequired', '请选择租户');
                    errEl.style.display = 'block';
                    return;
                }
                payload.tenant_id = tenantId;
            }
            await warehousesApi.create(payload);
        }
        closeWarehouseModal();
        loadWarehouses();
        if (refreshSwitcherFn) refreshSwitcherFn();
    } catch (error) {
        const msg = error.detail || error.message || '操作失败';
        errEl.textContent = msg;
        errEl.style.display = 'block';
    }
}

// ============ 启用/禁用仓库 ============
export async function toggleWarehouseStatus(id, isDisabled) {
    try {
        await warehousesApi.update(id, { is_disabled: !isDisabled });
        loadWarehouses();
        if (refreshSwitcherFn) refreshSwitcherFn();
    } catch (error) {
        alert(error.detail || error.message || '操作失败');
    }
}

// ============ 删除仓库 ============
export async function deleteWarehouse(id, name) {
    if (!confirm(`${t('confirmDeleteWarehouse') || '确定删除仓库'}「${name}」？\n${t('deleteWarehouseWarning') || '该仓库下的所有物料和记录也会被删除！'}`)) {
        return;
    }
    try {
        await warehousesApi.delete(id);
        loadWarehouses();
        if (refreshSwitcherFn) refreshSwitcherFn();
    } catch (error) {
        alert(error.detail || error.message || '删除失败');
    }
}
