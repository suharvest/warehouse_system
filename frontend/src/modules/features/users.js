// ============ 用户管理模块 ============
import { t } from '../../../i18n.js';
import { usersApi, authApi } from '../api.js';
import { getCurrentUser } from '../state.js';

// 回调函数引用
let checkAuthStatusFn = null;

function tt(key, fallback) {
    const value = t(key);
    return value === key ? fallback : value;
}

function escapeHtml(value) {
    const div = document.createElement('div');
    div.textContent = value == null ? '' : String(value);
    return div.innerHTML;
}

// 设置回调
export function setUsersCallbacks(callbacks) {
    checkAuthStatusFn = callbacks.checkAuthStatus;
}

// ============ 用户列表 ============
export async function loadUsers() {
    try {
        const users = await usersApi.getList();
        renderUsersTable(users);
    } catch (error) {
        if (error.status === 401 || error.status === 403) {
            return;
        }
        console.error('加载用户列表失败:', error);
    }
}

function renderUsersTable(users) {
    const tbody = document.getElementById('users-tbody');
    if (!tbody) return;

    if (!Array.isArray(users) || users.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;color:#999;">${t('noData')}</td></tr>`;
        return;
    }

    tbody.innerHTML = users.map(user => {
        const displayNameEscaped = (user.display_name || '').replace(/'/g, "\\'").replace(/"/g, '&quot;');
        return `
        <tr>
            <td>${user.username}</td>
            <td>${user.display_name || '-'}</td>
            <td><span class="user-role-badge ${user.role}">${t('role' + user.role.charAt(0).toUpperCase() + user.role.slice(1))}</span></td>
            <td>${user.is_disabled ? `<span style="color:#ff4d4f;">${t('disabled')}</span>` : `<span style="color:#52c41a;">${t('enabled')}</span>`}</td>
            <td>${user.created_at}</td>
            <td>
                <button class="action-btn-small" data-action="showEditUserModal" data-user-id="${user.id}" data-username="${user.username}" data-display-name="${displayNameEscaped}" data-role="${user.role}">
                    ${t('edit')}
                </button>
                ${user.id !== getCurrentUser()?.id ? `
                    <button class="action-btn-small ${user.is_disabled ? '' : 'danger'}" data-action="toggleUserStatus" data-user-id="${user.id}" data-is-disabled="${user.is_disabled}">
                        ${user.is_disabled ? t('enable') : t('disable')}
                    </button>
                ` : ''}
            </td>
        </tr>
    `}).join('');
}

// ============ 添加用户 ============
export async function showAddUserModal() {
    document.getElementById('add-user-modal').classList.add('show');
    document.getElementById('new-user-username').focus();
    document.getElementById('add-user-error').style.display = 'none';

    const user = getCurrentUser();
    const tenantGroup = document.getElementById('new-user-tenant-group');
    const tenantSelect = document.getElementById('new-user-tenant');
    if (user && !user.tenant_id && tenantGroup) {
        tenantGroup.style.display = '';
        try {
            const resp = await fetch('/api/tenants', { credentials: 'include' });
            if (resp.ok) {
                const tenants = await resp.json();
                tenantSelect.innerHTML = `
                    <option value="global">${tt('globalAdmin', '全局管理')}</option>
                ` + tenants.filter(t => t.is_active !== false).map(t =>
                    `<option value="${escapeHtml(t.id)}">${escapeHtml(t.name)} (${escapeHtml(t.slug)})</option>`
                ).join('');
            }
        } catch (e) { /* ignore */ }
    } else if (tenantGroup) {
        tenantGroup.style.display = 'none';
    }
}

export function closeAddUserModal() {
    document.getElementById('add-user-modal').classList.remove('show');
    document.getElementById('add-user-form').reset();
    document.getElementById('add-user-error').style.display = 'none';
}

export async function handleAddUser() {
    const username = document.getElementById('new-user-username').value.trim();
    const displayName = document.getElementById('new-user-display-name').value.trim();
    const password = document.getElementById('new-user-password').value;
    const role = document.getElementById('new-user-role').value;
    const errorDiv = document.getElementById('add-user-error');

    if (!username || !password) {
        errorDiv.textContent = t('fillAllFields');
        errorDiv.style.display = 'block';
        return;
    }

    try {
        const user = getCurrentUser();
        let tenantId = null;
        if (user && !user.tenant_id) {
            const selectedTenant = document.getElementById('new-user-tenant')?.value;
            tenantId = selectedTenant === 'global' ? null : (parseInt(selectedTenant, 10) || null);
            if (selectedTenant === 'global' && role !== 'admin') {
                errorDiv.textContent = tt('globalUserMustBeAdmin', '全局用户必须是管理员角色');
                errorDiv.style.display = 'block';
                return;
            }
        }
        await usersApi.create({
            username,
            password,
            display_name: displayName || null,
            role,
            tenant_id: tenantId
        });
        closeAddUserModal();
        loadUsers();
    } catch (error) {
        console.error('添加用户失败:', error);
        errorDiv.textContent = error.message || t('operationFailed');
        errorDiv.style.display = 'block';
    }
}

// ============ 编辑用户 ============
export function showEditUserModal(userId, username, displayName, role) {
    document.getElementById('edit-user-id').value = userId;
    document.getElementById('edit-user-username').value = username;
    document.getElementById('edit-user-display-name').value = displayName || '';
    document.getElementById('edit-user-password').value = '';
    document.getElementById('edit-user-role').value = role;
    document.getElementById('edit-user-modal').classList.add('show');
    document.getElementById('edit-user-username').focus();
    document.getElementById('edit-user-error').style.display = 'none';
}

export function closeEditUserModal() {
    document.getElementById('edit-user-modal').classList.remove('show');
    document.getElementById('edit-user-form').reset();
    document.getElementById('edit-user-error').style.display = 'none';
}

export async function handleEditUser() {
    const userId = document.getElementById('edit-user-id').value;
    const username = document.getElementById('edit-user-username').value.trim();
    const displayName = document.getElementById('edit-user-display-name').value.trim();
    const password = document.getElementById('edit-user-password').value;
    const role = document.getElementById('edit-user-role').value;
    const errorDiv = document.getElementById('edit-user-error');

    if (!username) {
        errorDiv.textContent = t('fillAllFields');
        errorDiv.style.display = 'block';
        return;
    }

    const updateData = {
        username,
        display_name: displayName || null,
        role
    };

    if (password) {
        if (password.length < 4) {
            errorDiv.textContent = t('passwordTooShort');
            errorDiv.style.display = 'block';
            return;
        }
        updateData.password = password;
    }

    try {
        await usersApi.update(userId, updateData);
        closeEditUserModal();
        loadUsers();
        if (getCurrentUser() && getCurrentUser().id == userId && checkAuthStatusFn) {
            await checkAuthStatusFn();
        }
    } catch (error) {
        console.error('编辑用户失败:', error);
        errorDiv.textContent = error.message || t('operationFailed');
        errorDiv.style.display = 'block';
    }
}

// ============ 切换用户状态 ============
export async function toggleUserStatus(userId, isDisabled) {
    try {
        await usersApi.toggleStatus(userId, !isDisabled);
        loadUsers();
    } catch (error) {
        if (error.status === 401) return;
        console.error('更新用户状态失败:', error);
        alert(error.message || t('operationFailed'));
    }
}

// ============ 租户信息 ============
export async function loadTenantInfo() {
    const container = document.getElementById('tenant-info-content');
    if (!container) return;

    const dm = localStorage.getItem('deploy_mode') || 'single_tenant';
    const user = getCurrentUser();
    if (dm !== 'multi_tenant' || !user?.tenant_id) {
        container.innerHTML = '';
        container.style.display = 'none';
        return;
    }
    container.style.display = '';

    try {
        const resp = await fetch('/api/tenants', { credentials: 'include' });
        if (!resp.ok) throw new Error('Failed to fetch tenants');
        const tenants = await resp.json();

        const tenant = tenants.find(t => t.id === user.tenant_id);
        if (!tenant) {
            container.innerHTML = `<p class="text-red-500">${tt('tenantInfoNotFound', '无法获取租户信息')}</p>`;
            return;
        }

        const isDisabled = tenant.is_disabled || tenant.is_active === false;
        const statusText = isDisabled ? t('disabled') : t('enabled');
        const statusClass = isDisabled ? 'status-disabled' : 'status-normal';
        const createdAt = escapeHtml(tenant.created_at ? tenant.created_at.split('T')[0] : '-');
        container.innerHTML = `
            <div class="tenant-info-card">
                <div class="tenant-info-header">
                    <div class="tenant-info-name">${escapeHtml(tenant.name)}</div>
                    <span class="status-badge ${statusClass}">${statusText}</span>
                </div>
                <div class="tenant-info-meta">
                    <div class="tenant-info-item">
                        <span class="tenant-info-label">${tt('tenantSlug', '租户标识')}</span>
                        <code>${escapeHtml(tenant.slug)}</code>
                    </div>
                    <div class="tenant-info-item">
                        <span class="tenant-info-label">${t('createdAt')}</span>
                        <span>${createdAt}</span>
                    </div>
                </div>
            </div>
        `;
    } catch (error) {
        console.error('加载租户信息失败:', error);
        container.style.display = '';
        container.innerHTML = `<p class="text-red-500">${tt('loadFailed', '加载失败')}: ${escapeHtml(error.message)}</p>`;
    }
}
