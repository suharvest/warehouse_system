// ============ API 密钥管理模块 ============
import { t } from '../../../i18n.js';
import { apiKeysApi, warehousesApi } from '../api.js';

// 仓库下拉的数据源。密钥列表与两个模态框共用，避免每次开框都打一次接口。
let warehouseOptions = [];

async function ensureWarehouses() {
    if (warehouseOptions.length) return warehouseOptions;
    try {
        const list = await warehousesApi.getList();
        warehouseOptions = Array.isArray(list) ? list : [];
    } catch (error) {
        console.error('加载仓库列表失败:', error);
        warehouseOptions = [];
    }
    return warehouseOptions;
}

function fillWarehouseSelect(select, { selectedId = null, allowEmpty = false } = {}) {
    if (!select) return;
    const opts = [];
    if (allowEmpty) {
        opts.push(`<option value="">${t('apiKeyWarehouseAllAdmin')}</option>`);
    } else {
        opts.push(`<option value="" disabled ${selectedId ? '' : 'selected'}>${t('selectWarehouse')}</option>`);
    }
    for (const w of warehouseOptions) {
        const sel = String(w.id) === String(selectedId) ? 'selected' : '';
        opts.push(`<option value="${w.id}" ${sel}>${w.name}</option>`);
    }
    select.innerHTML = opts.join('');
}

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
        tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;color:#999;">${t('noData')}</td></tr>`;
        return;
    }

    tbody.innerHTML = keys.map(key => `
        <tr>
            <td>${key.name}</td>
            <td><span class="user-role-badge ${key.role}">${t('role' + key.role.charAt(0).toUpperCase() + key.role.slice(1))}</span></td>
            <td>${renderWarehouseCell(key)}</td>
            <td>${key.is_disabled ? `<span style="color:#ff4d4f;">${t('disabled')}</span>` : `<span style="color:#52c41a;">${t('enabled')}</span>`}</td>
            <td>${key.created_at}</td>
            <td>${key.last_used_at || t('never')}</td>
            <td>
                <button class="action-btn-small" data-action="showEditApiKeyModal" data-key-id="${key.id}" data-key-name="${key.name}" data-key-role="${key.role}" data-warehouse-id="${key.warehouse_id == null ? '' : key.warehouse_id}">${t('edit')}</button>
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

function renderWarehouseCell(key) {
    if (key.warehouse_id != null) return key.warehouse_name || `#${key.warehouse_id}`;
    // admin 角色的 NULL 语义是"全仓"；其余角色的 NULL 是"一个仓库都访问不了"。
    if (key.role === 'admin') return t('allWarehouses');
    return `<span style="color:#ff4d4f;">${t('noWarehouseAccess')}</span>`;
}

// ============ 添加密钥 ============
function syncAddWarehouseRequirement() {
    const role = document.getElementById('new-api-key-role').value;
    const select = document.getElementById('new-api-key-warehouse');
    const group = document.getElementById('new-api-key-warehouse-group');
    if (!select || !group) return;
    const isAdmin = role === 'admin';
    select.required = !isAdmin;
    fillWarehouseSelect(select, { selectedId: select.value || null, allowEmpty: isAdmin });
}

export async function showAddApiKeyModal() {
    document.getElementById('add-api-key-modal').classList.add('show');
    document.getElementById('new-api-key-name').focus();
    document.getElementById('add-api-key-error').style.display = 'none';
    await ensureWarehouses();
    syncAddWarehouseRequirement();
    const roleSelect = document.getElementById('new-api-key-role');
    if (roleSelect && !roleSelect.dataset.whBound) {
        roleSelect.addEventListener('change', syncAddWarehouseRequirement);
        roleSelect.dataset.whBound = '1';
    }
}

export function closeAddApiKeyModal() {
    document.getElementById('add-api-key-modal').classList.remove('show');
    document.getElementById('add-api-key-form').reset();
    document.getElementById('add-api-key-error').style.display = 'none';
}

export async function handleAddApiKey() {
    const name = document.getElementById('new-api-key-name').value.trim();
    const role = document.getElementById('new-api-key-role').value;
    const warehouseRaw = document.getElementById('new-api-key-warehouse').value;
    const errorDiv = document.getElementById('add-api-key-error');

    if (!name) {
        errorDiv.textContent = t('fillAllFields');
        errorDiv.style.display = 'block';
        return;
    }
    // 后端对非 admin 角色的空 warehouse_id 返回 400；这里先挡一道，省一次往返。
    if (role !== 'admin' && !warehouseRaw) {
        errorDiv.textContent = t('apiKeyWarehouseHint');
        errorDiv.style.display = 'block';
        return;
    }

    const payload = { name, role };
    if (warehouseRaw) payload.warehouse_id = parseInt(warehouseRaw, 10);

    try {
        const data = await apiKeysApi.create(payload);
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

// ============ 编辑密钥（补绑仓库） ============
export async function showEditApiKeyModal(keyId, keyName, keyRole, warehouseId) {
    await ensureWarehouses();
    document.getElementById('edit-api-key-id').value = keyId;
    document.getElementById('edit-api-key-name').value = keyName;
    const select = document.getElementById('edit-api-key-warehouse');
    fillWarehouseSelect(select, {
        selectedId: warehouseId || null,
        allowEmpty: keyRole === 'admin',
    });
    select.dataset.keyRole = keyRole;
    document.getElementById('edit-api-key-error').style.display = 'none';
    document.getElementById('edit-api-key-modal').classList.add('show');
}

export function closeEditApiKeyModal() {
    document.getElementById('edit-api-key-modal').classList.remove('show');
    document.getElementById('edit-api-key-error').style.display = 'none';
}

export async function handleEditApiKey() {
    const keyId = document.getElementById('edit-api-key-id').value;
    const select = document.getElementById('edit-api-key-warehouse');
    const raw = select.value;
    const errorDiv = document.getElementById('edit-api-key-error');

    if (select.dataset.keyRole !== 'admin' && !raw) {
        errorDiv.textContent = t('apiKeyWarehouseHint');
        errorDiv.style.display = 'block';
        return;
    }

    try {
        await apiKeysApi.update(keyId, { warehouse_id: raw ? parseInt(raw, 10) : null });
        closeEditApiKeyModal();
        loadApiKeys();
    } catch (error) {
        if (error.status === 401) return;
        console.error('更新API密钥失败:', error);
        errorDiv.textContent = error.message || t('operationFailed');
        errorDiv.style.display = 'block';
    }
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
