// ============ ERP 系统模式管理模块 ============
import { t } from '../../../i18n.js';
import { API_BASE_URL } from '../state.js';

// API 封装
async function erpFetch(url, options = {}) {
    const response = await fetch(`${API_BASE_URL}${url}`, {
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', ...options.headers },
        ...options
    });
    if (!response.ok) {
        const error = new Error(`HTTP ${response.status}`);
        try { error.data = await response.json(); } catch {}
        throw error;
    }
    return response.json();
}

// 状态
let providers = [];
let currentMode = 'self_owned';
let refreshInterval = null;
let currentWizardStep = 1;
let uploadedProviderId = null;
let wizardProviderName = '';

function escapeHtml(str) {
    if (str == null) return '';
    const div = document.createElement('div');
    div.textContent = String(str);
    return div.innerHTML;
}

// ============ 主加载函数 ============
export async function loadERPStatus() {
    try {
        const [modeData, providersData] = await Promise.all([
            erpFetch('/system/mode'),
            erpFetch('/erp/providers')
        ]);
        currentMode = modeData.mode || 'self_owned';
        providers = providersData.providers || [];
    } catch (error) {
        console.error('加载 ERP 状态失败:', error);
        providers = [];
    }

    renderModeCards();
    renderProvidersTable();
    renderStatusDashboard();
}

// ============ 自动刷新 ============
export function startERPRefresh() {
    stopERPRefresh();
    refreshInterval = setInterval(() => {
        renderStatusDashboard();
    }, 10000);
}

export function stopERPRefresh() {
    if (refreshInterval) {
        clearInterval(refreshInterval);
        refreshInterval = null;
    }
}

// ============ 渲染：模式切换卡片 ============
function renderModeCards() {
    const container = document.getElementById('erp-mode-cards');
    if (!container) return;

    const selfActive = currentMode === 'self_owned';
    const erpActive = currentMode === 'external_erp';

    container.innerHTML = `
        <div class="section-header">
            <div class="section-title" data-i18n="erpCurrentMode">${t('erpCurrentMode')}</div>
        </div>
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px; padding: 16px;">
            <div class="mode-card ${selfActive ? 'mode-card-active' : 'mode-card-inactive'}"
                 data-action="switchToSelfOwned"
                 style="padding: 20px; border-radius: 8px; cursor: pointer; border: 2px solid ${selfActive ? 'var(--primary-color, #3b82f6)' : 'var(--border-color, #e5e7eb)'}; background: ${selfActive ? 'var(--primary-light, #eff6ff)' : 'var(--card-bg, #fff)'}; transition: all 0.2s;">
                <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 8px;">
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="${selfActive ? 'var(--primary-color, #3b82f6)' : 'var(--text-muted, #9ca3af)'}" stroke-width="2">
                        <rect x="2" y="3" width="20" height="14" rx="2"></rect>
                        <line x1="8" y1="21" x2="16" y2="21"></line>
                        <line x1="12" y1="17" x2="12" y2="21"></line>
                    </svg>
                    <strong style="font-size: 15px; color: ${selfActive ? 'var(--primary-color, #3b82f6)' : 'var(--text-primary, #1f2937)'};">${t('erpSelfOwned')}</strong>
                    ${selfActive ? '<span style="margin-left: auto; font-size: 11px; background: var(--primary-color, #3b82f6); color: #fff; padding: 2px 8px; border-radius: 10px;">✓ 当前</span>' : ''}
                </div>
                <p style="font-size: 13px; color: var(--text-muted, #6b7280); margin: 0;">${t('erpSelfOwnedDesc')}</p>
            </div>
            <div class="mode-card ${erpActive ? 'mode-card-active' : 'mode-card-inactive'}"
                 data-action="switchToERP"
                 style="padding: 20px; border-radius: 8px; cursor: pointer; border: 2px solid ${erpActive ? 'var(--primary-color, #3b82f6)' : 'var(--border-color, #e5e7eb)'}; background: ${erpActive ? 'var(--primary-light, #eff6ff)' : 'var(--card-bg, #fff)'}; transition: all 0.2s;">
                <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 8px;">
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="${erpActive ? 'var(--primary-color, #3b82f6)' : 'var(--text-muted, #9ca3af)'}" stroke-width="2">
                        <path d="M4 7V4a2 2 0 0 1 2-2h8.5L20 7.5V20a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-3"></path>
                        <polyline points="14 2 14 8 20 8"></polyline>
                        <path d="M4 12h12"></path>
                        <path d="M4 18h4"></path>
                    </svg>
                    <strong style="font-size: 15px; color: ${erpActive ? 'var(--primary-color, #3b82f6)' : 'var(--text-primary, #1f2937)'};">${t('erpExternal')}</strong>
                    ${erpActive ? '<span style="margin-left: auto; font-size: 11px; background: var(--primary-color, #3b82f6); color: #fff; padding: 2px 8px; border-radius: 10px;">✓ 当前</span>' : ''}
                </div>
                <p style="font-size: 13px; color: var(--text-muted, #6b7280); margin: 0;">${t('erpExternalDesc')}</p>
            </div>
        </div>
    `;
}

// ============ 渲染：Provider 表格 ============
function renderProvidersTable() {
    const tbody = document.getElementById('erp-providers-tbody');
    if (!tbody) return;

    if (!providers || providers.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" class="text-center" style="padding: 24px; color: var(--text-muted, #9ca3af);">${t('erpNoProviders')}</td></tr>`;
        return;
    }

    tbody.innerHTML = providers.map(p => {
        const testStatusBadge = getTestStatusBadge(getProviderTestStatus(p));
        const activeBadge = p.is_active
            ? `<span style="background: #dcfce7; color: #16a34a; padding: 2px 8px; border-radius: 10px; font-size: 12px;">${t('erpActive')}</span>`
            : `<span style="background: #f3f4f6; color: #6b7280; padding: 2px 8px; border-radius: 10px; font-size: 12px;">${t('erpInactive')}</span>`;

        const actions = getProviderActions(p);

        return `
            <tr>
                <td><div class="font-medium">${escapeHtml(p.name)}</div></td>
                <td><code style="font-size: 12px; background: var(--code-bg, #f3f4f6); padding: 2px 6px; border-radius: 4px;">${escapeHtml(p.provider_name)}</code></td>
                <td style="font-size: 12px; color: var(--text-muted, #6b7280); max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                    ${escapeHtml(p.config?.api_base_url || '-')}
                </td>
                <td>${testStatusBadge}</td>
                <td>${activeBadge}</td>
                <td>${actions}</td>
            </tr>
        `;
    }).join('');
}

function getProviderTestStatus(p) {
    if (!p.test_results) return 'not_run';
    const l1 = p.test_results.level1;
    if (!l1) return 'not_run';
    return l1.all_passed ? 'passed' : 'failed';
}

function getTestResultsSummary(p) {
    if (!p.test_results) return '';
    const l1 = p.test_results.level1;
    if (!l1 || !l1.results) return '';
    const results = l1.results;
    const total = Object.keys(results).length;
    const passed = Object.values(results).filter(r => r.passed).length;
    return `${passed}/${total}`;
}

function getAllTestMethods(p) {
    // Merge L1 and L2 test results for status dashboard
    if (!p.test_results) return {};
    const merged = {};
    for (const level of ['level1', 'level2']) {
        const lr = p.test_results[level];
        if (lr && lr.results) {
            Object.assign(merged, lr.results);
        }
    }
    return merged;
}

function getTestStatusBadge(status) {
    switch (status) {
        case 'passed':
            return `<span style="background: #dcfce7; color: #16a34a; padding: 2px 8px; border-radius: 10px; font-size: 12px;">${t('erpTestPassed')}</span>`;
        case 'failed':
            return `<span style="background: #fee2e2; color: #dc2626; padding: 2px 8px; border-radius: 10px; font-size: 12px;">${t('erpTestFailed')}</span>`;
        default:
            return `<span style="background: #f3f4f6; color: #6b7280; padding: 2px 8px; border-radius: 10px; font-size: 12px;">${t('erpTestNotRun')}</span>`;
    }
}

function getProviderActions(p) {
    const actions = [];
    const id = p.id;

    if (p.is_active) {
        actions.push(`<button class="action-btn" data-action="erpDeactivate" data-provider-id="${id}">${t('erpDeactivate')}</button>`);
    } else {
        actions.push(`<button class="action-btn add-btn" data-action="erpActivate" data-provider-id="${id}">${t('erpActivate')}</button>`);
    }

    actions.push(`<button class="action-btn" data-action="erpEdit" data-provider-id="${id}">${t('edit')}</button>`);
    actions.push(`<button class="action-btn" data-action="erpRunTest1" data-provider-id="${id}">${t('erpTestLevel1')}</button>`);
    actions.push(`<button class="action-btn delete-btn" data-action="erpDelete" data-provider-id="${id}">${t('delete')}</button>`);

    return `<div class="flex gap-2 flex-wrap">${actions.join('')}</div>`;
}

// ============ 渲染：状态看板 ============
async function renderStatusDashboard() {
    const activeProvider = providers.find(p => p.is_active);
    const dashboard = document.getElementById('erp-status-dashboard');
    if (!dashboard) return;

    if (!activeProvider) {
        dashboard.style.display = 'none';
        return;
    }

    dashboard.style.display = '';

    try {
        const statusData = await erpFetch(`/erp/providers/${activeProvider.id}/status`);
        const grid = document.getElementById('erp-status-grid');
        const footer = document.getElementById('erp-status-footer');

        if (grid) {
            const ok = statusData.online === true;
            // Show per-method results from last test if available, plus live connectivity
            const testResults = getAllTestMethods(activeProvider);
            const methods = Object.entries(testResults);

            if (methods.length > 0) {
                grid.innerHTML = methods.map(([method, r]) => {
                    const methodOk = r.passed;
                    return `
                        <div style="padding: 12px; border-radius: 8px; border: 1px solid ${methodOk ? '#bbf7d0' : '#fecaca'}; background: ${methodOk ? '#f0fdf4' : '#fef2f2'};">
                            <div style="font-size: 12px; font-weight: 600; color: var(--text-muted, #6b7280); margin-bottom: 4px;">${escapeHtml(method)}</div>
                            <div style="display: flex; align-items: center; justify-content: space-between;">
                                <span style="font-size: 13px; font-weight: 600; color: ${methodOk ? '#16a34a' : '#dc2626'};">
                                    ${methodOk ? '✓ ' + t('erpNormal') : '✗ ' + t('erpAbnormal')}
                                </span>
                                ${r.latency_ms != null ? `<span style="font-size: 11px; color: var(--text-muted, #9ca3af);">${r.latency_ms}ms</span>` : ''}
                            </div>
                        </div>
                    `;
                }).join('');
            } else {
                // No test results yet — show overall connectivity
                grid.innerHTML = `
                    <div style="padding: 12px; border-radius: 8px; border: 1px solid ${ok ? '#bbf7d0' : '#fecaca'}; background: ${ok ? '#f0fdf4' : '#fef2f2'}; grid-column: 1 / -1;">
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <span style="font-size: 14px; font-weight: 600; color: ${ok ? '#16a34a' : '#dc2626'};">
                                ${t('erpServerStatus')}: ${ok ? t('erpNormal') : t('erpAbnormal')}
                            </span>
                            ${statusData.latency_ms != null ? `<span style="font-size: 12px; color: var(--text-muted, #9ca3af);">${statusData.latency_ms}ms</span>` : ''}
                        </div>
                    </div>
                `;
            }
        }

        if (footer) {
            const connectStatus = statusData.online ? t('erpNormal') : t('erpAbnormal');
            footer.textContent = `${t('erpLastCheck')}: ${new Date().toLocaleTimeString()} · ${t('erpServerStatus')}: ${connectStatus}`;
        }
    } catch (error) {
        console.error('获取 ERP 状态失败:', error);
    }
}

// ============ 模式切换 ============
export async function switchSystemMode(mode) {
    if (!confirm(t('erpConfirmModeSwitch'))) return;
    try {
        await erpFetch('/system/mode', {
            method: 'PUT',
            body: JSON.stringify({ mode })
        });
        await loadERPStatus();
    } catch (error) {
        console.error('切换系统模式失败:', error);
        alert(error.data?.error || t('operationFailed'));
    }
}

// ============ Provider 操作 ============
export async function activateProvider(providerId) {
    try {
        await erpFetch(`/erp/providers/${providerId}/activate`, { method: 'POST' });
        await loadERPStatus();
    } catch (error) {
        console.error('激活 Provider 失败:', error);
        alert(error.data?.error || t('operationFailed'));
    }
}

export async function deactivateProvider(providerId) {
    try {
        await erpFetch(`/erp/providers/${providerId}/deactivate`, { method: 'POST' });
        await loadERPStatus();
    } catch (error) {
        console.error('停用 Provider 失败:', error);
        alert(error.data?.error || t('operationFailed'));
    }
}

export async function deleteProvider(providerId) {
    const provider = providers.find(p => p.id === providerId);
    const name = provider ? provider.name : providerId;
    if (!confirm(t('erpConfirmDelete').replace('{name}', name))) return;

    try {
        await erpFetch(`/erp/providers/${providerId}`, { method: 'DELETE' });
        await loadERPStatus();
    } catch (error) {
        console.error('删除 Provider 失败:', error);
        alert(error.data?.error || t('operationFailed'));
    }
}

export function editProviderConfig(providerId) {
    const provider = providers.find(p => p.id === providerId);
    if (!provider) return;

    uploadedProviderId = providerId;
    wizardProviderName = provider.name;

    // Pre-fill step 2 form and go directly to step 2
    const modal = document.getElementById('erp-upload-modal');
    if (!modal) return;

    currentWizardStep = 2;
    showWizardStep(2);

    // Fill form fields
    const cfg = provider.config || {};
    const auth = cfg.auth || {};
    let authValue = '';
    if (auth.type === 'api_key') authValue = auth.key || '';
    else if (auth.type === 'bearer') authValue = auth.token || '';
    else if (auth.type === 'basic') authValue = `${auth.username || ''}:${auth.password || ''}`;

    setVal('erp-config-name', provider.name);
    setVal('erp-config-url', cfg.api_base_url || '');
    setVal('erp-config-auth-type', auth.type || 'api_key');
    setVal('erp-config-auth-value', authValue);
    setVal('erp-config-timeout', cfg.timeout || 10);

    modal.classList.add('show');
}

function setVal(id, value) {
    const el = document.getElementById(id);
    if (el) el.value = value;
}

// ============ 上传向导 ============
export function showUploadWizard() {
    const modal = document.getElementById('erp-upload-modal');
    if (!modal) return;

    currentWizardStep = 1;
    uploadedProviderId = null;
    wizardProviderName = '';

    // Reset step 1
    const fileInput = document.getElementById('erp-provider-file');
    if (fileInput) fileInput.value = '';
    const fileLabel = document.getElementById('erp-file-name-label');
    if (fileLabel) fileLabel.textContent = '';
    const uploadErr = document.getElementById('erp-upload-error');
    if (uploadErr) { uploadErr.textContent = ''; uploadErr.style.display = 'none'; }

    showWizardStep(1);
    modal.classList.add('show');
}

export function closeUploadWizard() {
    const modal = document.getElementById('erp-upload-modal');
    if (modal) modal.classList.remove('show');
}

function showWizardStep(step) {
    for (let i = 1; i <= 5; i++) {
        const el = document.getElementById(`erp-wizard-step-${i}`);
        if (el) el.style.display = i === step ? '' : 'none';
    }

    // Update step indicator
    for (let i = 1; i <= 5; i++) {
        const dot = document.getElementById(`erp-step-dot-${i}`);
        if (dot) {
            dot.style.background = i === step ? 'var(--primary-color, #3b82f6)' : (i < step ? '#22c55e' : 'var(--border-color, #d1d5db)');
            dot.style.color = (i === step || i < step) ? '#fff' : 'var(--text-muted, #6b7280)';
        }
    }
}

export function wizardNextStep() {
    if (currentWizardStep < 5) {
        currentWizardStep++;
        showWizardStep(currentWizardStep);
    }
}

export function wizardPrevStep() {
    if (currentWizardStep > 1) {
        currentWizardStep--;
        showWizardStep(currentWizardStep);
    }
}

// Step 1: Upload file
export async function handleProviderUpload() {
    const fileInput = document.getElementById('erp-provider-file');
    const errorDiv = document.getElementById('erp-upload-error');

    if (!fileInput || !fileInput.files || fileInput.files.length === 0) {
        if (errorDiv) { errorDiv.textContent = t('fillAllFields'); errorDiv.style.display = 'block'; }
        return;
    }

    const file = fileInput.files[0];
    const formData = new FormData();
    formData.append('file', file);

    try {
        const response = await fetch(`${API_BASE_URL}/erp/providers`, {
            method: 'POST',
            credentials: 'include',
            body: formData
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            const errMsg = typeof err.error === 'string' ? err.error : (err.error?.message || t('erpValidationFailed'));
            if (errorDiv) { errorDiv.textContent = errMsg; errorDiv.style.display = 'block'; }
            return;
        }
        const data = await response.json();
        uploadedProviderId = data.id;
        wizardProviderName = data.provider_name || '';

        // Show validation result
        const valResult = document.getElementById('erp-validation-result');
        if (valResult) {
            valResult.innerHTML = `<div style="color: #16a34a; font-weight: 600;">✓ ${t('erpValidationPassed')}</div>
                <div style="font-size: 12px; color: var(--text-muted, #6b7280); margin-top: 4px;">
                    Provider: <strong>${escapeHtml(data.provider_name)}</strong> (${escapeHtml(data.class_name)})<br>
                    ${t('erpTestStatus')}: ${(data.methods || []).length} methods
                </div>`;
        }

        // Pre-fill config form with defaults
        setVal('erp-config-name', data.provider_name || '');
        setVal('erp-config-url', '');
        setVal('erp-config-auth-type', 'api_key');
        setVal('erp-config-auth-value', '');
        setVal('erp-config-timeout', 10);

        currentWizardStep = 2;
        showWizardStep(2);
        if (errorDiv) errorDiv.style.display = 'none';
    } catch (error) {
        console.error('上传 Provider 失败:', error);
        if (errorDiv) { errorDiv.textContent = t('operationFailed'); errorDiv.style.display = 'block'; }
    }
}

// Step 2: Save config
export async function saveProviderConfig() {
    if (!uploadedProviderId) return;

    const name = document.getElementById('erp-config-name')?.value?.trim();
    const api_base_url = document.getElementById('erp-config-url')?.value?.trim();
    const auth_type = document.getElementById('erp-config-auth-type')?.value;
    const auth_credentials = document.getElementById('erp-config-auth-value')?.value?.trim();
    const timeout = parseInt(document.getElementById('erp-config-timeout')?.value) || 30;
    const configErr = document.getElementById('erp-config-error');

    if (!name || !api_base_url) {
        if (configErr) { configErr.textContent = t('fillAllFields'); configErr.style.display = 'block'; }
        return;
    }

    // Build auth config matching BaseProvider's expected format
    const auth = {};
    if (auth_type === 'api_key') {
        auth.type = 'api_key';
        auth.key = auth_credentials;
    } else if (auth_type === 'bearer') {
        auth.type = 'bearer';
        auth.token = auth_credentials;
    } else if (auth_type === 'basic') {
        auth.type = 'basic';
        // Expect format "username:password"
        const parts = (auth_credentials || '').split(':');
        auth.username = parts[0] || '';
        auth.password = parts.slice(1).join(':') || '';
    }

    try {
        await erpFetch(`/erp/providers/${uploadedProviderId}`, {
            method: 'PUT',
            body: JSON.stringify({ name, config: { api_base_url, auth, timeout } })
        });
        wizardProviderName = name;
        if (configErr) configErr.style.display = 'none';

        // Auto-run Level 1 test
        currentWizardStep = 3;
        showWizardStep(3);
        await runProviderTest(uploadedProviderId, 1);
    } catch (error) {
        console.error('保存配置失败:', error);
        if (configErr) { configErr.textContent = error.data?.error || t('operationFailed'); configErr.style.display = 'block'; }
    }
}

// Steps 3-4: Run tests
export async function runProviderTest(providerId, level) {
    const resultContainerId = level === 1 ? 'erp-test1-results' : 'erp-test2-results';
    const resultContainer = document.getElementById(resultContainerId);
    if (resultContainer) {
        resultContainer.innerHTML = `<div style="color: var(--text-muted, #6b7280); padding: 8px;">${t('erpTestRunning')}</div>`;
    }

    try {
        const data = await erpFetch(`/erp/providers/${providerId}/test?level=${level}`, { method: 'POST' });
        // Backend returns {level, results: {method_name: {passed, latency_ms, error}}, all_passed}
        const resultsDict = data.results || {};
        const allPassed = data.all_passed === true;
        const resultsArray = Object.entries(resultsDict).map(([method, r]) => ({
            method, passed: r.passed, latency_ms: r.latency_ms, error: r.error
        }));

        if (resultContainer) {
            resultContainer.innerHTML = `
                <div style="margin-bottom: 8px; font-weight: 600; color: ${allPassed ? '#16a34a' : '#dc2626'};">
                    ${allPassed ? t('erpTestAllPassed') : t('erpTestSomeFailed')}
                </div>
                ${resultsArray.map(r => `
                    <div style="display: flex; align-items: center; gap: 8px; padding: 6px 0; border-bottom: 1px solid var(--border-color, #e5e7eb);">
                        <span style="color: ${r.passed ? '#16a34a' : '#dc2626'}; font-size: 14px;">
                            ${r.passed ? '✓' : '✗'}
                        </span>
                        <span style="flex: 1; font-size: 13px;">${escapeHtml(r.method)}</span>
                        ${r.latency_ms != null ? `<span style="font-size: 11px; color: var(--text-muted, #9ca3af);">${r.latency_ms}ms</span>` : ''}
                        ${r.error ? `<span style="font-size: 11px; color: #dc2626;">${escapeHtml(r.error)}</span>` : ''}
                    </div>
                `).join('')}
            `;
        }

        // Update provider test_results in local list (nested by level)
        const idx = providers.findIndex(p => p.id === providerId);
        if (idx >= 0) {
            if (!providers[idx].test_results) providers[idx].test_results = {};
            providers[idx].test_results[`level${level}`] = data;
        }

        // Enable activate button in step 5 only if level 1 passed
        if (level === 1) {
            const activateBtn = document.getElementById('erp-wizard-activate-btn');
            if (activateBtn) {
                activateBtn.disabled = !allPassed;
                activateBtn.title = allPassed ? '' : t('erpActivateRequireL1');
            }
        }

        return allPassed;
    } catch (error) {
        console.error(`Level ${level} 测试失败:`, error);
        if (resultContainer) {
            resultContainer.innerHTML = `<div style="color: #dc2626; padding: 8px;">${error.data?.error || t('operationFailed')}</div>`;
        }
        return false;
    }
}

// ============ Wizard step-specific actions ============

// Called from Step 3 "Next" button — go to step 4
// But saveProviderConfig already moves to step 3 and runs test.
// The "next" from step 3 goes to step 4 (Level 2 warning).
// From step 4, user can run test or skip to step 5.

export async function wizardRunLevel2() {
    if (!uploadedProviderId) return;
    currentWizardStep = 4;
    showWizardStep(4);
    await runProviderTest(uploadedProviderId, 2);
    // Show "proceed to results" button after test completes
    const nextBtn = document.getElementById('erp-level2-next-btn');
    if (nextBtn) nextBtn.style.display = '';
}

export async function wizardGoToResults() {
    currentWizardStep = 5;
    showWizardStep(5);
    renderWizardResults();
}

function renderWizardResults() {
    const summary = document.getElementById('erp-wizard-summary');
    if (!summary) return;
    const provider = providers.find(p => p.id === uploadedProviderId);
    const testPassed = getProviderTestStatus(provider) === 'passed';
    summary.innerHTML = `
        <div style="padding: 16px; border-radius: 8px; background: ${testPassed ? '#f0fdf4' : '#fef2f2'}; border: 1px solid ${testPassed ? '#bbf7d0' : '#fecaca'}; margin-bottom: 16px;">
            <div style="font-weight: 600; color: ${testPassed ? '#16a34a' : '#dc2626'}; margin-bottom: 4px;">
                ${testPassed ? t('erpTestAllPassed') : t('erpTestSomeFailed')}
            </div>
            <div style="font-size: 13px; color: var(--text-muted, #6b7280);">${t('erpUploadProvider')}: ${escapeHtml(wizardProviderName)}</div>
        </div>
    `;
    const activateBtn = document.getElementById('erp-wizard-activate-btn');
    if (activateBtn) activateBtn.disabled = !testPassed;
}

export async function wizardActivate() {
    if (!uploadedProviderId) return;
    try {
        await activateProvider(uploadedProviderId);
        closeUploadWizard();
        await loadERPStatus();
    } catch (error) {
        alert(error.data?.error || t('operationFailed'));
    }
}
