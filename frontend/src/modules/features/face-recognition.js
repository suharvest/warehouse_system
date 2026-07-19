// ============ 人脸识别管理模块 ============
import { t } from '../../../i18n.js';
import { faceApi, warehousesApi } from '../api.js';
import { showToast } from '../ui-components.js';
import { getCurrentUser, API_BASE_URL } from '../state.js';
import { initFilterDrawers } from '../ui/filter-drawer.js';

const DEFAULT_CONFIG = {
    enabled: false,
    mode: 'local',
    endpoint: '',
    auth_token: '',
    embedding_model_tag: '',
    min_confidence: 0.7,
    verify_frequency: 'always'
};
const SUB_TABS = ['setup', 'logs'];
const FACE_OPERATIONS = ['stock_in', 'stock_out', 'transfer', 'adjust'];

// 本机(local)模式设备端人脸库上限。与固件 FACE_MAX_COUNT=20 / 后端 MAX_PUSH_FACES=20
// 对齐：设备最多存 20 条 embedding（= 20 张图片，不是 20 个人）。lan 模式在端点比对、
// 不受此限。前端在录入时前置拦截，避免录到超限、下发才失败。
const FACE_LOCAL_MAX_ENROLLMENTS = 20;

// 全租户已录入的人脸图片(embedding)总数：各人员 enrollment_count 之和。
function faceTotalEnrollments() {
    return allSubjects.reduce((sum, s) => sum + (Number(s.enrollment_count) || 0), 0);
}

// 当前是否本机模式（非 local 一律视为 lan，与 renderSetupTab 归一化一致）。
function isFaceLocalMode() {
    return (currentConfig && currentConfig.mode) === 'local';
}

let currentSubTab = 'setup';
let currentConfig = { ...DEFAULT_CONFIG };
let currentRules = [];
let allWarehouses = [];
let allSubjects = [];
let selectedSubjectId = null;
let enrollmentItems = [];
let allTenants = [];
let selectedTenantId = null;

// 「待下发」脏检测（纯前端，不追设备实际状态）：进入配置页时以当前 DB 值为基线，
// 用户改动了「需下发到设备才生效」的内容就标出来，下发成功后重置基线。刷新页面即丢失
// （用户已确认此取舍：不为多设备记录各自下发了什么，只提示"你改了需下发的东西"）。
let facePushBaseline = null;      // { mode, min_confidence, endpoint, auth_token }
let facePendingLibrary = false;   // 人脸库/人员增删改 → 需重新下发

function isGlobalAdmin() {
    const u = getCurrentUser();
    return !!(u && u.role === 'admin' && (u.tenant_id == null));
}

function effectiveTenantId() {
    if (isGlobalAdmin()) return selectedTenantId;
    const u = getCurrentUser();
    return u ? u.tenant_id : null;
}
function emptyLogsFilters() {
    return { operation: '', start: '', end: '' };
}
let logsState = {
    page: 1,
    pageSize: 20,
    total: 0,
    items: [],
    filters: emptyLogsFilters()
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

// stock_in 等操作枚举 → 本地化文案。入库/出库复用全站 inbound/outbound 键，
// 避免同一业务概念两套叫法；transfer/adjust 无既有独立键，用 faceOp_*。
const FACE_OP_I18N_KEYS = {
    stock_in: 'inbound',
    stock_out: 'outbound',
    transfer: 'faceOp_transfer',
    adjust: 'faceOp_adjust',
};
function opLabel(op) {
    if (!op) return '-';
    return tt(FACE_OP_I18N_KEYS[op] || `faceOp_${op}`, op);
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
    allWarehouses = []; allSubjects = []; enrollmentItems = []; selectedSubjectId = null;
    logsState.page = 1;
    logsState.filters = emptyLogsFilters();
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
        <div class="face-tenant-bar">
            <span class="face-tenant-label">${tt('tenant', '所属租户')}</span>
            <select id="face-tenant-select" data-action-change="onFaceTenantChange">
                ${opts}
            </select>
        </div>
    `;
}

function renderShell() {
    const tabs = [
        { key: 'setup', label: tt('faceSetup', '配置与录入') },
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
    if (!SUB_TABS.includes(subTab)) subTab = 'setup';
    currentSubTab = subTab;
    document.querySelectorAll('#face-sub-tabs .sub-tab').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.subTab === subTab);
    });
    const content = document.getElementById('face-content');
    if (!content) return;
    content.innerHTML = renderLoading();
    try {
        if (subTab === 'setup') {
            await loadConfigAndRules();
            await loadSubjectsAndWarehouses();
            content.innerHTML = renderSetupTab();
            attachSetupAutoSave();
            captureFacePushBaseline();
            updateFacePendingUI();
            if (selectedSubjectId) {
                await loadEnrollmentsForSelected();
            }
        } else if (subTab === 'logs') {
            await loadSubjectsAndWarehouses();
            content.innerHTML = renderLogsTab();
            initFilterDrawers(content);  // 动态渲染的 filter-bar 补挂移动端筛选抽屉
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
            <button class="btn confirm-btn" data-action="refreshFacePanel">${tt('retry', '重试')}</button>
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

async function loadSubjectsAndWarehouses(force = false) {
    const tid = effectiveTenantId();
    if (force || allSubjects.length === 0) {
        try { allSubjects = await faceApi.getSubjects(tid, true); } catch { allSubjects = []; }
    }
    if (allWarehouses.length === 0) {
        try { allWarehouses = await warehousesApi.getList(true); } catch { allWarehouses = []; }
    }
}

// 强刷人员数据并重渲左侧人员列表（含空态）。人员/录入的增删改后统一走这里，
// 保证计数徽章与空态在所有入口一致。
async function refreshSubjectList() {
    await loadSubjectsAndWarehouses(true);
    const list = document.getElementById('face-enroll-subject-list');
    if (list) {
        list.innerHTML = allSubjects.length === 0
            ? `<div class="face-enroll-users-empty">${tt('faceSubjectsEmpty', '点击右上角「新增人员」开始录入')}</div>`
            : allSubjects.map(renderSubjectItem).join('');
    }
}

// ============ 子页签 A: 配置与录入（合并）============
// 按「归属 + 是否下发到设备」重组：
//   - 「下发到设备」模块：识别设置 + 人脸录入（两者都需下发到设备后生效，下发逻辑统一）
//   - 「操作规则」卡片：MCP Server 侧校验规则，服务端实时生效，不下发
function renderSetupTab() {
    const c = currentConfig;
    const modes = [
        { v: 'local', label: tt('mode_local', '本机') },
        { v: 'lan', label: tt('mode_lan', '局域网设备') }
    ];
    // 老数据可能是 hello/jetson/custom，统一归为 lan
    const currentMode = (c.mode === 'local') ? 'local' : 'lan';
    // 人脸验证频率（与识别模式正交，只控制会话缓存）
    const verifyFrequencies = [
        { v: 'always', label: tt('verifyFrequency_always', '每次操作都验证') },
        { v: 'session', label: tt('verifyFrequency_session', '仅首次验证（之后免验）') }
    ];
    const currentVerifyFrequency = (c.verify_frequency === 'session') ? 'session' : 'always';
    return `
        <!-- 操作规则（服务端·保存即时生效）置前：先决定"要不要刷脸、谁能操作"，
             总开关「启用人脸识别」也是服务端即时生效，一并放这张卡顶部。 -->
        <div class="table-container face-block face-block-server">
            <div class="section-header face-block-header">
                <div class="face-module-heading">
                    <div class="face-block-titlerow">
                        <span class="section-title">${tt('faceRules', '操作规则')}</span>
                        <span class="face-scope-tag is-server">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"></path></svg>
                            ${tt('faceScopeServerTag', '服务端规则 · 保存即时生效')}
                        </span>
                    </div>
                    <div class="face-module-hint">${tt('faceRulesServerHint2', '哪些操作要刷脸、谁能操作，无需下发到设备')}</div>
                </div>
                <div class="face-block-header-actions" style="display:flex;align-items:center;gap:16px;">
                    <label class="face-switch">
                        <input type="checkbox" id="face-config-enabled" data-action-change="onFaceConfigEnabledChange" ${c.enabled ? 'checked' : ''}>
                        <span class="face-switch-slider"></span>
                        <span class="face-switch-text">${tt('faceEnabled', '启用人脸识别')}</span>
                    </label>
                    <button class="action-btn add-btn" data-action="showAddFaceRuleModal">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
                        <span>${tt('faceAddRule', '新增规则')}</span>
                    </button>
                </div>
            </div>
            <div class="face-config-disabled-note" id="face-config-disabled-note" style="margin: 14px 24px;" ${c.enabled ? 'hidden' : ''}>
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>
                <span>${tt('faceDisabledNote', '未启用人脸识别，所有操作规则与下方基础配置均不生效')}</span>
            </div>
            <table id="face-rules-table">
                <thead>
                    <tr>
                        <th>${tt('warehouse', t('warehouseName') || '仓库')}</th>
                        <th>${tt('faceRuleOperation', '操作类型')}</th>
                        <th>${tt('faceRuleStatus', '状态')}</th>
                        <th>${tt('faceAllowedUsers', '允许用户')}</th>
                        <th>${tt('faceMinConfidenceOverride', '自定义阈值')}</th>
                        <th style="width:160px;">${t('actions') || '管理'}</th>
                    </tr>
                </thead>
                <tbody id="face-rules-tbody">${renderRulesRows()}</tbody>
            </table>
        </div>

        <!-- 基础配置（设备侧·需下发到设备后生效）置后 -->
        <div class="table-container mt-6 face-config-card face-block face-block-device">
            <div class="section-header face-block-header">
                <div class="face-module-heading">
                    <div class="face-block-titlerow">
                        <span class="section-title">${tt('faceBaseConfigTitle', '基础配置')}</span>
                        <span class="face-scope-tag is-device face-pending-summary" id="face-pending-summary" hidden>
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M12 5v14"></path><path d="M19 12l-7 7-7-7"></path></svg>
                            <span id="face-pending-summary-text"></span>
                        </span>
                    </div>
                    <div class="face-module-hint">${tt('faceBaseConfigHint', '人脸识别如何工作，以及有哪些人可以被识别')}</div>
                </div>
                <button class="btn confirm-btn face-push-btn" data-action="showFacePushModal" title="${tt('facePushHint', '把人脸库和识别配置（模式/阈值/端点）一并下发到指定设备')}">
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:-2px;margin-right:5px;"><path d="M12 5v14"></path><path d="M19 12l-7 7-7-7"></path></svg>
                    ${tt('facePushToDevice', '下发到设备')}
                </button>
            </div>
            <div class="face-config-body">
                <!-- 分组 A-1：识别设置 -->
                <div class="face-subsection">
                    <div class="face-subsection-head">
                        <div class="face-subsection-title">${tt('faceConfigCardTitle', '识别设置')}</div>
                    </div>
                    <div class="face-settings-row">
                        <div class="form-group">
                            <label>${tt('faceMode', '识别模式')}${pendingBadge('mode')}</label>
                            <select id="face-config-mode" data-action-change="onFaceModeChange">
                                ${modes.map(m => `<option value="${m.v}" ${currentMode === m.v ? 'selected' : ''}>${escapeHtml(m.label)}</option>`).join('')}
                            </select>
                        </div>
                        <div class="form-group">
                            <label>${tt('faceVerifyFrequency', '人脸验证频率')}</label>
                            <select id="face-config-verify-frequency">
                                ${verifyFrequencies.map(m => `<option value="${m.v}" ${currentVerifyFrequency === m.v ? 'selected' : ''}>${escapeHtml(m.label)}</option>`).join('')}
                            </select>
                        </div>
                        <div class="form-group">
                            <label>${tt('faceMinConfidence', '最低识别置信度')} <span class="face-inline-hint">(0.0 - 1.0)</span>${pendingBadge('min_confidence')}</label>
                            <input type="number" id="face-config-min-confidence" min="0" max="1" step="0.01" value="${Number(c.min_confidence ?? 0.7)}">
                        </div>
                    </div>
                    <div class="face-config-grid" style="margin-top:12px;">
                        <div class="form-group span-2" id="face-config-endpoint-group" style="${currentMode === 'local' ? 'display:none;' : ''}">
                            <label>${tt('faceEndpoint', '远端服务地址')}${pendingBadge('endpoint')}</label>
                            <input type="text" id="face-config-endpoint" value="${escapeHtml(c.endpoint || '')}" placeholder="https://example.com/face">
                        </div>
                        <!-- 认证 Token 暂隐藏：face_rec_api 端点当前不校验 token，放出来只会误导。
                             输入框保留在 DOM，保存/测试连接仍读它（不丢已存值），启用端点鉴权后再放出。 -->
                        <div class="form-group span-2" id="face-config-token-group" style="display:none;">
                            <label>${tt('faceAuthToken', '认证 Token')}${pendingBadge('auth_token')}</label>
                            <input type="password" id="face-config-token" value="${escapeHtml(c.auth_token || '')}" autocomplete="new-password">
                        </div>
                        <input type="hidden" id="face-config-model-tag" value="${escapeHtml(c.embedding_model_tag || '')}">
                    </div>
                    <div class="face-config-actions">
                        <span class="face-autosave-note">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"></path></svg>
                            ${tt('faceAutoSaveNote', '修改后自动保存')}
                        </span>
                        <span id="face-config-saved-hint" class="face-saved-hint">${tt('faceSaved', '已保存')}</span>
                        <button class="btn cancel-btn" id="face-config-test-btn" data-action="testFaceConnection" style="${currentMode === 'local' ? 'display:none;' : ''}">${tt('faceTestConnection', '测试连接')}</button>
                        <span id="face-config-test-result" class="form-hint"></span>
                    </div>
                </div>

                <!-- 分组 A-2：人脸录入 -->
                <div class="face-subsection">
                    <div class="face-subsection-head">
                        <div class="face-subsection-title">${tt('faceSubjectsTitle', '人员与录入')}</div>
                        <div class="action-buttons">
                            <button class="btn confirm-btn" data-action="showAddFaceSubjectModal">
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:-2px;margin-right:4px;"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
                                ${tt('faceSubjectAdd', '新增人员')}
                            </button>
                        </div>
                    </div>
                    <div class="face-enroll-grid face-enroll-grid-embed">
                        <aside class="face-enroll-users">
                            <div class="face-enroll-users-header">${tt('faceSubjectList', '人员列表')} <span class="face-enroll-users-count">${allSubjects.length}</span></div>
                            <div id="face-enroll-subject-list" class="face-enroll-users-list">
                                ${allSubjects.length === 0
                                    ? `<div class="face-enroll-users-empty">${tt('faceSubjectsEmpty', '点击右上角「新增人员」开始录入')}</div>`
                                    : allSubjects.map(renderSubjectItem).join('')}
                            </div>
                        </aside>
                        <section id="face-enroll-detail" class="face-enroll-detail">
                            ${selectedSubjectId ? renderEnrollDetail() : renderEnrollPlaceholder()}
                        </section>
                    </div>
                </div>
            </div>
        </div>
    `;
}

function renderRulesRows() {
    if (!currentRules.length) {
        return `<tr><td colspan="6" class="table-empty-cell">${t('noData')}</td></tr>`;
    }
    return currentRules.map(rule => {
        const wh = rule.warehouse_id ? (allWarehouses.find(w => w.id === rule.warehouse_id) || {}).name : null;
        const allowedIds = rule.allowed_subject_ids || [];
        const allowedNames = allowedIds.length === 0
            ? tt('faceAllowedAll', '全部人员')
            : allowedIds.map(id => {
                const s = allSubjects.find(x => x.id === id);
                return s ? s.name : `#${id}`;
            }).join(', ');
        return `
            <tr>
                <td>${escapeHtml(wh || tt('faceAppliesAll', '全部仓库'))}</td>
                <td>${escapeHtml(opLabel(rule.operation))}</td>
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

// mode=local 进程内推理,不需要远端地址/Token → 隐藏这两个字段和「测试连接」
// (测试连接只测远端 face_rec_api,本机模式下无意义);mode=lan 走外部端点 → 显示。
export function onFaceModeChange() {
    const modeEl = document.getElementById('face-config-mode');
    if (!modeEl) return;
    const isLocal = modeEl.value === 'local';
    const endpointGroup = document.getElementById('face-config-endpoint-group');
    const tokenGroup = document.getElementById('face-config-token-group');
    const testBtn = document.getElementById('face-config-test-btn');
    const testResult = document.getElementById('face-config-test-result');
    if (endpointGroup) endpointGroup.style.display = isLocal ? 'none' : '';
    if (tokenGroup) tokenGroup.style.display = 'none';  // 认证 Token 暂时始终隐藏（端点未校验）
    if (testBtn) testBtn.style.display = isLocal ? 'none' : '';
    if (testResult && isLocal) testResult.textContent = '';
    updateFacePendingUI();
}

// ============ 「待下发」脏检测 ============
// 待下发的橙点 badge 只挂在这几个字段上；启用开关/验证频率是后端即时生效，不挂。
function pendingBadge(key) {
    return `<span class="face-pending-badge" data-pending-badge="${key}" hidden>${tt('facePending', '待下发')}</span>`;
}

// 当前 mode 下"影响设备行为、改了就要下发"的配置字段。
// 本机(local)：模式、阈值(=闸门实际阈值)；端点/Token 本机模式隐藏不计。
// 局域网(lan)：模式、阈值、端点、Token（这些改动出入库闸门后端即时生效，
//              下发只把设备本地识别/唤醒问候同步过去）。
function pendingConfigKeys() {
    const modeEl = document.getElementById('face-config-mode');
    const mode = modeEl ? modeEl.value : 'local';
    const keys = ['mode', 'min_confidence'];
    if (mode === 'lan') keys.push('endpoint');  // auth_token 字段已隐藏，不参与待下发
    return keys;
}

// 从当前表单读出用于比对的值（阈值归一化，避免 "0.7" / "0.70" 误判为改动）。
function faceFormValues() {
    const g = id => document.getElementById(id);
    const conf = parseFloat(g('face-config-min-confidence')?.value);
    return {
        mode: g('face-config-mode')?.value ?? '',
        min_confidence: Number.isFinite(conf) ? String(Math.round(conf * 100) / 100) : '',
        endpoint: (g('face-config-endpoint')?.value ?? '').trim(),
        auth_token: g('face-config-token')?.value ?? '',
    };
}

// 以当前表单值为"已下发基线"。进入配置页、下发成功后各调一次。
function captureFacePushBaseline() {
    facePushBaseline = faceFormValues();
    facePendingLibrary = false;
}

// 重算并渲染：逐字段橙点、顶部汇总、下发按钮高亮。
function updateFacePendingUI() {
    if (!facePushBaseline) return;
    const cur = faceFormValues();
    const keys = pendingConfigKeys();
    const dirty = keys.filter(k => cur[k] !== facePushBaseline[k]);

    // 逐字段 badge（只显示当前 mode 关心的字段；隐藏字段的 badge 一律收起）
    document.querySelectorAll('[data-pending-badge]').forEach(el => {
        const k = el.getAttribute('data-pending-badge');
        el.hidden = !dirty.includes(k);
    });

    const total = dirty.length + (facePendingLibrary ? 1 : 0);
    const summary = document.getElementById('face-pending-summary');
    const summaryText = document.getElementById('face-pending-summary-text');
    if (summary && summaryText) {
        if (total > 0) {
            const parts = [];
            if (dirty.length) parts.push(tt('facePendingConfig', '识别配置'));
            if (facePendingLibrary) parts.push(tt('facePendingLibrary', '人脸库'));
            summaryText.textContent = tt('facePendingSummary', '{items} 有改动待下发到设备')
                .replace('{items}', parts.join(' + '));
            summary.hidden = false;
        } else {
            summary.hidden = true;   // 即时生效 / 已同步 → 不显示任何提示
        }
    }
    const pushBtn = document.querySelector('.face-push-btn');
    if (pushBtn) pushBtn.classList.toggle('has-pending', total > 0);
}

// 人脸库/人员发生增删改 → 标记待下发（下发成功后清除）。
function markFaceLibraryDirty() {
    facePendingLibrary = true;
    updateFacePendingUI();
}

// 总开关关闭时,配置与规则均不生效 → 顶部提醒条随开关显隐(不置灰,仍可编辑配置)
export function onFaceConfigEnabledChange() {
    const box = document.getElementById('face-config-enabled');
    const note = document.getElementById('face-config-disabled-note');
    if (box && note) note.hidden = box.checked;
}

// 识别设置改为失焦/变更自动保存：下拉与开关 change 触发、文本输入 blur 触发。
// saveFaceConfig 保留原有 PUT 字段与行为，只把「保存」反馈从 toast 换成轻量内联提示。
export async function saveFaceConfig() {
    const data = {
        enabled: document.getElementById('face-config-enabled').checked,
        mode: document.getElementById('face-config-mode').value,
        endpoint: document.getElementById('face-config-endpoint').value.trim(),
        auth_token: document.getElementById('face-config-token').value,
        embedding_model_tag: document.getElementById('face-config-model-tag').value.trim(),
        min_confidence: parseFloat(document.getElementById('face-config-min-confidence').value) || 0,
        verify_frequency: document.getElementById('face-config-verify-frequency').value
    };
    try {
        await faceApi.updateConfig(data, effectiveTenantId());
        currentConfig = { ...currentConfig, ...data };
        showSavedHint();
    } catch (error) {
        showToast(getErrorMessage(error, 'faceConfigSaveFailed', '配置保存失败'), 'error');
    }
}

// 轻量「已保存」内联提示：淡入后 1.6s 自动淡出，不打断操作。
function showSavedHint() {
    const el = document.getElementById('face-config-saved-hint');
    if (!el) return;
    el.classList.add('is-visible');
    clearTimeout(el._hintTimer);
    el._hintTimer = setTimeout(() => el.classList.remove('is-visible'), 1600);
}

// 识别设置自动保存监听：在 renderSetupTab 注入 DOM 后调用一次。
// 每次进入 setup 页都是全新节点，监听随节点重建，不会累积。
function attachSetupAutoSave() {
    // 下拉与开关：change 即保存
    ['face-config-mode', 'face-config-verify-frequency', 'face-config-enabled'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('change', () => { saveFaceConfig(); updateFacePendingUI(); });
    });
    // 置信度：blur 时校验并 clamp 到 0.0-1.0，非法值不保存（还原上次有效值）
    const conf = document.getElementById('face-config-min-confidence');
    if (conf) {
        conf.addEventListener('blur', () => {
            let v = parseFloat(conf.value);
            if (!Number.isFinite(v)) {
                conf.value = Number(currentConfig.min_confidence ?? 0.7);
                return;
            }
            v = Math.min(1, Math.max(0, Math.round(v * 100) / 100));
            conf.value = v;
            saveFaceConfig();
            updateFacePendingUI();
        });
    }
    // 文本输入（远端地址 / Token）：blur 即保存
    ['face-config-endpoint', 'face-config-token'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('blur', () => { saveFaceConfig(); updateFacePendingUI(); });
    });
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
    const r = rule || { warehouse_id: null, operation: 'stock_out', require_face: true, allowed_subject_ids: [], min_confidence_override: null };
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
                            ${operations.map(op => `<option value="${op}" ${r.operation === op ? 'selected' : ''}>${escapeHtml(opLabel(op))}</option>`).join('')}
                        </select>
                    </div>
                    <div class="form-group">
                        <label class="face-enable-toggle">
                            <input type="checkbox" id="face-rule-require" ${r.require_face ? 'checked' : ''}>
                            <span>${tt('faceRuleEnabled', '启用此规则')}</span>
                        </label>
                        <div class="form-hint">${tt('faceRuleEnabledHint', '停用后，此仓库 + 操作不做人脸校验（等同于删除这条规则）')}</div>
                    </div>
                    <div id="face-rule-dependent" class="face-rule-dependent">
                    <div class="form-group">
                        <label>${tt('faceAllowedSubjects', '允许人员')}</label>
                        <div id="face-rule-subjects" class="face-enroll-wh-list">
                            ${allSubjects.length === 0 ? `<div class="face-enroll-wh-empty">${tt('faceSubjectsEmpty', '暂无人员，请先到「人员与录入」新增')}</div>` : allSubjects.map(s => `
                                <label class="checkbox-label face-enroll-wh-item">
                                    <input type="checkbox" value="${s.id}" ${(r.allowed_subject_ids || []).includes(s.id) ? 'checked' : ''} ${!s.is_active ? 'disabled' : ''}>
                                    <span>${escapeHtml(s.name)}${s.employee_id ? ` <span class="face-inline-hint">(${escapeHtml(s.employee_id)})</span>` : ''}${!s.is_active ? ` <span class="face-enroll-user-role is-inactive">${tt('disabled', '已停用')}</span>` : ''}</span>
                                </label>
                            `).join('')}
                        </div>
                        <div class="form-hint">${tt('faceAllowedSubjectsHint', '不勾选任何人员表示所有已录入人员都可以通过')}</div>
                    </div>
                    <div class="form-group">
                        <label>${tt('faceMinConfidenceOverride', '自定义阈值')}</label>
                        <input type="number" id="face-rule-confidence" min="0" max="1" step="0.01" value="${r.min_confidence_override == null ? '' : r.min_confidence_override}" placeholder="${tt('faceLeaveBlankInherit', '留空则继承全局')}">
                    </div>
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
    // 规则停用时,"允许人员/自定义阈值"无意义 → 置灰(与录入弹窗同款联动)
    const requireBox = document.getElementById('face-rule-require');
    const dependent = document.getElementById('face-rule-dependent');
    if (requireBox && dependent) {
        const sync = () => dependent.classList.toggle('is-disabled', !requireBox.checked);
        requireBox.addEventListener('change', sync);
        sync();
    }
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
    const allowedIds = Array.from(document.querySelectorAll('#face-rule-subjects input[type="checkbox"]:checked')).map(cb => parseInt(cb.value, 10));
    const errEl = document.getElementById('face-rule-error');

    const data = {
        warehouse_id: whVal ? parseInt(whVal, 10) : null,
        operation: op,
        require_face: requireFace,
        allowed_subject_ids: allowedIds,
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

// ============ 子页签 B: 人员 + 录入（渲染辅助，嵌入 renderSetupTab）============
function renderSubjectItem(s) {
    const name = s.name || `#${s.id}`;
    const sub = s.employee_id ? `${tt('faceSubjectEmployeeId', '工号')}: ${s.employee_id}` : '';
    const inactive = !s.is_active;
    const active = selectedSubjectId === s.id ? 'is-active' : '';
    return `
        <button class="face-enroll-user-item ${active}" data-action="selectFaceSubject" data-subject-id="${s.id}">
            <div class="face-enroll-user-name">${escapeHtml(name)}${inactive ? ` <span class="face-enroll-user-role is-inactive">${tt('disabled', '已停用')}</span>` : ''}</div>
            ${sub ? `<div class="face-enroll-user-sub">${escapeHtml(sub)}</div>` : ''}
            ${typeof s.enrollment_count === 'number' ? `<span class="face-enroll-user-role">${s.enrollment_count} ${tt('faceEnrolledItems', '条')}</span>` : ''}
        </button>
    `;
}

function renderEnrollPlaceholder() {
    return `
        <div class="panel-empty-state">
            <svg class="empty-icon" width="44" height="44" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M16 21v-2a4 4 0 00-4-4H6a4 4 0 00-4 4v2"></path>
                <circle cx="9" cy="7" r="4" stroke-width="1.5"></circle>
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M19 8v6M22 11h-6"></path>
            </svg>
            <div class="empty-message">${tt('faceSelectSubjectHint', '从左侧选择一个人员开始录入人脸')}</div>
        </div>
    `;
}

function renderEnrollDetail() {
    const subject = allSubjects.find(s => s.id === selectedSubjectId);
    if (!subject) return '';
    const count = enrollmentItems.length;
    return `
        <div class="face-enroll-detail-header">
            <div>
                <div class="face-enroll-detail-name">${escapeHtml(subject.name)}</div>
                <div class="face-enroll-detail-meta">
                    ${subject.employee_id ? `${tt('faceSubjectEmployeeId', '工号')}: ${escapeHtml(subject.employee_id)} · ` : ''}
                    ${tt('faceEnrolledCount', '已录入')} <strong>${count}</strong> ${tt('faceEnrolledItems', '条')}
                </div>
            </div>
            <div class="action-buttons">
                <button class="action-btn-small" data-action="showEditFaceSubjectModal" data-subject-id="${subject.id}">${tt('edit', '编辑')}</button>
                <button class="action-btn-small danger" data-action="deleteFaceSubject" data-subject-id="${subject.id}">${tt('delete', '删除')}</button>
                <button class="btn confirm-btn" data-action="showFaceEnrollModal">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:-2px;margin-right:4px;"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
                    ${tt('faceEnrollAdd', '录入新条目')}
                </button>
            </div>
        </div>
        ${count === 0
            ? `<div class="panel-empty-state"><div class="empty-message">${tt('faceEnrollNoItems', '该人员尚未录入人脸')}</div></div>`
            : renderEnrollmentTable()
        }
    `;
}

// 录入条目用紧凑从属表格（.sub-table，与智能体物理设备子表同一组件）：
// 表头浅灰弱化，行分隔线极浅，字号 13px，从属感明显，不再是层级感重的卡片。
function renderEnrollmentTable() {
    return `
        <div style="margin-top:4px;">
            <table class="sub-table">
                <thead><tr>
                    <th style="width:64px;">#</th>
                    <th>${tt('faceAppliesToWarehouses', '生效仓库')}</th>
                    <th style="width:120px;">${tt('faceEnrollCreatedAt', '录入时间')}</th>
                    <th style="width:80px;">${tt('actions', '操作')}</th>
                </tr></thead>
                <tbody>${enrollmentItems.map(renderEnrollmentRow).join('')}</tbody>
            </table>
        </div>
    `;
}

function renderEnrollmentRow(item) {
    const whIds = Array.isArray(item.applies_to_warehouse_ids) ? item.applies_to_warehouse_ids : [];
    const whText = whIds.length === 0
        ? tt('faceAppliesAll', '全部仓库')
        : whIds.map(id => (allWarehouses.find(w => w.id === id) || {}).name || `#${id}`).join(', ');
    // 后端时间可能是 "2026-07-01T08:35:57" 或 "2026-07-01 08:35:57"，只取日期部分
    const created = String(item.enrolled_at || item.created_at || '').slice(0, 10) || '-';
    return `
        <tr>
            <td class="is-mono">#${escapeHtml(item.id)}</td>
            <td>${escapeHtml(whText)}</td>
            <td>${escapeHtml(created)}</td>
            <td class="is-nowrap">
                <button class="action-btn-small danger" data-action="deleteFaceEnrollment" data-enrollment-id="${item.id}">${t('delete') || '删除'}</button>
            </td>
        </tr>
    `;
}

// ============ 人脸库下发（库级动作：整库推到所选设备）============
// 复用后端 push-faces 端点（与智能体配置里的「下发」同一接口、同样行为），
// 差别只是本页多一步「选设备」。设备列表来自新扁平接口 GET /api/mcp/agent-devices。
async function mcpAdminFetch(path, options = {}) {
    const resp = await fetch(`${API_BASE_URL}${path}`, {
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
        ...options,
    });
    if (!resp.ok) {
        const err = new Error(`HTTP ${resp.status}`);
        try { err.data = await resp.json(); } catch {}
        err.status = resp.status;
        throw err;
    }
    return resp.json();
}

export async function showFacePushModal() {
    const modal = document.getElementById('face-push-modal');
    if (!modal) return;
    modal.innerHTML = `
        <div class="modal-content modal-small">
            <div class="modal-header">
                <h3>${tt('facePushTitle', '下发人脸库到设备')}</h3>
                <button class="close-btn" data-action="closeFacePushModal">&times;</button>
            </div>
            <div class="modal-body">
                <div class="form-hint" style="margin-bottom:10px;">${tt('facePushDesc', '将当前租户已录入的整个人脸库下发到所选设备。')}</div>
                <div id="face-push-body">${renderLoading()}</div>
                <div class="form-error" id="face-push-error" hidden></div>
                <div id="face-push-result" style="margin-top:10px;"></div>
            </div>
            <div class="modal-footer">
                <button class="btn cancel-btn" data-action="closeFacePushModal">${t('cancel') || '取消'}</button>
                <button class="btn confirm-btn" id="face-push-submit" data-action="submitFacePush" disabled>${tt('facePushLibrary', '下发')}</button>
            </div>
        </div>
    `;
    modal.classList.add('show');
    const bodyEl = document.getElementById('face-push-body');
    const submitBtn = document.getElementById('face-push-submit');
    try {
        const devices = await mcpAdminFetch('/mcp/agent-devices');
        if (!Array.isArray(devices) || devices.length === 0) {
            bodyEl.innerHTML = `<div class="panel-empty-state"><div class="empty-message">${tt('facePushNoDevices', '暂无设备。请先在「智能体配置」里为智能体添加物理设备。')}</div></div>`;
            return;
        }
        const opts = devices.map(d => {
            const label = [d.name || d.ip || ('#' + d.id), d.ip, d.connection_name].filter(Boolean).join(' · ');
            return `<option value="${d.connection_id}::${d.id}">${escapeHtml(label)}</option>`;
        }).join('');
        bodyEl.innerHTML = `
            <div class="form-group">
                <label>${tt('facePushSelectDevice', '选择目标设备')}</label>
                <select id="face-push-device" class="form-control">${opts}</select>
            </div>
        `;
        if (submitBtn) submitBtn.disabled = false;
    } catch (error) {
        if (bodyEl) bodyEl.innerHTML = '';
        const errEl = document.getElementById('face-push-error');
        if (errEl) { errEl.hidden = false; errEl.textContent = getErrorMessage(error, 'facePushLoadFailed', '加载设备列表失败'); }
    }
}

export function closeFacePushModal() {
    const modal = document.getElementById('face-push-modal');
    if (modal) modal.classList.remove('show');
}

export async function submitFacePush() {
    const select = document.getElementById('face-push-device');
    const submitBtn = document.getElementById('face-push-submit');
    const resultEl = document.getElementById('face-push-result');
    const errEl = document.getElementById('face-push-error');
    if (errEl) errEl.hidden = true;
    if (!select || !select.value) return;
    const [connId, devId] = select.value.split('::');
    const label = select.options[select.selectedIndex]?.textContent || devId;
    const orig = submitBtn ? submitBtn.textContent : '';
    if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = tt('mcpDevicePushing', '下发中…'); }
    if (resultEl) resultEl.innerHTML = '';
    try {
        const result = await mcpAdminFetch(`/mcp/connections/${connId}/devices/${devId}/push-faces`, { method: 'POST' });
        if (result && result.success) {
            const msg = tt('mcpDevicePushSuccess', '已向设备 "{name}" 下发 {count} 条人脸')
                .replace('{name}', label).replace('{count}', result.pushed_count ?? 0);
            if (resultEl) resultEl.innerHTML = `<div class="result-banner success">${escapeHtml(msg)}</div>`;
            showToast(msg);
            // 下发成功 → 以当前配置为新基线，清掉所有待下发标记。
            captureFacePushBaseline();
            updateFacePendingUI();
        } else {
            const msg = tt('mcpDevicePushFailed', '向设备 "{name}" 下发失败：{error}')
                .replace('{name}', label).replace('{error}', (result && result.error) || tt('operationFailed', '操作失败'));
            if (resultEl) resultEl.innerHTML = `<div class="result-banner error">${escapeHtml(msg)}</div>`;
        }
    } catch (error) {
        const msg = tt('mcpDevicePushFailed', '向设备 "{name}" 下发失败：{error}')
            .replace('{name}', label).replace('{error}', getErrorMessage(error, 'operationFailed', '操作失败'));
        if (resultEl) resultEl.innerHTML = `<div class="result-banner error">${escapeHtml(msg)}</div>`;
    } finally {
        if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = orig; }
    }
}

export async function selectFaceSubject(el) {
    selectedSubjectId = parseInt(el.dataset.subjectId, 10);
    const detail = document.getElementById('face-enroll-detail');
    if (detail) detail.innerHTML = renderLoading();
    document.querySelectorAll('#face-enroll-subject-list [data-action="selectFaceSubject"]').forEach(btn => {
        const id = parseInt(btn.dataset.subjectId, 10);
        btn.classList.toggle('is-active', id === selectedSubjectId);
    });
    await loadEnrollmentsForSelected();
}

async function loadEnrollmentsForSelected() {
    if (!selectedSubjectId) {
        enrollmentItems = [];
        return;
    }
    // 守卫竞态：快速连点两个人员时，先发出的慢请求返回后不得覆盖后选中人员的数据
    const requestedSubjectId = selectedSubjectId;
    let items;
    try {
        const result = await faceApi.getEnrollments({ subjectId: requestedSubjectId, tenantId: effectiveTenantId() });
        items = Array.isArray(result) ? result : (result.items || []);
    } catch {
        items = [];
    }
    if (selectedSubjectId !== requestedSubjectId) return;  // stale response, discard
    enrollmentItems = items;
    const detail = document.getElementById('face-enroll-detail');
    if (detail) detail.innerHTML = renderEnrollDetail();
}

export function showFaceEnrollModal() {
    const modal = document.getElementById('face-enroll-modal');
    if (!modal || !selectedSubjectId) return;
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
                        ${isFaceLocalMode() ? `<div class="form-hint" style="color:#b25b00;">${tt('faceEnrollLocalQuota', '本机模式：设备最多存 {max} 张图片（是 {max} 张图，不是 {max} 人），当前 {total} 张，可再录 {remaining} 张。')
                            .replaceAll('{max}', FACE_LOCAL_MAX_ENROLLMENTS)
                            .replace('{total}', faceTotalEnrollments())
                            .replace('{remaining}', Math.max(0, FACE_LOCAL_MAX_ENROLLMENTS - faceTotalEnrollments()))}</div>` : ''}
                    </div>
                    <div class="form-group">
                        <label>${tt('faceAppliesToWarehouses', '生效仓库')}</label>
                        <label class="checkbox-label face-enroll-all-row">
                            <input type="checkbox" id="face-enroll-all-wh" checked>
                            <span>${tt('faceAppliesAll', '全部仓库')}</span>
                            <span class="face-enroll-all-hint">${tt('faceAppliesAllHint', '取消勾选可单独指定下方仓库')}</span>
                        </label>
                        <div id="face-enroll-wh-list" class="face-enroll-wh-list is-disabled">
                            ${allWarehouses.length === 0
                                ? `<div class="face-enroll-wh-empty">${t('noData')}</div>`
                                : allWarehouses.map(w => `
                                    <label class="checkbox-label face-enroll-wh-item">
                                        <input type="checkbox" value="${w.id}" disabled>
                                        <span>${escapeHtml(w.name)}</span>
                                    </label>
                                `).join('')
                            }
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
        const sync = () => {
            list.classList.toggle('is-disabled', allBox.checked);
            list.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                cb.disabled = allBox.checked;
                if (allBox.checked) cb.checked = false;
            });
        };
        allBox.addEventListener('change', sync);
        sync();
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
    if (!selectedSubjectId) return;
    if (files.length === 0) {
        if (errEl) { errEl.hidden = false; errEl.textContent = tt('faceEnrollImagesRequired', '请至少选择一张图片'); }
        return;
    }
    // 本机模式设备端人脸库上限：20 张图片（不是 20 人）。录入前拦，避免超限、下发才失败。
    if (isFaceLocalMode()) {
        const total = faceTotalEnrollments();
        const remaining = FACE_LOCAL_MAX_ENROLLMENTS - total;
        if (files.length > remaining) {
            if (errEl) {
                errEl.hidden = false;
                errEl.textContent = tt('faceEnrollLocalLimit',
                    '本机模式设备最多存 {max} 张人脸图片（是 {max} 张图片，不是 {max} 个人）。'
                    + '当前已录入 {total} 张，仅剩 {remaining} 张，本次选了 {picked} 张，超出。'
                    + '请减少本次数量，或先删除已有录入。')
                    .replaceAll('{max}', FACE_LOCAL_MAX_ENROLLMENTS)
                    .replace('{total}', total)
                    .replace('{remaining}', Math.max(0, remaining))
                    .replace('{picked}', files.length);
            }
            return;
        }
    }
    let warehouseIds = [];
    if (!allBox.checked) {
        warehouseIds = Array.from(document.querySelectorAll('#face-enroll-wh-list input[type="checkbox"]:checked')).map(cb => parseInt(cb.value, 10));
    }
    try {
        const images_b64 = await Promise.all(files.map(readFileAsBase64));
        const payload = {
            subject_id: selectedSubjectId,
            images_b64,
            applies_to_warehouse_ids: warehouseIds
        };
        await faceApi.createEnrollment(payload, effectiveTenantId());
        showToast(tt('faceEnrollSuccess', '录入成功'));
        closeFaceEnrollModal();
        // 人员计数徽章与右侧详情互相独立，可并行刷新
        await Promise.all([refreshSubjectList(), loadEnrollmentsForSelected()]);
        markFaceLibraryDirty();
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
        await Promise.all([refreshSubjectList(), loadEnrollmentsForSelected()]);
        markFaceLibraryDirty();
    } catch (error) {
        showToast(getErrorMessage(error, 'faceEnrollDeleteFailed', '删除失败'), 'error');
    }
}

// ============ 子页签 C: 审计日志 ============
// 日志页 = 静态筛选栏 + 动态主体（#face-logs-main）。筛选栏只在进入页签时渲染
// 一次：移动端抽屉增强（initFilterDrawers）挂在这些节点上，反复重建会丢监听、
// 丢输入焦点；翻页/筛选只需刷新表格与分页。
function renderLogsTab() {
    const f = logsState.filters;
    return `
        <div class="filter-bar">
            <div class="filter-group">
                <label>${tt('operation', '操作')}</label>
                <select id="face-logs-operation">
                    <option value="">${tt('all', '全部')}</option>
                    ${FACE_OPERATIONS.map(op => `<option value="${op}" ${f.operation === op ? 'selected' : ''}>${escapeHtml(opLabel(op))}</option>`).join('')}
                </select>
            </div>
            <div class="filter-group">
                <label>${tt('startDate', '开始日期')}</label>
                <input type="date" id="face-logs-start" value="${escapeHtml(f.start || '')}">
            </div>
            <div class="filter-group">
                <label>${tt('endDate', '结束日期')}</label>
                <input type="date" id="face-logs-end" value="${escapeHtml(f.end || '')}">
            </div>
            <div class="filter-actions">
                <button class="filter-btn primary" data-action="applyFaceLogsFilter">${tt('apply', '应用')}</button>
                <button class="filter-btn secondary" data-action="resetFaceLogsFilter">${tt('reset', '重置')}</button>
            </div>
        </div>
        <div id="face-logs-main">${renderLogsMain()}</div>
    `;
}

function renderLogsMain() {
    return `
        <div class="table-container">
            <div class="section-header">
                <div class="section-title">${tt('faceLogs', '审计日志')}</div>
            </div>
            <table id="face-logs-table">
                <thead>
                    <tr>
                        <th>${tt('faceLogTime', '时间')}</th>
                        <th>${tt('faceLogCaller', '调用方')}</th>
                        <th>${tt('operation', '操作')}</th>
                        <th>${tt('faceLogMatchedSubject', '匹配人员')}</th>
                        <th>${tt('faceLogConfidence', '置信度')}</th>
                        <th>${tt('faceLogDecision', '判定')}</th>
                        <th>${tt('faceLogReason', '原因')}</th>
                    </tr>
                </thead>
                <tbody id="face-logs-tbody">${renderLogsRows()}</tbody>
            </table>
            <div class="pagination">
                <div>
                    <span>${t('totalRecords') || '共'}</span>
                    <span id="face-logs-total">${logsState.total}</span>
                    <span>${t('recordsUnit') || '条记录'}</span>
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
        return `<tr><td colspan="7" class="table-empty-cell">${t('noData')}</td></tr>`;
    }
    return logsState.items.map(item => {
        const matched = item.matched_subject_id ? (allSubjects.find(s => s.id === item.matched_subject_id) || {}) : null;
        const matchedName = matched && matched.name
            ? matched.name
            : (item.matched_subject_id ? `#${item.matched_subject_id}` : '-');
        const callerText = item.user_id ? `#${item.user_id}` : '-';
        const decisionKey = `decision_${item.decision || 'skipped'}`;
        const decisionText = tt(decisionKey, item.decision || '-');
        const decisionClass = item.decision === 'pass' ? 'status-normal' : (item.decision === 'deny' ? 'status-disabled' : '');
        return `
            <tr>
                <td>${escapeHtml((item.created_at || '').replace('T', ' ').slice(0, 19))}</td>
                <td>${escapeHtml(callerText)}</td>
                <td>${escapeHtml(opLabel(item.operation))}</td>
                <td>${escapeHtml(matchedName)}</td>
                <td>${item.confidence == null ? '-' : escapeHtml(Number(item.confidence).toFixed(3))}</td>
                <td><span class="status-badge ${decisionClass}">${escapeHtml(decisionText)}</span></td>
                <td>${escapeHtml(item.failure_reason || '-')}</td>
            </tr>
        `;
    }).join('');
}

// 拉取当前筛选/页码的日志到 logsState，然后重渲表格+分页主体
// （分页按钮的 disabled 态、页码指示都依赖 logsState，只 patch tbody 会漏掉它们）。
async function reloadLogs() {
    try {
        const f = logsState.filters;
        const result = await faceApi.getLogs({
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
    const main = document.getElementById('face-logs-main');
    if (main) main.innerHTML = renderLogsMain();
}

export async function applyFaceLogsFilter() {
    logsState.filters = {
        operation: document.getElementById('face-logs-operation').value,
        start: document.getElementById('face-logs-start').value,
        end: document.getElementById('face-logs-end').value
    };
    logsState.page = 1;
    await reloadLogs();
}

export async function resetFaceLogsFilter() {
    logsState.filters = emptyLogsFilters();
    logsState.page = 1;
    // 筛选栏是静态的，输入框需要显式清空
    ['face-logs-operation', 'face-logs-start', 'face-logs-end'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
    });
    await reloadLogs();
}

export async function faceLogsPrevPage() {
    if (logsState.page > 1) {
        logsState.page--;
        await reloadLogs();
    }
}

export async function faceLogsNextPage() {
    const totalPages = Math.max(1, Math.ceil((logsState.total || 0) / logsState.pageSize));
    if (logsState.page < totalPages) {
        logsState.page++;
        await reloadLogs();
    }
}

// ============ 人员档案 CRUD ============
export function showAddFaceSubjectModal() {
    openSubjectModal(null);
}

export function showEditFaceSubjectModal(el) {
    const sid = parseInt(el.dataset.subjectId, 10);
    const subject = allSubjects.find(s => s.id === sid);
    if (!subject) return;
    openSubjectModal(subject);
}

function openSubjectModal(subject) {
    const modal = document.getElementById('face-subject-modal');
    if (!modal) return;
    const isEdit = !!subject;
    const s = subject || { name: '', employee_id: '', note: '', is_active: true };
    modal.innerHTML = `
        <div class="modal-content modal-small">
            <div class="modal-header">
                <h3>${isEdit ? tt('faceSubjectEdit', '编辑人员') : tt('faceSubjectAdd', '新增人员')}</h3>
                <button class="close-btn" data-action="closeFaceSubjectModal">&times;</button>
            </div>
            <div class="modal-body">
                <form id="face-subject-form">
                    <input type="hidden" id="face-subject-id" value="${isEdit ? s.id : ''}">
                    <div class="form-group">
                        <label>${tt('faceSubjectName', '姓名')} <span class="required">*</span></label>
                        <input type="text" id="face-subject-name" value="${escapeHtml(s.name || '')}" maxlength="100" placeholder="${tt('faceSubjectNamePlaceholder', '如：张三')}">
                    </div>
                    <div class="form-group">
                        <label>${tt('faceSubjectEmployeeId', '工号')} <span class="form-hint" style="display:inline;font-weight:normal;">(${tt('optional', '可选')})</span></label>
                        <input type="text" id="face-subject-employee-id" value="${escapeHtml(s.employee_id || '')}" maxlength="50">
                    </div>
                    <div class="form-group">
                        <label>${tt('faceSubjectNote', '备注')} <span class="form-hint" style="display:inline;font-weight:normal;">(${tt('optional', '可选')})</span></label>
                        <input type="text" id="face-subject-note" value="${escapeHtml(s.note || '')}" maxlength="200">
                    </div>
                    <div class="form-group">
                        <label class="face-enable-toggle">
                            <input type="checkbox" id="face-subject-active" ${s.is_active !== false ? 'checked' : ''}>
                            <span>${tt('faceSubjectActive', '启用')}</span>
                        </label>
                        <div class="form-hint">${tt('faceSubjectActiveHint', '停用后该人员的录入将不参与识别')}</div>
                    </div>
                    <div class="form-error" id="face-subject-error" hidden></div>
                </form>
            </div>
            <div class="modal-footer">
                <button class="btn cancel-btn" data-action="closeFaceSubjectModal">${t('cancel') || '取消'}</button>
                <button class="btn confirm-btn" data-action="saveFaceSubject">${t('submit') || '提交'}</button>
            </div>
        </div>
    `;
    modal.classList.add('show');
}

export function closeFaceSubjectModal() {
    const modal = document.getElementById('face-subject-modal');
    if (modal) modal.classList.remove('show');
}

export async function saveFaceSubject() {
    const idVal = document.getElementById('face-subject-id').value;
    const name = document.getElementById('face-subject-name').value.trim();
    const employeeId = document.getElementById('face-subject-employee-id').value.trim();
    const note = document.getElementById('face-subject-note').value.trim();
    const isActive = document.getElementById('face-subject-active').checked;
    const errEl = document.getElementById('face-subject-error');
    if (!name) {
        if (errEl) { errEl.hidden = false; errEl.textContent = tt('faceSubjectNameRequired', '姓名不能为空'); }
        return;
    }
    const data = {
        name,
        employee_id: employeeId || null,
        note: note || null,
        is_active: isActive,
    };
    try {
        const tid = effectiveTenantId();
        if (idVal) {
            await faceApi.updateSubject(parseInt(idVal, 10), data, tid);
        } else {
            await faceApi.createSubject(data, tid);
        }
        closeFaceSubjectModal();
        showToast(tt('faceSubjectSaved', '人员档案已保存'));
        markFaceLibraryDirty();
        await refreshSubjectList();
        // refresh detail if currently viewing the edited subject
        if (idVal && parseInt(idVal, 10) === selectedSubjectId) {
            const detail = document.getElementById('face-enroll-detail');
            if (detail) detail.innerHTML = renderEnrollDetail();
        }
    } catch (error) {
        if (errEl) { errEl.hidden = false; errEl.textContent = getErrorMessage(error, 'faceSubjectSaveFailed', '保存失败'); }
    }
}

export async function deleteFaceSubject(el) {
    const sid = parseInt(el.dataset.subjectId, 10);
    const subject = allSubjects.find(s => s.id === sid);
    const name = subject ? subject.name : `#${sid}`;
    if (!confirm(`${tt('faceSubjectDeleteConfirm', '删除该人员将一并删除其所有录入记录，确定继续？')}\n\n${name}`)) return;
    try {
        await faceApi.deleteSubject(sid, effectiveTenantId());
        showToast(tt('faceSubjectDeleted', '已删除'));
        markFaceLibraryDirty();
        if (selectedSubjectId === sid) {
            selectedSubjectId = null;
            enrollmentItems = [];
        }
        await refreshSubjectList();
        const detail = document.getElementById('face-enroll-detail');
        if (detail) detail.innerHTML = renderEnrollPlaceholder();
    } catch (error) {
        showToast(getErrorMessage(error, 'faceSubjectDeleteFailed', '删除失败'), 'error');
    }
}


// ============ 模态容器 ============
export function getFaceModalsHTML() {
    return `
        <div id="face-rule-modal" class="modal"></div>
        <div id="face-enroll-modal" class="modal"></div>
        <div id="face-subject-modal" class="modal"></div>
        <div id="face-push-modal" class="modal"></div>
    `;
}
