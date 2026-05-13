// ============ 认证模块 ============
import { t } from '../../../i18n.js';
import { authApi, setSessionExpiredHandler } from '../api.js';
import {
    getCurrentUser, setCurrentUser,
    getIsSystemInitialized, setIsSystemInitialized,
    getCurrentTab, getDeployMode
} from '../state.js';

// 标记是否已显示过期提示（避免重复弹窗）
let sessionExpiredNotified = false;

// 回调函数引用（由 main.js 设置）
let onAuthChange = null;
let switchTabFn = null;
let refreshCurrentTabFn = null;
let onLoginSuccessFn = null;

// 设置回调
export function setAuthCallbacks(callbacks) {
    onAuthChange = callbacks.onAuthChange;
    switchTabFn = callbacks.switchTab;
    refreshCurrentTabFn = callbacks.refreshCurrentTab;
    onLoginSuccessFn = callbacks.onLoginSuccess;
}

// 初始化 session 过期处理
export function initSessionExpiredHandler() {
    setSessionExpiredHandler(handleSessionExpired);
}

// 处理 session 过期
async function handleSessionExpired() {
    // 如果当前没有用户或已经通知过，跳过
    if (!getCurrentUser() || sessionExpiredNotified) return;

    sessionExpiredNotified = true;

    // 清除用户状态
    setCurrentUser(null);
    await updateUserDisplay();
    updatePermissionUI();

    // 如果在需要权限的页面，切换到看板
    if ((getCurrentTab() === 'users' || getCurrentTab() === 'contacts' || getCurrentTab() === 'mcp') && switchTabFn) {
        switchTabFn('dashboard');
    }

    // 提示用户重新登录
    alert(t('sessionExpired') || '登录已过期，请重新登录');
    showLoginModal();

    // 重置通知标记（允许下次再次通知）
    setTimeout(() => {
        sessionExpiredNotified = false;
    }, 3000);
}

// 检查认证状态
export async function checkAuthStatus() {
    try {
        const data = await authApi.getStatus();

        setIsSystemInitialized(data.initialized);

        if (!data.initialized) {
            // 系统未初始化，显示设置模态框
            showSetupModal();
            return;
        }

        if (data.logged_in && data.user) {
            setCurrentUser(data.user);
        } else {
            setCurrentUser(null);
        }

        await updateUserDisplay();
        updatePermissionUI();
    } catch (error) {
        console.error('检查认证状态失败:', error);
        setCurrentUser(null);
        await updateUserDisplay();
    }
}

// 更新用户显示
export async function updateUserDisplay() {
    const nameDisplay = document.getElementById('user-name-display');
    const roleBadge = document.getElementById('user-role-badge');
    const loginBtn = document.getElementById('login-btn');
    const logoutBtn = document.getElementById('logout-btn');

    const user = getCurrentUser();
    if (user) {
        let tenantPrefix = '';
        const dm = getDeployMode();

        if (dm === 'multi_tenant') {
            if (!user.tenant_id) {
                tenantPrefix = `[${t('globalAdmin') || '全局管理'}] `;
            } else {
                // 如果 user 对象中没有 tenant_name，尝试获取
                if (!user.tenant_name) {
                    try {
                        // 优先检查 login response 中是否已经包含 (假设后端已更新)
                        // 如果没有，再尝试从 tenants 列表获取
                        const resp = await fetch('/api/tenants', { credentials: 'include' });
                        if (resp.ok) {
                            const tenants = await resp.json();
                            const tenant = tenants.find(t => t.id === user.tenant_id);
                            if (tenant) {
                                user.tenant_name = tenant.name;
                            }
                        }
                    } catch (e) {
                        console.error('获取租户信息失败:', e);
                    }
                }
                tenantPrefix = `[${user.tenant_name || t('tenant') || '租户'}] `;
            }
        }

        nameDisplay.textContent = tenantPrefix + (user.display_name || user.username);
        roleBadge.textContent = t('role' + user.role.charAt(0).toUpperCase() + user.role.slice(1));
        roleBadge.className = 'user-role-badge ' + user.role;
        roleBadge.style.display = 'inline';
        loginBtn.style.display = 'none';
        logoutBtn.style.display = 'inline';
    } else {
        nameDisplay.textContent = t('guest');
        roleBadge.style.display = 'none';
        loginBtn.style.display = 'inline';
        logoutBtn.style.display = 'none';
    }
}

// 更新权限控制UI
export function updatePermissionUI() {
    const currentUser = getCurrentUser();
    const role = currentUser ? currentUser.role : 'view';
    const roleLevel = { view: 1, operate: 2, admin: 3 };
    const currentLevel = roleLevel[role] || 1;

    // 显示/隐藏联系方管理TAB（operate+）
    const contactsNav = document.getElementById('nav-contacts');
    if (contactsNav) {
        contactsNav.style.display = currentLevel >= 2 ? 'flex' : 'none';
    }

    // 显示/隐藏用户管理TAB（admin only）
    const usersNav = document.getElementById('nav-users');
    if (usersNav) {
        usersNav.style.display = role === 'admin' ? 'flex' : 'none';
    }

    // 显示/隐藏智能体配置TAB（admin only）
    const mcpNav = document.getElementById('nav-mcp');
    if (mcpNav) {
        mcpNav.style.display = role === 'admin' ? 'flex' : 'none';
    }

    // 显示/隐藏租户管理TAB（admin + multi_tenant）
    const tenantsNav = document.getElementById('nav-tenants');
    const dm = getDeployMode();
    if (tenantsNav) {
        // 租户管理仅在 multi_tenant 模式下对全局 admin 可见
        tenantsNav.style.display = (role === 'admin' && dm === 'multi_tenant' && !currentUser?.tenant_id) ? 'flex' : 'none';
    }

}

// 显示登录模态框
export function showLoginModal() {
    document.getElementById('login-modal').classList.add('show');
    document.getElementById('login-username').focus();
    document.getElementById('login-error').style.display = 'none';
}

// 关闭登录模态框
export function closeLoginModal() {
    document.getElementById('login-modal').classList.remove('show');
    document.getElementById('login-form').reset();
    document.getElementById('login-error').style.display = 'none';
}

// 处理登录
export async function handleLogin(event) {
    if (event) event.preventDefault();

    const username = document.getElementById('login-username').value.trim();
    const password = document.getElementById('login-password').value;
    const errorDiv = document.getElementById('login-error');

    if (!username || !password) {
        errorDiv.textContent = t('fillAllFields');
        errorDiv.style.display = 'block';
        return;
    }

    try {
        const data = await authApi.login(username, password);

        if (data.success) {
            setCurrentUser(data.user);
            if (onLoginSuccessFn) await onLoginSuccessFn();
            closeLoginModal();
            await updateUserDisplay();
            updatePermissionUI();
            if (switchTabFn) switchTabFn(getCurrentTab());
            else if (refreshCurrentTabFn) refreshCurrentTabFn();
        } else {
            errorDiv.textContent = data.message || t('loginFailed');
            errorDiv.style.display = 'block';
        }
    } catch (error) {
        console.error('登录失败:', error);
        errorDiv.textContent = t('operationFailed');
        errorDiv.style.display = 'block';
    }
}

// 处理登出
export async function handleLogout() {
    try {
        await authApi.logout();
    } catch (error) {
        console.error('登出失败:', error);
    }

    setCurrentUser(null);
    await updateUserDisplay();
    updatePermissionUI();

    // 如果在需要权限的页面，切换到看板
    if ((getCurrentTab() === 'users' || getCurrentTab() === 'mcp') && switchTabFn) {
        switchTabFn('dashboard');
    }
}

// 显示设置模态框（首次使用）
export function showSetupModal() {
    document.getElementById('setup-modal').classList.add('show');
    document.getElementById('setup-username').focus();
    document.getElementById('setup-error').style.display = 'none';
}

// 处理首次设置
export async function handleSetup(event) {
    if (event) event.preventDefault();

    const username = document.getElementById('setup-username').value.trim();
    const displayName = document.getElementById('setup-display-name').value.trim();
    const password = document.getElementById('setup-password').value;
    const passwordConfirm = document.getElementById('setup-password-confirm').value;
    const errorDiv = document.getElementById('setup-error');

    if (!username || !password) {
        errorDiv.textContent = t('fillAllFields');
        errorDiv.style.display = 'block';
        return;
    }

    if (password !== passwordConfirm) {
        errorDiv.textContent = t('passwordMismatch');
        errorDiv.style.display = 'block';
        return;
    }

    if (password.length < 4) {
        errorDiv.textContent = '密码长度至少4位';
        errorDiv.style.display = 'block';
        return;
    }

    try {
        const data = await authApi.setup(username, password, displayName);

        if (data.success) {
            setCurrentUser(data.user);
            setIsSystemInitialized(true);
            document.getElementById('setup-modal').classList.remove('show');
            await updateUserDisplay();
            updatePermissionUI();
            if (onLoginSuccessFn) await onLoginSuccessFn();
        } else {
            errorDiv.textContent = data.message || t('operationFailed');
            errorDiv.style.display = 'block';
        }
    } catch (error) {
        console.error('设置失败:', error);
        errorDiv.textContent = t('operationFailed');
        errorDiv.style.display = 'block';
    }
}
