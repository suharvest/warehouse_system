// ============ 认证模块 ============
import { t } from '../../../i18n.js';
import { authApi } from '../api.js';
import {
    currentUser, setCurrentUser,
    isSystemInitialized, setIsSystemInitialized,
    currentTab
} from '../state.js';

// 回调函数引用（由 main.js 设置）
let onAuthChange = null;
let switchTabFn = null;
let refreshCurrentTabFn = null;

// 设置回调
export function setAuthCallbacks(callbacks) {
    onAuthChange = callbacks.onAuthChange;
    switchTabFn = callbacks.switchTab;
    refreshCurrentTabFn = callbacks.refreshCurrentTab;
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

        updateUserDisplay();
        updatePermissionUI();
    } catch (error) {
        console.error('检查认证状态失败:', error);
        setCurrentUser(null);
        updateUserDisplay();
    }
}

// 更新用户显示
export function updateUserDisplay() {
    const nameDisplay = document.getElementById('user-name-display');
    const roleBadge = document.getElementById('user-role-badge');
    const loginBtn = document.getElementById('login-btn');
    const logoutBtn = document.getElementById('logout-btn');

    if (currentUser) {
        nameDisplay.textContent = currentUser.display_name || currentUser.username;
        roleBadge.textContent = t('role' + currentUser.role.charAt(0).toUpperCase() + currentUser.role.slice(1));
        roleBadge.className = 'user-role-badge ' + currentUser.role;
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
            closeLoginModal();
            updateUserDisplay();
            updatePermissionUI();
            if (refreshCurrentTabFn) refreshCurrentTabFn();
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
    updateUserDisplay();
    updatePermissionUI();

    // 如果在用户管理页面，切换到看板
    if (currentTab === 'users' && switchTabFn) {
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
            updateUserDisplay();
            updatePermissionUI();
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
