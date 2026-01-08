// ============ API 密钥管理模块 ============
import { t } from '../../../i18n.js';
import { apiKeysApi } from '../api.js';

// ============ 密钥列表 ============
export async function loadApiKeys() {
    try {
        const keys = await apiKeysApi.getList();
        renderApiKeysTable(keys);
    } catch (error) {
        if (error.status === 401 || error.status === 403) {
            return;
        }
        console.error('加载API密钥列表失败:', error);
    }
}

function renderApiKeysTable(keys) {
    const tbody = document.getElementById('api-keys-tbody');
    if (!tbody) return;

    if (!Array.isArray(keys) || keys.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;color:#999;">${t('noData')}</td></tr>`;
        return;
    }

    tbody.innerHTML = keys.map(key => `
        <tr>
            <td>${key.name}</td>
            <td><span class="user-role-badge ${key.role}">${t('role' + key.role.charAt(0).toUpperCase() + key.role.slice(1))}</span></td>
            <td>${key.is_disabled ? `<span style="color:#ff4d4f;">${t('disabled')}</span>` : `<span style="color:#52c41a;">${t('enabled')}</span>`}</td>
            <td>${key.created_at}</td>
            <td>${key.last_used_at || t('never')}</td>
            <td>
                ${key.is_disabled
                    ? `<button class="action-btn-small success" data-action="toggleApiKeyStatus" data-key-id="${key.id}" data-is-disabled="true">${t('enable')}</button>`
                    : `<button class="action-btn-small danger" data-action="toggleApiKeyStatus" data-key-id="${key.id}" data-is-disabled="false">${t('disable')}</button>`
                }
                <button class="action-btn-small danger" data-action="deleteApiKey" data-key-id="${key.id}" data-key-name="${key.name}">
                    ${t('delete')}
                </button>
            </td>
        </tr>
    `).join('');
}

// ============ 添加密钥 ============
export function showAddApiKeyModal() {
    document.getElementById('add-api-key-modal').classList.add('show');
    document.getElementById('new-api-key-name').focus();
    document.getElementById('add-api-key-error').style.display = 'none';
}

export function closeAddApiKeyModal() {
    document.getElementById('add-api-key-modal').classList.remove('show');
    document.getElementById('add-api-key-form').reset();
    document.getElementById('add-api-key-error').style.display = 'none';
}

export async function handleAddApiKey() {
    const name = document.getElementById('new-api-key-name').value.trim();
    const role = document.getElementById('new-api-key-role').value;
    const errorDiv = document.getElementById('add-api-key-error');

    if (!name) {
        errorDiv.textContent = t('fillAllFields');
        errorDiv.style.display = 'block';
        return;
    }

    try {
        const data = await apiKeysApi.create({ name, role });
        closeAddApiKeyModal();
        loadApiKeys();
        showCreatedApiKey(data.key);
    } catch (error) {
        console.error('添加API密钥失败:', error);
        errorDiv.textContent = error.message || t('operationFailed');
        errorDiv.style.display = 'block';
    }
}

// ============ 显示创建的密钥 ============
function showCreatedApiKey(key) {
    document.getElementById('created-api-key').textContent = key;
    document.getElementById('show-api-key-modal').classList.add('show');
}

export function closeShowApiKeyModal() {
    document.getElementById('show-api-key-modal').classList.remove('show');
}

export function copyApiKey() {
    const keyEl = document.getElementById('created-api-key');
    if (!keyEl) return;
    const key = keyEl.textContent;

    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(key).then(() => {
            alert(t('copied'));
        }).catch(err => {
            console.error('复制失败:', err);
            fallbackCopy(key);
        });
    } else {
        fallbackCopy(key);
    }
}

function fallbackCopy(text) {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.select();
    try {
        document.execCommand('copy');
        alert(t('copied'));
    } catch (err) {
        console.error('复制失败:', err);
        alert('复制失败，请手动复制');
    }
    document.body.removeChild(textarea);
}

// ============ 禁用密钥 ============
export async function disableApiKey(keyId) {
    if (!confirm('确定要禁用此API密钥吗？')) return;

    try {
        await apiKeysApi.toggleStatus(keyId, true);
        loadApiKeys();
    } catch (error) {
        if (error.status === 401) return;
        console.error('禁用API密钥失败:', error);
        alert(error.message || t('operationFailed'));
    }
}

export async function toggleApiKeyStatus(keyId, isDisabled) {
    try {
        await apiKeysApi.toggleStatus(keyId, !isDisabled);
        loadApiKeys();
    } catch (error) {
        if (error.status === 401) return;
        console.error('更新API密钥状态失败:', error);
        alert(error.message || t('operationFailed'));
    }
}

// ============ 删除密钥 ============
export async function deleteApiKey(keyId, keyName) {
    const confirmMsg = t('confirmDeleteApiKey') || `确定要删除API密钥 "${keyName}" 吗？此操作不可撤销。`;
    if (!confirm(confirmMsg.replace('{name}', keyName))) return;

    try {
        await apiKeysApi.delete(keyId);
        loadApiKeys();
    } catch (error) {
        if (error.status === 401) return;
        console.error('删除API密钥失败:', error);
        alert(error.message || t('operationFailed'));
    }
}
