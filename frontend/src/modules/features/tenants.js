// ============ 租户管理模块 ============
import { t } from '../../../i18n.js';
import { getDeployMode, setDeployMode, setFaceEnabled, getCurrentUser } from '../state.js';
import { showToast, showModalSuccessState } from '../ui-components.js';
import { warehousesApi, usersApi } from '../api.js';
import { switchTab } from '../ui/tabs.js';

const API_BASE = '/api';
const DEFAULT_PAGE_SIZE = 20;
let tenantsData = [];
let currentPage = 1;
let pageSize = DEFAULT_PAGE_SIZE;
let isLoading = false;
let globalWarehouses = [];
let globalUsers = [];
let lastCreatedTenantId = null;

function tt(key, fallback) {
    const value = t(key);
    return value === key ? fallback : value;
}

function escapeHtml(value) {
    const div = document.createElement('div');
    div.textContent = value == null ? '' : String(value);
    return div.innerHTML;
}

function parseBool(value) {
    return value === true || value === 'true';
}

function getTotalPages() {
    return Math.max(1, Math.ceil(tenantsData.length / pageSize));
}

function getErrorMessage(error, fallbackKey, fallbackText) {
    return error.detail || error.message || (error.data && error.data.detail) || tt(fallbackKey, fallbackText);
}

// ============ 部署模式 ============
// 唯一的 deploy_mode 来源：调 /api/system/mode（无需登录），结果写入 state.js 的 deployMode（
// 同时 setDeployMode 会顺手缓存到 localStorage 作为下次启动的 fallback）。所有 UI 读 deploy_mode
// 一律通过 getDeployMode()，不要再直接读 localStorage。
export async function fetchDeployMode() {
    try {
        const response = await fetch(`${API_BASE}/system/mode`);
        if (response.ok) {
            const data = await response.json();
            const mode = data.deploy_mode || 'single_tenant';
            setDeployMode(mode);
            // 部署级人脸开关：缺省（旧后端不返回该字段）当 true，只有显式 false 才关。
            setFaceEnabled(data.face_enabled !== false);
            return mode;
        }
        console.warn('获取部署模式失败: HTTP', response.status);
    } catch (error) {
        console.error('获取部署模式失败:', error);
    }
    // 网络/后端不可用时回退到 localStorage 缓存或默认值；setDeployMode 内部保证模块变量与 localStorage 同步
    const cached = localStorage.getItem('deploy_mode') || 'single_tenant';
    setDeployMode(cached);
    return cached;
}

// ============ 租户 CRUD ============
export async function loadTenants() {
    if (getDeployMode() !== 'multi_tenant') return [];
    try {
        const response = await fetch(`${API_BASE}/tenants`, {
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' }
        });
        if (!response.ok) {
            if (response.status === 403) return [];
            throw new Error(`HTTP ${response.status}`);
        }
        const tenants = await response.json();
        
        // Fetch warehouses and users to calculate counts for global admin
        if (getCurrentUser() && getCurrentUser().role === 'admin' && !getCurrentUser().tenant_id) {
            try {
                globalWarehouses = await warehousesApi.getList(true);
                globalUsers = await usersApi.getList();
            } catch (e) {
                console.error('获取全局数据失败:', e);
            }
        }
        
        return tenants.map(tenant => ({
            ...tenant,
            warehouse_count: globalWarehouses.filter(w => w.tenant_id === tenant.id).length,
            user_count: globalUsers.filter(u => u.tenant_id === tenant.id).length
        }));
    } catch (error) {
        console.error('加载租户列表失败:', error);
        throw error;
    }
}

export async function createTenant(data) {
    const response = await fetch(`${API_BASE}/tenants`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    });
    if (!response.ok) {
        const err = await response.json();
        throw new Error(err.error || err.detail || tt('tenantCreateFailed', '创建租户失败'));
    }
    return response.json();
}

export async function updateTenant(tenantId, data) {
    const response = await fetch(`${API_BASE}/tenants/${tenantId}`, {
        method: 'PUT',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    });
    if (!response.ok) {
        const err = await response.json();
        throw new Error(err.error || err.detail || tt('tenantUpdateFailed', '更新租户失败'));
    }
    return response.json();
}

export async function deleteTenant(tenantId) {
    const response = await fetch(`${API_BASE}/tenants/${tenantId}`, {
        method: 'DELETE',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' }
    });
    if (!response.ok) {
        const err = await response.json();
        throw new Error(err.error || err.detail || tt('tenantDisableFailed', '停用租户失败'));
    }
    return response.json();
}

// ============ 租户管理渲染 ============
export function setTenantsPage(p) {
    currentPage = Math.max(1, parseInt(p, 10) || 1);
}

export async function renderTenantsPanel() {
    const panel = document.getElementById('tab-tenants');
    if (!panel) return;
    if (getDeployMode() !== 'multi_tenant') {
        panel.innerHTML = renderEmptyState(
            tt('singleTenantMode', '当前为单租户模式'),
            tt('singleTenantModeHint', '设置 DEPLOY_MODE=multi_tenant 环境变量可启用多租户管理')
        );
        return;
    }
    if (!getCurrentUser() || getCurrentUser().role !== 'admin' || getCurrentUser().tenant_id) {
        panel.innerHTML = renderEmptyState(
            tt('tenantPermissionDenied', '权限不足'),
            tt('tenantGlobalAdminOnly', '仅全局管理员可管理租户')
        );
        return;
    }

    panel.innerHTML = `
        <div class="page-header">
            <h2 class="page-title" data-i18n="tabTenants">${t('tabTenants')}</h2>
        </div>
        <div class="table-container">
            <div class="section-header">
                <div class="section-title" data-i18n="tenantList">${tt('tenantList', '租户列表')}</div>
                ${!getCurrentUser().tenant_id ? renderAddButton() : ''}
            </div>
            <div id="tenants-content">
                ${renderSpinner()}
            </div>
        </div>
    `;

    try {
        isLoading = true;
        tenantsData = await loadTenants();
        currentPage = Math.min(currentPage, getTotalPages());
        renderTenantsTablePanel();
    } catch (error) {
        renderErrorState(document.getElementById('tenants-content'), error);
    } finally {
        isLoading = false;
    }
}

function renderTenantsTablePanel() {
    const container = document.getElementById('tenants-content');
    if (!container) return;
    
    if (!Array.isArray(tenantsData) || tenantsData.length === 0) {
        container.innerHTML = `
            <div class="panel-empty-state">
                <svg class="empty-icon" width="56" height="56" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1" d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4"></path>
                </svg>
                <div class="empty-message">${t('noData')}</div>
                ${!getCurrentUser().tenant_id ? `<button class="btn confirm-btn" data-action="showAddTenantModal">${tt('addTenant', '新增首个租户')}</button>` : ''}
            </div>
        `;
        return;
    }

    const isGlobalAdmin = !getCurrentUser().tenant_id;
    container.innerHTML = `
        <table id="tenants-table">
            <thead>
                <tr>
                    <th width="40"></th>
                    <th data-i18n="tenantId">${tt('tenantId', 'ID')}</th>
                    <th data-i18n="tenantSlug">${tt('tenantSlug', '标识')}</th>
                    <th data-i18n="tenantName">${tt('tenantName', '名称')}</th>
                    <th style="text-align:center;">${tt('warehouseCount', '仓库数')}</th>
                    <th style="text-align:center;">${tt('userCount', '用户数')}</th>
                    <th data-i18n="status">${t('status')}</th>
                    <th data-i18n="createdAt">${t('createdAt')}</th>
                    ${isGlobalAdmin ? `<th data-i18n="actions">${t('actions')}</th>` : ''}
                </tr>
            </thead>
            <tbody id="tenants-tbody"></tbody>
        </table>
        ${renderPagination()}
    `;
    renderTenantsTableRows(isGlobalAdmin);
}

function renderSpinner() {
    return `
        <div class="panel-loading-state">
            <div class="spinner"></div>
            <div>${t('loading') || '加载中...'}</div>
        </div>
    `;
}

function renderErrorState(container, error) {
    if (!container) return;
    container.innerHTML = `
        <div class="panel-error-state">
            <svg class="error-icon" width="44" height="44" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
            </svg>
            <div class="error-title">${tt('loadFailed', '加载失败')}</div>
            <div class="error-message">${escapeHtml(error.message)}</div>
            <button class="btn cancel-btn" data-action="refreshTenantsPanel">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 4v6h-6M1 20v-6h6M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15"></path></svg>
                ${tt('retry', '重试')}
            </button>
        </div>
    `;
}

function renderEmptyState(title, message) {
    return `
        <div class="page-header">
            <h2 class="page-title" data-i18n="tabTenants">${t('tabTenants')}</h2>
        </div>
        <div class="table-container">
            <div class="section-header">
                <div class="section-title">${escapeHtml(title)}</div>
            </div>
            <div class="panel-empty-state">
                <div class="empty-message">${escapeHtml(message)}</div>
            </div>
        </div>
    `;
}

function renderAddButton() {
    return `
        <button class="action-btn add-btn" data-action="showAddTenantModal">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="12" y1="5" x2="12" y2="19"></line>
                <line x1="5" y1="12" x2="19" y2="12"></line>
            </svg>
            <span data-i18n="addTenant">${tt('addTenant', '新增租户')}</span>
        </button>
    `;
}

function renderTenantsTableRows(isGlobalAdmin) {
    const tbody = document.getElementById('tenants-tbody');
    if (!tbody) return;
    
    const start = (currentPage - 1) * pageSize;
    const pageItems = tenantsData.slice(start, start + pageSize);
    
    tbody.innerHTML = pageItems.flatMap(tenant => [
        `
        <tr class="expandable-row" data-action="toggleTenantExpand" data-tenant-id="${tenant.id}">
            <td style="text-align:center;">
                <svg class="expand-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
                    <polyline points="9 18 15 12 9 6"></polyline>
                </svg>
            </td>
            <td>${escapeHtml(tenant.id)}</td>
            <td><code>${escapeHtml(tenant.slug)}</code></td>
            <td>${escapeHtml(tenant.name)}</td>
            <td style="text-align:center;"><span class="badge">${tenant.warehouse_count || 0}</span></td>
            <td style="text-align:center;"><span class="badge">${tenant.user_count || 0}</span></td>
            <td>
                <span class="status-badge ${tenant.is_active ? 'status-normal' : 'status-disabled'}">
                    ${tenant.is_active ? tt('tenantStatusActive', t('enabled')) : tt('tenantStatusInactive', t('disabled'))}
                </span>
            </td>
            <td style="color:#999;">${escapeHtml((tenant.created_at || '').split('T')[0] || '-')}</td>
            ${isGlobalAdmin ? `<td>${renderTenantActions(tenant)}</td>` : ''}
        </tr>
        `,
        `
        <tr class="expanded-content-row" id="tenant-details-${tenant.id}">
            <td colspan="${isGlobalAdmin ? 9 : 8}" class="expanded-content-cell">
                <div class="expanded-panel">
                    <div class="expanded-panel-title">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path></svg>
                        ${tt('warehousesUnderTenant', '所属仓库')}
                    </div>
                    <div class="expanded-panel-grid">
                        ${renderTenantWarehouses(tenant.id)}
                    </div>
                </div>
            </td>
        </tr>
        `
    ]).join('');
}

function renderTenantWarehouses(tenantId) {
    const warehouses = globalWarehouses.filter(w => w.tenant_id === tenantId);
    if (warehouses.length === 0) {
        return `<div style="color:#999;font-size:12px;padding:8px 0;">${t('noData')}</div>`;
    }
    return warehouses.map(wh => `
        <div class="mini-card">
            <div class="mini-card-title">${escapeHtml(wh.name)}</div>
            <div class="mini-card-subtitle"><code>${escapeHtml(wh.slug)}</code></div>
            <div style="margin-top:4px;">
                <span class="status-badge ${wh.is_disabled ? 'status-disabled' : 'status-normal'}">
                    ${wh.is_disabled ? t('disabled') : t('enabled')}
                </span>
            </div>
        </div>
    `).join('');
}

export function toggleTenantExpand(el) {
    const tenantId = el.dataset.tenantId;
    const detailRow = document.getElementById(`tenant-details-${tenantId}`);
    if (!detailRow) return;

    const isShowing = detailRow.classList.contains('show');
    
    // Close others
    document.querySelectorAll('.expanded-content-row.show').forEach(row => {
        if (row !== detailRow) {
            row.classList.remove('show');
            row.previousElementSibling.classList.remove('expanded');
        }
    });

    if (isShowing) {
        detailRow.classList.remove('show');
        el.classList.remove('expanded');
    } else {
        detailRow.classList.add('show');
        el.classList.add('expanded');
    }
}

function renderTenantActions(tenant) {
    return `
        <button class="action-btn-small" data-action="editTenant"
            data-tenant-id="${escapeHtml(tenant.id)}"
            data-tenant-name="${escapeHtml(tenant.name)}"
            data-tenant-active="${tenant.is_active}">
            ${t('edit')}
        </button>
        ${tenant.id !== 1 && tenant.is_active ? `
            <button class="action-btn-small danger" data-action="deleteTenant"
                data-tenant-id="${escapeHtml(tenant.id)}"
                data-tenant-name="${escapeHtml(tenant.name)}">
                ${t('disable')}
            </button>
        ` : ''}
    `;
}

function renderPagination() {
    const totalPages = getTotalPages();
    return `
        <div class="pagination">
            <div>
                <span data-i18n="tenantTotal">${tt('tenantTotal', '共')}</span>
                <span>${tenantsData.length}</span>
                <span data-i18n="recordsUnit">${t('recordsUnit')}</span>
            </div>
            <div class="pagination-controls">
                <button data-action="tenantsPrevPage" ${currentPage <= 1 ? 'disabled' : ''}>${t('prevPage')}</button>
                <span class="page-info">${tt('tenantPageInfo', '第')} ${currentPage} / ${totalPages}</span>
                <button data-action="tenantsNextPage" ${currentPage >= totalPages ? 'disabled' : ''}>${t('nextPage')}</button>
            </div>
        </div>
    `;
}

// ============ 新增租户 ============
export function showAddTenantModal() {
    const modal = document.getElementById('add-tenant-modal');
    if (!modal) return;
    
    // Reset modal content in case it was showing success state
    modal.innerHTML = `
        <div class="modal-content modal-small">
            <div class="modal-header">
                <h3 data-i18n="addTenant">${tt('addTenant', '新增租户')}</h3>
                <button class="close-btn" data-action="closeAddTenantModal">&times;</button>
            </div>
            <div class="modal-body">
                <form id="tenant-form">
                    <div class="form-group">
                        <label><span data-i18n="tenantSlug">${tt('tenantSlug', '租户标识')}</span> <span class="required">*</span></label>
                        <input type="text" id="tenant-slug" required maxlength="50" pattern="^[a-z0-9][a-z0-9\\-]*$" placeholder="${tt('tenantSlugPlaceholder', '如：company-a')}">
                        <div class="form-hint" data-i18n="tenantSlugHint">${tt('tenantSlugHint', '仅支持小写字母、数字和连字符')}</div>
                    </div>
                    <div class="form-group">
                        <label><span data-i18n="tenantName">${tt('tenantName', '租户名称')}</span> <span class="required">*</span></label>
                        <input type="text" id="tenant-name" required maxlength="100" placeholder="${tt('tenantNamePlaceholder', '如：A公司')}">
                    </div>
                    <div class="form-error" id="tenant-slug-error" hidden></div>
                </form>
            </div>
            <div class="modal-footer">
                <button class="btn cancel-btn" data-action="closeAddTenantModal" data-i18n="cancel">${t('cancel')}</button>
                <button class="btn confirm-btn" data-action="handleAddTenant" data-i18n="confirmCreate">${t('confirmCreate')}</button>
            </div>
        </div>
    `;
    
    modal.classList.add('show');
}

export function closeAddTenantModal() {
    const modal = document.getElementById('add-tenant-modal');
    if (modal) modal.classList.remove('show');
}

export async function handleAddTenant() {
    const slug = document.getElementById('tenant-slug').value.trim();
    const name = document.getElementById('tenant-name').value.trim();
    const errEl = document.getElementById('tenant-slug-error');
    const modal = document.getElementById('add-tenant-modal');

    if (!slug || !name) {
        showFormError(errEl, tt('tenantRequiredFields', t('fillAllFields')));
        return;
    }
    if (!/^[a-z0-9][a-z0-9-]*$/.test(slug)) {
        showFormError(errEl, tt('tenantSlugInvalid', '标识只能包含小写字母、数字和连字符，且不能以连字符开头'));
        return;
    }

    const btn = modal.querySelector('.confirm-btn');
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = t('processing') || '处理中...';

    try {
        const tenant = await createTenant({ slug, name });
        lastCreatedTenantId = tenant.id;
        showToast(tt('tenantCreateSuccess', '租户创建成功'));
        
        showModalSuccessState(modal, {
            title: tt('tenantCreateSuccess', '租户创建成功'),
            message: tt('tenantCreateNextSteps', `租户「${name}」已就绪，接下来您可以：`),
            buttons: [
                { text: tt('addWarehouse', '添加仓库'), action: 'goToAddWarehouse', primary: true },
                { text: tt('inviteUsers', '邀请用户'), action: 'goToUsers', primary: false },
                { text: t('close') || '关闭', action: 'closeAddTenantModal', primary: false }
            ]
        });
        
        await renderTenantsPanel();
    } catch (error) {
        showFormError(errEl, getErrorMessage(error, 'tenantCreateFailed', '创建租户失败'));
        btn.disabled = false;
        btn.textContent = originalText;
    }
}

export function goToAddWarehouse() {
    closeAddTenantModal();
    switchTab('users');
    // Give it a moment to render the sub-tabs
    setTimeout(() => {
        const whSubTab = document.querySelector('[data-action="switchSettingsSubTab"][data-sub-tab="warehouses"]');
        if (whSubTab) whSubTab.click();
        import('./warehouses.js').then(m => m.showAddWarehouseModal(lastCreatedTenantId));
    }, 100);
}

export function refreshTenantsPanel() {
    return renderTenantsPanel();
}

export function goToUsers() {
    closeAddTenantModal();
    switchTab('users');
}

// ============ 编辑租户 ============
export function showEditTenantModal(tenantId, name, isActive) {
    const modal = document.getElementById('edit-tenant-modal');
    if (!modal) return;
    document.getElementById('edit-tenant-id').value = tenantId;
    document.getElementById('edit-tenant-name').value = name;
    document.getElementById('edit-tenant-active').checked = parseBool(isActive);
    const errEl = document.getElementById('edit-tenant-error');
    if (errEl) errEl.hidden = true;
    modal.classList.add('show');
}

export function closeEditTenantModal() {
    const modal = document.getElementById('edit-tenant-modal');
    if (modal) modal.classList.remove('show');
}

export async function handleEditTenant() {
    const tenantId = parseInt(document.getElementById('edit-tenant-id').value, 10);
    const name = document.getElementById('edit-tenant-name').value.trim();
    const isActive = document.getElementById('edit-tenant-active').checked;
    const errEl = document.getElementById('edit-tenant-error');
    if (!name) {
        showFormError(errEl, tt('tenantNameRequired', '名称不能为空'));
        return;
    }
    try {
        await updateTenant(tenantId, { name, is_active: isActive });
        showToast(tt('tenantUpdateSuccess', '租户更新成功'));
        closeEditTenantModal();
        await renderTenantsPanel();
    } catch (error) {
        showFormError(errEl, getErrorMessage(error, 'tenantUpdateFailed', '更新租户失败'));
    }
}

export async function handleDeleteTenant(tenantId, tenantName) {
    const title = tt('confirmDisableTenant', '确定要停用租户');
    const warning = tt('disableTenantWarning', '此操作不可逆。');
    if (!confirm(`${title}「${tenantName}」？\n${warning}`)) return;
    try {
        await deleteTenant(tenantId);
        showToast(tt('tenantDisableSuccess', '租户已停用'));
        await renderTenantsPanel();
    } catch (error) {
        showToast(getErrorMessage(error, 'tenantDisableFailed', '停用租户失败'), 'error');
    }
}

function showFormError(errEl, message) {
    if (!errEl) return;
    errEl.textContent = message;
    errEl.hidden = false;
}

// ============ 分页 ============
export function tenantsPrevPage() {
    if (currentPage > 1) {
        currentPage--;
        renderTenantsPanel();
    }
}

export function tenantsNextPage() {
    if (currentPage < getTotalPages()) {
        currentPage++;
        renderTenantsPanel();
    }
}

// ============ 初始化租户管理 HTML ============
export function getTenantModalsHTML() {
    return `
        <div id="add-tenant-modal" class="modal"></div>
        <div id="edit-tenant-modal" class="modal">
            <div class="modal-content modal-small">
                <div class="modal-header">
                    <h3 data-i18n="editTenant">${tt('editTenant', '编辑租户')}</h3>
                    <button class="close-btn" data-action="closeEditTenantModal">&times;</button>
                </div>
                <div class="modal-body">
                    <form id="edit-tenant-form">
                        <input type="hidden" id="edit-tenant-id">
                        <div class="form-group">
                            <label><span data-i18n="tenantName">${tt('tenantName', '租户名称')}</span> <span class="required">*</span></label>
                            <input type="text" id="edit-tenant-name" required maxlength="100">
                        </div>
                        <div class="form-group">
                            <label class="checkbox-label">
                                <input type="checkbox" id="edit-tenant-active">
                                <span data-i18n="enable">${t('enable')}</span>
                            </label>
                        </div>
                        <div class="form-error" id="edit-tenant-error" hidden></div>
                    </form>
                </div>
                <div class="modal-footer">
                    <button class="btn cancel-btn" data-action="closeEditTenantModal" data-i18n="cancel">${t('cancel')}</button>
                    <button class="btn confirm-btn" data-action="handleEditTenant" data-i18n="submit">${t('submit')}</button>
                </div>
            </div>
        </div>
    `;
}
