// ============ 人脸识别管理模块 ============
import { t } from '../../../i18n.js';
import { faceApi, usersApi, warehousesApi } from '../api.js';
import { showToast } from '../ui-components.js';
import { getCurrentUser } from '../state.js';

const DEFAULT_CONFIG = {
    enabled: false,
    mode: 'local',
    endpoint: '',
    auth_token: '',
    embedding_model_tag: '',
    min_confidence: 0.7
};
const SUB_TABS = ['config', 'enroll', 'logs'];
const FACE_OPERATIONS = ['stock_in', 'stock_out', 'transfer', 'adjust'];

let currentSubTab = 'config';
let currentConfig = { ...DEFAULT_CONFIG };
let currentRules = [];
let allUsers = [];
let allWarehouses = [];
let selectedEnrollUserId = null;
let enrollmentItems = [];
let allTenants = [];
let selectedTenantId = null;

function isGlobalAdmin() {
    const u = getCurrentUser();
    return !!(u && u.role === 'admin' && (u.tenant_id == null));
}

function effectiveTenantId() {
    if (isGlobalAdmin()) return selectedTenantId;
    const u = getCurrentUser();
    return u ? u.tenant_id : null;
}
let logsState = {
    page: 1,
    pageSize: 20,
    total: 0,
    items: [],
    filters: { userId: '', operation: '', start: '', end: '' }
};

// ============ 工具函数 ============
function tt(key, fallback) {
    const value = t(key);
    return value === key ? fallback : value;
}

function escapeHtml(value) {
    const div = document.createElement('div');
    div.textContent = value == null ? '' : String(value);
    return div.innerHTML;
}

function getErrorMessage(error, fallbackKey, fallbackText) {
    if (!error) return tt(fallbackKey, fallbackText);
    if (error.data && (error.data.detail || error.data.error)) {
        return error.data.detail || error.data.error;
    }
    return error.detail || error.message || tt(fallbackKey, fallbackText);
}

function readFileAsBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
            const result = String(reader.result || '');
            const idx = result.indexOf(',');
            resolve(idx >= 0 ? result.slice(idx + 1) : result);
        };
        reader.onerror = () => reject(reader.error || new Error('read file failed'));
        reader.readAsDataURL(file);
    });
}

// ============ 入口渲染 ============
export async function renderFaceRecognitionPanel() {
    const panel = document.getElementById('settings-panel-face-recognition');
    if (!panel) return;
    if (isGlobalAdmin() && allTenants.length === 0) {
        try {
            const r = await fetch('/api/tenants', { credentials: 'include' });
            if (r.ok) allTenants = (await r.json()).filter(x => x.is_active !== false);
        } catch { allTenants = []; }
        if (allTenants.length > 0 && !selectedTenantId) {
            selectedTenantId = allTenants[0].id;
        }
    }
    panel.innerHTML = renderShell();
    if (isGlobalAdmin() && !selectedTenantId) return;  // wait for selection
    await switchFaceSubTab(currentSubTab);
}

export async function onFaceTenantChange(el) {
    const v = parseInt(el.value, 10);
    selectedTenantId = Number.isFinite(v) ? v : null;
    allUsers = []; allWarehouses = []; enrollmentItems = []; selectedEnrollUserId = null;
    await renderFaceRecognitionPanel();
}

function renderTenantBar() {
    if (!isGlobalAdmin()) return '';
    if (allTenants.length === 0) {
        return `<div class="panel-empty-state"><div class="empty-message">${tt('tenantNoneAvailable', '暂无可管理的租户')}</div></div>`;
    }
    const opts = allTenants.map(tn => `
        <option value="${tn.id}" ${String(selectedTenantId) === String(tn.id) ? 'selected' : ''}>${escapeHtml(tn.name)} (${escapeHtml(tn.slug)})</option>
    `).join('');
    return `
        <div class="form-group" style="display:flex;align-items:center;gap:8px;padding:8px 16px;background:#fafafa;border-bottom:1px solid #f0f0f0;">
            <label style="margin:0;font-size:13px;color:#666;">${tt('tenant', '租户')}:</label>
            <select id="face-tenant-select" data-action-change="onFaceTenantChange" style="min-width:240px;">
                ${opts}
            </select>
        </div>
    `;
}

function renderShell() {
    const tabs = [
        { key: 'config', label: tt('faceConfig', '配置与规则') },
        { key: 'enroll', label: tt('faceEnrollments', '人脸录入') },
        { key: 'logs', label: tt('faceLogs', '审计日志') }
    ];
    return `
        <div class="page-header">
            <h2 class="page-title">${tt('faceRecognition', '人脸识别')}</h2>
        </div>
        ${renderTenantBar()}
        <div class="sub-tabs" id="face-sub-tabs">
            ${tabs.map(tab => `
                <button class="sub-tab ${tab.key === currentSubTab ? 'active' : ''}" data-action="switchFaceSubTab" data-sub-tab="${tab.key}">
                    <span>${escapeHtml(tab.label)}</span>
                </button>
            `).join('')}
        </div>
        <div id="face-content"></div>
    `;
}

export async function switchFaceSubTab(subTab) {
    if (!SUB_TABS.includes(subTab)) subTab = 'config';
    currentSubTab = subTab;
    document.querySelectorAll('#face-sub-tabs .sub-tab').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.subTab === subTab);
    });
    const content = document.getElementById('face-content');
    if (!content) return;
    content.innerHTML = renderLoading();
    try {
        if (subTab === 'config') {
            await loadConfigAndRules();
            content.innerHTML = renderConfigTab();
        } else if (subTab === 'enroll') {
            await loadUsersAndWarehouses();
            content.innerHTML = renderEnrollTab();
            if (selectedEnrollUserId) {
                await loadEnrollmentsForSelected();
            }
        } else if (subTab === 'logs') {
            await loadUsersAndWarehouses();
            content.innerHTML = renderLogsTab();
            await reloadLogs();
        }
    } catch (error) {
        renderErrorState(content, error);
    }
}

function renderLoading() {
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
            <div class="error-message">${escapeHtml(getErrorMessage(error, 'loadFailed', '加载失败'))}</div>
            <button class="btn cancel-btn" data-action="refreshFacePanel">${tt('retry', '重试')}</button>
        </div>
    `;
}

export function refreshFacePanel() {
    return switchFaceSubTab(currentSubTab);
}

// ============ 数据加载 ============
async function loadConfigAndRules() {
    const tid = effectiveTenantId();
    const [config, rules] = await Promise.all([
        faceApi.getConfig(tid).catch(() => ({})),
        faceApi.getRules(tid).catch(() => [])
    ]);
    currentConfig = { ...DEFAULT_CONFIG, ...(config || {}) };
    currentRules = Array.isArray(rules) ? rules : [];
}

async function loadUsersAndWarehouses() {
    if (allUsers.length === 0) {
        try { allUsers = await usersApi.getList(); } catch { allUsers = []; }
    }
    if (allWarehouses.length === 0) {
        try { allWarehouses = await warehousesApi.getList(true); } catch { allWarehouses = []; }
    }
}

// ============ 子页签 A: 配置 ============
function renderConfigTab() {
    const c = currentConfig;
    const modes = [
        { v: 'local', label: tt('mode_local', '本地推理') },
        { v: 'hello', label: tt('mode_hello', 'Hello 服务') },
        { v: 'jetson', label: tt('mode_jetson', 'Jetson 服务') },
        { v: 'custom', label: tt('mode_custom', '自定义') }
    ];
    return `
        <div class="table-container">
            <div class="section-header">
                <div class="section-title">${tt('faceConfig', '人脸识别配置')}</div>
            </div>
            <div style="padding:16px;">
                <div class="form-group">
                    <label class="checkbox-label">
                        <input type="checkbox" id="face-config-enabled" ${c.enabled ? 'checked' : ''}>
                        <span>${tt('faceEnabled', '启用人脸识别')}</span>
                    </label>
                </div>
                <div class="form-group">
                    <label>${tt('faceMode', '识别模式')}</label>
                    <select id="face-config-mode">
                        ${modes.map(m => `<option value="${m.v}" ${c.mode === m.v ? 'selected' : ''}>${escapeHtml(m.label)}</option>`).join('')}
                    </select>
                </div>
                <div class="form-group">
                    <label>${tt('faceEndpoint', '远端服务地址')}</label>
                    <input type="text" id="face-config-endpoint" value="${escapeHtml(c.endpoint || '')}" placeholder="https://example.com/face">
                </div>
                <div class="form-group">
                    <label>${tt('faceAuthToken', '认证 Token')}</label>
                    <input type="password" id="face-config-token" value="${escapeHtml(c.auth_token || '')}" autocomplete="new-password">
                </div>
                <div class="form-group">
                    <label>${tt('faceModelTag', '嵌入模型标签')}</label>
                    <input type="text" id="face-config-model-tag" value="${escapeHtml(c.embedding_model_tag || '')}" placeholder="arcface-r100">
                </div>
                <div class="form-group">
                    <label>${tt('faceMinConfidence', '最低识别置信度')}</label>
                    <input type="number" id="face-config-min-confidence" min="0" max="1" step="0.01" value="${Number(c.min_confidence ?? 0.7)}">
                    <div class="form-hint">0.0 - 1.0</div>
                </div>
                <div class="form-group" style="display:flex;gap:8px;flex-wrap:wrap;">
                    <button class="btn confirm-btn" data-action="saveFaceConfig">${tt('save', t('submit') || '保存')}</button>
                    <button class="btn cancel-btn" data-action="testFaceConnection">${tt('faceTestConnection', '测试连接')}</button>
                    <span id="face-config-test-result" class="form-hint"></span>
                </div>
            </div>
        </div>

        <div class="table-container mt-6">
            <div class="section-header">
                <div class="section-title">${tt('faceRules', '操作规则')}</div>
                <button class="action-btn add-btn" data-action="showAddFaceRuleModal">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
                    <span>${tt('faceAddRule', '新增规则')}</span>
                </button>
            </div>
            <table id="face-rules-table">
                <thead>
                    <tr>
                        <th>${tt('warehouse', t('warehouseName') || '仓库')}</th>
                        <th>${tt('operation', t('recordType') || '操作')}</th>
                        <th>${tt('faceRequireFace', '启用人脸')}</th>
                        <th>${tt('faceAllowedUsers', '允许用户')}</th>
                        <th>${tt('faceMinConfidenceOverride', '自定义阈值')}</th>
                        <th>${t('actions') || '操作'}</th>
                    </tr>
                </thead>
                <tbody id="face-rules-tbody">${renderRulesRows()}</tbody>
            </table>
        </div>
    `;
}

function renderRulesRows() {
    if (!currentRules.length) {
        return `<tr><td colspan="6" style="text-align:center;color:#999;">${t('noData')}</td></tr>`;
    }
    return currentRules.map(rule => {
        const wh = rule.warehouse_id ? (allWarehouses.find(w => w.id === rule.warehouse_id) || {}).name : null;
        const allowedNames = (rule.allowed_user_ids || []).map(id => {
            const u = allUsers.find(u => u.id === id);
            return u ? (u.display_name || u.username) : `#${id}`;
        }).join(', ');
        return `
            <tr>
                <td>${escapeHtml(wh || tt('faceAppliesAll', '全部仓库'))}</td>
                <td>${escapeHtml(rule.operation || '-')}</td>
                <td><span class="status-badge ${rule.require_face ? 'status-normal' : 'status-disabled'}">${rule.require_face ? tt('enabled', t('enabled') || '启用') : tt('disabled', t('disabled') || '停用')}</span></td>
                <td>${escapeHtml(allowedNames || '-')}</td>
                <td>${rule.min_confidence_override == null ? '-' : escapeHtml(String(rule.min_confidence_override))}</td>
                <td>
                    <button class="action-btn-small" data-action="editFaceRule" data-rule-id="${rule.id}">${t('edit') || '编辑'}</button>
                    <button class="action-btn-small danger" data-action="deleteFaceRule" data-rule-id="${rule.id}">${t('delete') || '删除'}</button>
                </td>
            </tr>
        `;
    }).join('');
}

export async function saveFaceConfig() {
    const data = {
        enabled: document.getElementById('face-config-enabled').checked,
        mode: document.getElementById('face-config-mode').value,
        endpoint: document.getElementById('face-config-endpoint').value.trim(),
        auth_token: document.getElementById('face-config-token').value,
        embedding_model_tag: document.getElementById('face-config-model-tag').value.trim(),
        min_confidence: parseFloat(document.getElementById('face-config-min-confidence').value) || 0
    };
    try {
        await faceApi.updateConfig(data, effectiveTenantId());
        currentConfig = { ...currentConfig, ...data };
        showToast(tt('faceConfigSaved', '配置已保存'));
    } catch (error) {
        showToast(getErrorMessage(error, 'faceConfigSaveFailed', '配置保存失败'), 'error');
    }
}

export async function testFaceConnection() {
    const endpoint = document.getElementById('face-config-endpoint').value.trim();
    const auth_token = document.getElementById('face-config-token').value;
    const resultEl = document.getElementById('face-config-test-result');
    if (!endpoint) {
        if (resultEl) resultEl.textContent = tt('faceEndpointRequired', '请填写远端服务地址');
        return;
    }
    if (resultEl) resultEl.textContent = tt('processing', t('processing') || '测试中...');
    try {
        const result = await faceApi.testConnection({ endpoint, auth_token });
        if (result && result.success) {
            const tag = result.info && result.info.model_tag ? ` (${result.info.model_tag})` : '';
            if (resultEl) resultEl.textContent = `${tt('faceConnectionOk', '连接成功')}${tag}`;
        } else {
            const err = result && result.error ? `: ${result.error}` : '';
            if (resultEl) resultEl.textContent = `${tt('faceConnectionFailed', '连接失败')}${err}`;
        }
    } catch (error) {
        if (resultEl) resultEl.textContent = `${tt('faceConnectionFailed', '连接失败')}: ${getErrorMessage(error, 'faceConnectionFailed', '连接失败')}`;
    }
}

// ============ 规则编辑模态 ============
export function showAddFaceRuleModal() {
    openRuleModal(null);
}

export function editFaceRule(el) {
    const ruleId = parseInt(el.dataset.ruleId, 10);
    const rule = currentRules.find(r => r.id === ruleId);
    if (rule) openRuleModal(rule);
}

function openRuleModal(rule) {
    const modal = document.getElementById('face-rule-modal');
    if (!modal) return;
    const isEdit = !!rule;
    const r = rule || { warehouse_id: null, operation: 'stock_out', require_face: true, allowed_user_ids: [], min_confidence_override: null };
    const operations = FACE_OPERATIONS;
    modal.innerHTML = `
        <div class="modal-content modal-small">
            <div class="modal-header">
                <h3>${isEdit ? tt('faceEditRule', '编辑规则') : tt('faceAddRule', '新增规则')}</h3>
                <button class="close-btn" data-action="closeFaceRuleModal">&times;</button>
            </div>
            <div class="modal-body">
                <form id="face-rule-form">
                    <input type="hidden" id="face-rule-id" value="${isEdit ? r.id : ''}">
                    <div class="form-group">
                        <label>${tt('warehouse', t('warehouseName') || '仓库')}</label>
                        <select id="face-rule-warehouse">
                            <option value="">${tt('faceAppliesAll', '全部仓库')}</option>
                            ${allWarehouses.map(w => `<option value="${w.id}" ${r.warehouse_id === w.id ? 'selected' : ''}>${escapeHtml(w.name)}</option>`).join('')}
                        </select>
                    </div>
                    <div class="form-group">
                        <label>${tt('operation', '操作')} <span class="required">*</span></label>
                        <select id="face-rule-operation">
                            ${operations.map(op => `<option value="${op}" ${r.operation === op ? 'selected' : ''}>${escapeHtml(op)}</option>`).join('')}
                        </select>
                    </div>
                    <div class="form-group">
                        <label class="checkbox-label">
                            <input type="checkbox" id="face-rule-require" ${r.require_face ? 'checked' : ''}>
                            <span>${tt('faceRequireFace', '启用人脸')}</span>
                        </label>
                    </div>
                    <div class="form-group">
                        <label>${tt('faceAllowedUsers', '允许用户')}</label>
                        <div id="face-rule-users" style="max-height:160px;overflow:auto;border:1px solid var(--border-color, #e5e7eb);border-radius:4px;padding:8px;">
                            ${allUsers.length === 0 ? `<div style="color:#999;">${t('noData')}</div>` : allUsers.map(u => `
                                <label class="checkbox-label" style="display:block;">
                                    <input type="checkbox" value="${u.id}" ${(r.allowed_user_ids || []).includes(u.id) ? 'checked' : ''}>
                                    <span>${escapeHtml(u.display_name || u.username)}</span>
                                </label>
                            `).join('')}
                        </div>
                        <div class="form-hint">${tt('faceAllowedUsersHint', '为空表示所有用户均需通过人脸识别')}</div>
                    </div>
                    <div class="form-group">
                        <label>${tt('faceMinConfidenceOverride', '自定义阈值')}</label>
                        <input type="number" id="face-rule-confidence" min="0" max="1" step="0.01" value="${r.min_confidence_override == null ? '' : r.min_confidence_override}" placeholder="${tt('faceLeaveBlankInherit', '留空则继承全局')}">
                    </div>
                    <div class="form-error" id="face-rule-error" hidden></div>
                </form>
            </div>
            <div class="modal-footer">
                <button class="btn cancel-btn" data-action="closeFaceRuleModal">${t('cancel') || '取消'}</button>
                <button class="btn confirm-btn" data-action="saveFaceRule">${t('submit') || '提交'}</button>
            </div>
        </div>
    `;
    modal.classList.add('show');
}

export function closeFaceRuleModal() {
    const modal = document.getElementById('face-rule-modal');
    if (modal) modal.classList.remove('show');
}

export async function saveFaceRule() {
    const idVal = document.getElementById('face-rule-id').value;
    const whVal = document.getElementById('face-rule-warehouse').value;
    const op = document.getElementById('face-rule-operation').value;
    const requireFace = document.getElementById('face-rule-require').checked;
    const confidenceVal = document.getElementById('face-rule-confidence').value;
    const allowedIds = Array.from(document.querySelectorAll('#face-rule-users input[type="checkbox"]:checked')).map(cb => parseInt(cb.value, 10));
    const errEl = document.getElementById('face-rule-error');

    const data = {
        warehouse_id: whVal ? parseInt(whVal, 10) : null,
        operation: op,
        require_face: requireFace,
        allowed_user_ids: allowedIds,
        min_confidence_override: confidenceVal === '' ? null : parseFloat(confidenceVal)
    };
    try {
        const tid = effectiveTenantId();
        if (idVal) {
            await faceApi.updateRule(parseInt(idVal, 10), data, tid);
        } else {
            await faceApi.createRule(data, tid);
        }
        closeFaceRuleModal();
        showToast(tt('faceRuleSaved', '规则已保存'));
        await loadConfigAndRules();
        const tbody = document.getElementById('face-rules-tbody');
        if (tbody) tbody.innerHTML = renderRulesRows();
    } catch (error) {
        if (errEl) {
            errEl.hidden = false;
            errEl.textContent = getErrorMessage(error, 'faceRuleSaveFailed', '规则保存失败');
        }
    }
}

export async function deleteFaceRule(el) {
    const ruleId = parseInt(el.dataset.ruleId, 10);
    if (!confirm(tt('faceRuleDeleteConfirm', '确定要删除该规则吗？'))) return;
    try {
        await faceApi.deleteRule(ruleId, effectiveTenantId());
        showToast(tt('faceRuleDeleted', '规则已删除'));
        await loadConfigAndRules();
        const tbody = document.getElementById('face-rules-tbody');
        if (tbody) tbody.innerHTML = renderRulesRows();
    } catch (error) {
        showToast(getErrorMessage(error, 'faceRuleDeleteFailed', '删除失败'), 'error');
    }
}

// ============ 子页签 B: 录入 ============
function renderEnrollTab() {
    const eligibleUsers = allUsers.filter(u => ['operate', 'admin'].includes((u.role || '').toLowerCase()));
    return `
        <div class="table-container">
            <div class="section-header">
                <div class="section-title">${tt('faceEnrollments', '人脸录入')}</div>
            </div>
            <div style="display:grid;grid-template-columns:280px 1fr;gap:16px;padding:16px;">
                <div>
                    <div class="section-title" style="margin-bottom:8px;">${tt('userList', '用户列表')}</div>
                    <div id="face-enroll-user-list" style="border:1px solid var(--border-color, #e5e7eb);border-radius:4px;max-height:480px;overflow:auto;">
                        ${eligibleUsers.length === 0 ? `<div style="padding:12px;color:#999;">${t('noData')}</div>` : eligibleUsers.map(u => `
                            <button class="action-btn-small" data-action="selectFaceEnrollUser" data-user-id="${u.id}" style="display:block;width:100%;text-align:left;border:0;border-bottom:1px solid var(--border-color, #f0f0f0);background:${selectedEnrollUserId === u.id ? 'var(--bg-hover, #f5f5f5)' : 'transparent'};border-radius:0;padding:10px 12px;">
                                <div>${escapeHtml(u.display_name || u.username)}</div>
                                <div class="form-hint" style="margin-top:2px;">${escapeHtml(u.username)}</div>
                            </button>
                        `).join('')}
                    </div>
                </div>
                <div id="face-enroll-detail">
                    ${selectedEnrollUserId ? renderEnrollDetail() : `<div class="panel-empty-state"><div class="empty-message">${tt('faceSelectUserHint', '请选择左侧的用户进行人脸录入')}</div></div>`}
                </div>
            </div>
        </div>
    `;
}

function renderEnrollDetail() {
    const user = allUsers.find(u => u.id === selectedEnrollUserId);
    if (!user) return '';
    return `
        <div class="section-header">
            <div class="section-title">${escapeHtml(user.display_name || user.username)}</div>
            <button class="action-btn add-btn" data-action="showFaceEnrollModal">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
                <span>${tt('faceEnrollAdd', '录入新条目')}</span>
            </button>
        </div>
        <div style="padding:12px 0;">
            <div class="form-hint">${tt('faceEnrolledCount', '已录入条数')}: <strong>${enrollmentItems.length}</strong></div>
        </div>
        <div id="face-enroll-list" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px;">
            ${enrollmentItems.length === 0 ? `<div style="grid-column:1/-1;color:#999;">${t('noData')}</div>` : enrollmentItems.map(renderEnrollmentCard).join('')}
        </div>
    `;
}

function renderEnrollmentCard(item) {
    const whIds = Array.isArray(item.applies_to_warehouse_ids) ? item.applies_to_warehouse_ids : [];
    const whText = whIds.length === 0
        ? tt('faceAppliesAll', '全部仓库')
        : whIds.map(id => (allWarehouses.find(w => w.id === id) || {}).name || `#${id}`).join(', ');
    const created = (item.created_at || '').split('T')[0] || '-';
    return `
        <div class="mini-card">
            <div class="mini-card-title">#${escapeHtml(item.id)}</div>
            <div class="mini-card-subtitle">${escapeHtml(created)}</div>
            <div style="margin-top:6px;font-size:12px;color:#666;">${tt('faceAppliesToWarehouses', '生效仓库')}: ${escapeHtml(whText)}</div>
            <div style="margin-top:8px;">
                <button class="action-btn-small danger" data-action="deleteFaceEnrollment" data-enrollment-id="${item.id}">${t('delete') || '删除'}</button>
            </div>
        </div>
    `;
}

export async function selectFaceEnrollUser(el) {
    selectedEnrollUserId = parseInt(el.dataset.userId, 10);
    const detail = document.getElementById('face-enroll-detail');
    if (detail) detail.innerHTML = renderLoading();
    document.querySelectorAll('#face-enroll-user-list [data-action="selectFaceEnrollUser"]').forEach(btn => {
        btn.style.background = parseInt(btn.dataset.userId, 10) === selectedEnrollUserId ? 'var(--bg-hover, #f5f5f5)' : 'transparent';
    });
    await loadEnrollmentsForSelected();
}

async function loadEnrollmentsForSelected() {
    if (!selectedEnrollUserId) {
        enrollmentItems = [];
        return;
    }
    try {
        const result = await faceApi.getEnrollments({ userId: selectedEnrollUserId, tenantId: effectiveTenantId() });
        enrollmentItems = Array.isArray(result) ? result : (result.items || []);
    } catch {
        enrollmentItems = [];
    }
    const detail = document.getElementById('face-enroll-detail');
    if (detail) detail.innerHTML = renderEnrollDetail();
}

export function showFaceEnrollModal() {
    const modal = document.getElementById('face-enroll-modal');
    if (!modal || !selectedEnrollUserId) return;
    modal.innerHTML = `
        <div class="modal-content modal-small">
            <div class="modal-header">
                <h3>${tt('faceEnrollAdd', '录入新条目')}</h3>
                <button class="close-btn" data-action="closeFaceEnrollModal">&times;</button>
            </div>
            <div class="modal-body">
                <form id="face-enroll-form">
                    <div class="form-group">
                        <label>${tt('faceEnrollImages', '上传人脸图片')} <span class="required">*</span></label>
                        <input type="file" id="face-enroll-images" accept="image/*" multiple>
                        <div class="form-hint">${tt('faceEnrollImagesHint', '可选择多张照片，建议正面清晰图像')}</div>
                    </div>
                    <div class="form-group">
                        <label>${tt('faceAppliesToWarehouses', '生效仓库')}</label>
                        <label class="checkbox-label">
                            <input type="checkbox" id="face-enroll-all-wh" checked>
                            <span>${tt('faceAppliesAll', '全部仓库')}</span>
                        </label>
                        <div id="face-enroll-wh-list" style="display:none;max-height:160px;overflow:auto;border:1px solid var(--border-color, #e5e7eb);border-radius:4px;padding:8px;margin-top:8px;">
                            ${allWarehouses.map(w => `
                                <label class="checkbox-label" style="display:block;">
                                    <input type="checkbox" value="${w.id}">
                                    <span>${escapeHtml(w.name)}</span>
                                </label>
                            `).join('')}
                        </div>
                    </div>
                    <div class="form-error" id="face-enroll-error" hidden></div>
                </form>
            </div>
            <div class="modal-footer">
                <button class="btn cancel-btn" data-action="closeFaceEnrollModal">${t('cancel') || '取消'}</button>
                <button class="btn confirm-btn" data-action="submitFaceEnroll">${t('submit') || '提交'}</button>
            </div>
        </div>
    `;
    modal.classList.add('show');
    const allBox = document.getElementById('face-enroll-all-wh');
    const list = document.getElementById('face-enroll-wh-list');
    if (allBox && list) {
        allBox.addEventListener('change', () => { list.style.display = allBox.checked ? 'none' : ''; });
    }
}

export function closeFaceEnrollModal() {
    const modal = document.getElementById('face-enroll-modal');
    if (modal) modal.classList.remove('show');
}

export async function submitFaceEnroll() {
    const fileInput = document.getElementById('face-enroll-images');
    const errEl = document.getElementById('face-enroll-error');
    const allBox = document.getElementById('face-enroll-all-wh');
    const files = fileInput && fileInput.files ? Array.from(fileInput.files) : [];
    if (!selectedEnrollUserId) return;
    if (files.length === 0) {
        if (errEl) { errEl.hidden = false; errEl.textContent = tt('faceEnrollImagesRequired', '请至少选择一张图片'); }
        return;
    }
    let warehouseIds = [];
    if (!allBox.checked) {
        warehouseIds = Array.from(document.querySelectorAll('#face-enroll-wh-list input[type="checkbox"]:checked')).map(cb => parseInt(cb.value, 10));
    }
    try {
        const images_b64 = await Promise.all(files.map(readFileAsBase64));
        const payload = {
            user_id: selectedEnrollUserId,
            images_b64,
            applies_to_warehouse_ids: warehouseIds
        };
        await faceApi.createEnrollment(payload, effectiveTenantId());
        showToast(tt('faceEnrollSuccess', '录入成功'));
        closeFaceEnrollModal();
        await loadEnrollmentsForSelected();
    } catch (error) {
        if (errEl) { errEl.hidden = false; errEl.textContent = getErrorMessage(error, 'faceEnrollFailed', '录入失败'); }
    }
}

export async function deleteFaceEnrollment(el) {
    const id = parseInt(el.dataset.enrollmentId, 10);
    if (!confirm(tt('faceEnrollDeleteConfirm', '确定要删除该录入条目吗？'))) return;
    try {
        await faceApi.deleteEnrollment(id, effectiveTenantId());
        showToast(tt('faceEnrollDeleted', '录入条目已删除'));
        await loadEnrollmentsForSelected();
    } catch (error) {
        showToast(getErrorMessage(error, 'faceEnrollDeleteFailed', '删除失败'), 'error');
    }
}

// ============ 子页签 C: 审计日志 ============
function renderLogsTab() {
    const f = logsState.filters;
    return `
        <div class="table-container">
            <div class="section-header">
                <div class="section-title">${tt('faceLogs', '审计日志')}</div>
            </div>
            <div style="padding:12px 16px;display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;">
                <div class="form-group">
                    <label>${tt('faceLogOperator', '操作人')}</label>
                    <select id="face-logs-user">
                        <option value="">${tt('all', '全部')}</option>
                        ${allUsers.map(u => `<option value="${u.id}" ${String(f.userId) === String(u.id) ? 'selected' : ''}>${escapeHtml(u.display_name || u.username)}</option>`).join('')}
                    </select>
                </div>
                <div class="form-group">
                    <label>${tt('operation', '操作')}</label>
                    <select id="face-logs-operation">
                        <option value="">${tt('all', '全部')}</option>
                        ${FACE_OPERATIONS.map(op => `<option value="${op}" ${f.operation === op ? 'selected' : ''}>${escapeHtml(op)}</option>`).join('')}
                    </select>
                </div>
                <div class="form-group">
                    <label>${tt('startDate', '开始日期')}</label>
                    <input type="date" id="face-logs-start" value="${escapeHtml(f.start || '')}">
                </div>
                <div class="form-group">
                    <label>${tt('endDate', '结束日期')}</label>
                    <input type="date" id="face-logs-end" value="${escapeHtml(f.end || '')}">
                </div>
                <div class="form-group" style="display:flex;align-items:flex-end;gap:8px;">
                    <button class="btn confirm-btn" data-action="applyFaceLogsFilter">${tt('apply', '应用')}</button>
                    <button class="btn cancel-btn" data-action="resetFaceLogsFilter">${tt('reset', '重置')}</button>
                </div>
            </div>
            <table id="face-logs-table">
                <thead>
                    <tr>
                        <th>${tt('faceLogTime', '时间')}</th>
                        <th>${tt('faceLogOperator', '操作人')}</th>
                        <th>${tt('operation', '操作')}</th>
                        <th>${tt('faceLogMatchedUser', '匹配用户')}</th>
                        <th>${tt('faceLogConfidence', '置信度')}</th>
                        <th>${tt('faceLogDecision', '判定')}</th>
                        <th>${tt('faceLogReason', '原因')}</th>
                    </tr>
                </thead>
                <tbody id="face-logs-tbody">${renderLogsRows()}</tbody>
            </table>
            <div class="pagination">
                <div>
                    <span>${tt('total', t('total') || '共')}</span>
                    <span id="face-logs-total">${logsState.total}</span>
                    <span>${t('recordsUnit') || '条'}</span>
                </div>
                <div class="pagination-controls">
                    <button data-action="faceLogsPrevPage" ${logsState.page <= 1 ? 'disabled' : ''}>${t('prevPage') || '上一页'}</button>
                    <span class="page-info">${logsState.page} / ${Math.max(1, Math.ceil((logsState.total || 0) / logsState.pageSize))}</span>
                    <button data-action="faceLogsNextPage" ${logsState.page >= Math.ceil((logsState.total || 0) / logsState.pageSize) ? 'disabled' : ''}>${t('nextPage') || '下一页'}</button>
                </div>
            </div>
        </div>
    `;
}

function renderLogsRows() {
    if (!logsState.items.length) {
        return `<tr><td colspan="7" style="text-align:center;color:#999;">${t('noData')}</td></tr>`;
    }
    return logsState.items.map(item => {
        const matched = item.matched_user_id ? (allUsers.find(u => u.id === item.matched_user_id) || {}) : null;
        const acting = item.user_id ? (allUsers.find(u => u.id === item.user_id) || {}) : null;
        const decisionKey = `decision_${item.decision || 'skipped'}`;
        const decisionText = tt(decisionKey, item.decision || '-');
        const decisionClass = item.decision === 'pass' ? 'status-normal' : (item.decision === 'deny' ? 'status-disabled' : '');
        return `
            <tr>
                <td>${escapeHtml((item.created_at || '').replace('T', ' ').slice(0, 19))}</td>
                <td>${escapeHtml(acting ? (acting.display_name || acting.username) : (item.user_id ? `#${item.user_id}` : '-'))}</td>
                <td>${escapeHtml(item.operation || '-')}</td>
                <td>${escapeHtml(matched ? (matched.display_name || matched.username) : (item.matched_user_id ? `#${item.matched_user_id}` : '-'))}</td>
                <td>${item.confidence == null ? '-' : escapeHtml(Number(item.confidence).toFixed(3))}</td>
                <td><span class="status-badge ${decisionClass}">${escapeHtml(decisionText)}</span></td>
                <td>${escapeHtml(item.failure_reason || '-')}</td>
            </tr>
        `;
    }).join('');
}

async function reloadLogs() {
    try {
        const f = logsState.filters;
        const result = await faceApi.getLogs({
            userId: f.userId || undefined,
            operation: f.operation || undefined,
            start: f.start || undefined,
            end: f.end || undefined,
            page: logsState.page,
            pageSize: logsState.pageSize,
            tenantId: effectiveTenantId() || undefined
        });
        logsState.items = Array.isArray(result) ? result : (result.items || []);
        logsState.total = (result && typeof result.total === 'number') ? result.total : logsState.items.length;
    } catch {
        logsState.items = [];
        logsState.total = 0;
    }
    const tbody = document.getElementById('face-logs-tbody');
    if (tbody) tbody.innerHTML = renderLogsRows();
    const totalEl = document.getElementById('face-logs-total');
    if (totalEl) totalEl.textContent = String(logsState.total);
}

export async function applyFaceLogsFilter() {
    logsState.filters = {
        userId: document.getElementById('face-logs-user').value,
        operation: document.getElementById('face-logs-operation').value,
        start: document.getElementById('face-logs-start').value,
        end: document.getElementById('face-logs-end').value
    };
    logsState.page = 1;
    await reloadLogs();
}

export async function resetFaceLogsFilter() {
    logsState.filters = { userId: '', operation: '', start: '', end: '' };
    logsState.page = 1;
    const content = document.getElementById('face-content');
    if (content) content.innerHTML = renderLogsTab();
    await reloadLogs();
}

export async function faceLogsPrevPage() {
    if (logsState.page > 1) {
        logsState.page--;
        await reloadLogs();
        const content = document.getElementById('face-content');
        if (content) content.innerHTML = renderLogsTab();
    }
}

export async function faceLogsNextPage() {
    const totalPages = Math.max(1, Math.ceil((logsState.total || 0) / logsState.pageSize));
    if (logsState.page < totalPages) {
        logsState.page++;
        await reloadLogs();
        const content = document.getElementById('face-content');
        if (content) content.innerHTML = renderLogsTab();
    }
}

// ============ 模态容器 ============
export function getFaceModalsHTML() {
    return `
        <div id="face-rule-modal" class="modal"></div>
        <div id="face-enroll-modal" class="modal"></div>
    `;
}
