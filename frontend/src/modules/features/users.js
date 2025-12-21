// ============ 用户管理模块 ============
import { t } from '../../../i18n.js';
import { usersApi, authApi } from '../api.js';
import { currentUser } from '../state.js';

// 回调函数引用
let checkAuthStatusFn = null;

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
        if (error.message?.includes('401') || error.message?.includes('403')) {
            return;
        }
        console.error('加载用户列表失败:', error);
    }
}

function renderUsersTable(users) {
    const tbody = document.getElementById('users-tbody');
    if (!tbody) return;

    if (!users || users.length === 0) {
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
                ${user.id !== currentUser?.id ? `
                    <button class="action-btn-small ${user.is_disabled ? '' : 'danger'}" data-action="toggleUserStatus" data-user-id="${user.id}" data-is-disabled="${user.is_disabled}">
                        ${user.is_disabled ? t('enable') : t('disable')}
                    </button>
                ` : ''}
            </td>
        </tr>
    `}).join('');
}

// ============ 添加用户 ============
export function showAddUserModal() {
    document.getElementById('add-user-modal').classList.add('show');
    document.getElementById('new-user-username').focus();
    document.getElementById('add-user-error').style.display = 'none';
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
        await usersApi.create({
            username,
            password,
            display_name: displayName || null,
            role
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
        if (currentUser && currentUser.id == userId && checkAuthStatusFn) {
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
        console.error('更新用户状态失败:', error);
        alert(error.message || t('operationFailed'));
    }
}
