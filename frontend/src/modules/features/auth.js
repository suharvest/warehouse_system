// ============ 认证模块 ============
import { t } from '../../../i18n.js';
import { authApi, setSessionExpiredHandler } from '../api.js';
import {
    getCurrentUser, setCurrentUser,
    getIsSystemInitialized, setIsSystemInitialized,
    getCurrentTab, getDeployMode,
    setCurrentWarehouse, setAllWarehouses
} from '../state.js';
import { startOnboarding } from './onboarding.js';

// 标记是否已显示过期提示（避免重复弹窗）
let sessionExpiredNotified = false;

// 自助注册用户名/密码校验（与后端 database.py 保持一致）
const RESERVED_USERNAMES = new Set([
    'admin', 'administrator', 'root', 'superuser', 'sysadmin',
    'system', 'support', 'guest', 'test', 'demo',
    'user', 'null', 'none', 'anonymous', 'watcher',
]);
const WEAK_PASSWORDS = new Set([
    '12345678', '123456789', '1234567890', '11111111', '00000000',
    'password', 'passw0rd', 'password1', 'qwerty12', 'qwertyui',
    'abcdefgh', 'abc12345', 'iloveyou', 'admin123', 'administrator',
    'welcome1', 'letmein1', 'monkey12', 'dragon12', 'football',
    'baseball', 'sunshine', 'princess', 'trustno1', 'asdfghjk',
    '1qaz2wsx', 'zaq12wsx', 'qazwsxedc',
]);
function validateUsername(username) {
    if (!username) return '用户名不能为空';
    if (username.length < 3) return '用户名长度至少3位';
    if (username.length > 64) return '用户名过长（最多64字符）';
    if (RESERVED_USERNAMES.has(username.toLowerCase())) return '该用户名为系统保留，请换一个';
    return null;
}
function validatePasswordStrength(password) {
    if (!password) return '密码不能为空';
    if (password.length < 8) return '密码长度至少8位';
    if (password.length > 128) return '密码过长';
    const hasLetter = /[A-Za-z]/.test(password);
    const hasDigit = /\d/.test(password);
    if (!(hasLetter && hasDigit)) return '密码需同时包含字母和数字';
    if (WEAK_PASSWORDS.has(password.toLowerCase())) return '密码过于常见，请换一个更复杂的密码';
    return null;
}

// 回调函数引用（由 main.js 设置）
let onAuthChange = null;
let switchTabFn = null;
let refreshCurrentTabFn = null;
let onLoginSuccessFn = null;
let onLogoutFn = null;

// 设置回调
export function setAuthCallbacks(callbacks) {
    onAuthChange = callbacks.onAuthChange;
    switchTabFn = callbacks.switchTab;
    refreshCurrentTabFn = callbacks.refreshCurrentTab;
    onLoginSuccessFn = callbacks.onLoginSuccess;
    onLogoutFn = callbacks.onLogout;
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

    // 多租户模式 + 已初始化 → 显示「注册新租户」按钮
    const dm = getDeployMode();
    const initialized = getIsSystemInitialized();
    const registerRow = document.getElementById('register-link-row');
    if (registerRow) {
        registerRow.style.display = (dm === 'multi_tenant' && initialized) ? 'block' : 'none';
    }
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
            if (data.is_first_login) {
                setTimeout(() => startOnboarding(), 500);
            }
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

    // 停止自动刷新定时器
    if (onLogoutFn) onLogoutFn();

    // 清掉会话相关的页面上下文，并整页刷新，避免登出后残留上一位用户的数据。
    setCurrentWarehouse(null);
    setAllWarehouses([]);
    window.location.replace('/');
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
            if (data.is_first_login) {
                setTimeout(() => startOnboarding(), 500);
            }
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

// ============ 自助注册 ============

export function openRegisterModal() {
    closeLoginModal();
    document.getElementById('registerModal').classList.add('show');
    showRegisterStep1();
}

export function closeRegisterModal() {
    document.getElementById('registerModal').classList.remove('show');
    resetRegisterForm();
}

function showRegisterStep1() {
    document.getElementById('register-modal-title').textContent = '注册新租户';
    document.getElementById('register-step1').style.display = 'block';
    document.getElementById('register-step2-new').style.display = 'none';
    document.getElementById('register-step2-reset').style.display = 'none';
    document.getElementById('register-footer-step1').style.display = '';
    document.getElementById('register-footer-step2-new').style.display = 'none';
    document.getElementById('register-footer-step2-reset').style.display = 'none';
    document.getElementById('register-step1-error').style.display = 'none';
    document.getElementById('register-device-id').value = '';
    document.getElementById('register-device-id').focus();
}

function showRegisterStep2New() {
    document.getElementById('register-modal-title').textContent = '创建管理员账号';
    document.getElementById('register-step1').style.display = 'none';
    document.getElementById('register-step2-new').style.display = 'block';
    document.getElementById('register-step2-reset').style.display = 'none';
    document.getElementById('register-footer-step1').style.display = 'none';
    document.getElementById('register-footer-step2-new').style.display = '';
    document.getElementById('register-footer-step2-reset').style.display = 'none';
    document.getElementById('register-username').focus();
}

function showRegisterStep2Reset(tenantName) {
    document.getElementById('register-modal-title').textContent = `重置密码 — ${tenantName}`;
    document.getElementById('register-step1').style.display = 'none';
    document.getElementById('register-step2-new').style.display = 'none';
    document.getElementById('register-step2-reset').style.display = 'block';
    document.getElementById('register-footer-step1').style.display = 'none';
    document.getElementById('register-footer-step2-new').style.display = 'none';
    document.getElementById('register-footer-step2-reset').style.display = '';
    document.getElementById('register-reset-password').focus();
}

function resetRegisterForm() {
    document.getElementById('register-device-id').value = '';
    document.getElementById('register-username').value = '';
    document.getElementById('register-password').value = '';
    document.getElementById('register-display-name').value = '';
    document.getElementById('register-reset-username').value = '';
    document.getElementById('register-reset-password').value = '';
    document.getElementById('register-step1-error').style.display = 'none';
    document.getElementById('register-step2-new-error').style.display = 'none';
    document.getElementById('register-step2-reset-error').style.display = 'none';
}

export function backToRegisterStep1() {
    showRegisterStep1();
}

let registerDeviceOk = false;

export async function registerVerifyDevice() {
    const deviceId = document.getElementById('register-device-id').value.trim();
    const errorDiv = document.getElementById('register-step1-error');
    const checkingDiv = document.getElementById('register-step1-checking');
    const btn = document.getElementById('register-step1-btn');

    if (!deviceId) {
        errorDiv.textContent = '请输入设备 ID';
        errorDiv.style.display = 'block';
        return;
    }

    errorDiv.style.display = 'none';
    checkingDiv.style.display = 'block';
    btn.disabled = true;
    registerDeviceOk = false;

    try {
        const resp = await fetch('/api/auth/register/verify-device', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ device_id: deviceId }),
        });

        const data = await resp.json();

        if (!resp.ok) {
            // 服务端错误：502/503/504
            errorDiv.textContent = data.error || data.detail || data.message || '设备验证服务异常，请稍后重试';
            errorDiv.style.display = 'block';
            return;
        }

        if (!data.authorized) {
            errorDiv.textContent = '设备未授权，请确认设备 ID 正确';
            errorDiv.style.display = 'block';
            return;
        }

        registerDeviceOk = true;

        if (data.registered) {
            showRegisterStep2Reset(data.tenant_name || '未知租户');
        } else {
            showRegisterStep2New();
        }
    } catch (e) {
        console.error('设备验证失败:', e);
        errorDiv.textContent = '网络错误，请检查网络连接后重试';
        errorDiv.style.display = 'block';
    } finally {
        checkingDiv.style.display = 'none';
        btn.disabled = false;
    }
}

export async function registerSubmit() {
    const deviceId = document.getElementById('register-device-id').value.trim();
    const username = document.getElementById('register-username').value.trim();
    const password = document.getElementById('register-password').value;
    const displayName = document.getElementById('register-display-name').value.trim();
    const errorDiv = document.getElementById('register-step2-new-error');
    const submittingDiv = document.getElementById('register-step2-submitting');
    const btn = document.getElementById('register-step2-new-btn');

    if (!username || !password) {
        errorDiv.textContent = '用户名和密码不能为空';
        errorDiv.style.display = 'block';
        return;
    }
    const userErr = validateUsername(username);
    if (userErr) {
        errorDiv.textContent = userErr;
        errorDiv.style.display = 'block';
        return;
    }
    const pwdErr = validatePasswordStrength(password);
    if (pwdErr) {
        errorDiv.textContent = pwdErr;
        errorDiv.style.display = 'block';
        return;
    }

    errorDiv.style.display = 'none';
    submittingDiv.style.display = 'block';
    btn.disabled = true;

    try {
        const resp = await fetch('/api/auth/register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ device_id: deviceId, username, password, display_name: displayName || undefined }),
        });

        if (resp.ok) {
            const data = await resp.json();
            if (data.success) {
                closeRegisterModal();
                setCurrentUser(data.user);
                setIsSystemInitialized(true);
                await updateUserDisplay();
                updatePermissionUI();
                setTimeout(() => startOnboarding(), 500);
                return;
            }
            errorDiv.textContent = data.message || '注册失败';
            errorDiv.style.display = 'block';
        } else {
            const data = await resp.json().catch(() => ({}));
            errorDiv.textContent = data.error || data.detail || data.message || `注册失败（${resp.status}）`;
            errorDiv.style.display = 'block';
        }
    } catch (e) {
        console.error('注册失败:', e);
        errorDiv.textContent = '网络错误，请检查网络连接后重试';
    } finally {
        submittingDiv.style.display = 'none';
        btn.disabled = false;
    }
}

export async function registerResetPassword() {
    const deviceId = document.getElementById('register-device-id').value.trim();
    const username = document.getElementById('register-reset-username').value.trim();
    const newPassword = document.getElementById('register-reset-password').value;
    const errorDiv = document.getElementById('register-step2-reset-error');
    const resettingDiv = document.getElementById('register-step2-resetting');
    const btn = document.getElementById('register-step2-reset-btn');

    if (!username) {
        errorDiv.textContent = '请输入管理员用户名';
        errorDiv.style.display = 'block';
        return;
    }
    const pwdErr = validatePasswordStrength(newPassword);
    if (pwdErr) {
        errorDiv.textContent = pwdErr;
        errorDiv.style.display = 'block';
        return;
    }

    errorDiv.style.display = 'none';
    resettingDiv.style.display = 'block';
    btn.disabled = true;

    try {
        const resp = await fetch('/api/auth/reset-password', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ device_id: deviceId, username, new_password: newPassword }),
        });

        if (resp.ok) {
            const data = await resp.json();
            if (data.success) {
                alert(data.message);
                closeRegisterModal();
                return;
            }
            errorDiv.textContent = data.message || '重置失败';
            errorDiv.style.display = 'block';
        } else {
            const data = await resp.json().catch(() => ({}));
            errorDiv.textContent = data.error || data.detail || data.message || `重置失败（${resp.status}）`;
            errorDiv.style.display = 'block';
        }
    } catch (e) {
        console.error('重置密码失败:', e);
        errorDiv.textContent = '网络错误，请检查网络连接后重试';
        errorDiv.style.display = 'block';
    } finally {
        resettingDiv.style.display = 'none';
        btn.disabled = false;
    }
}
