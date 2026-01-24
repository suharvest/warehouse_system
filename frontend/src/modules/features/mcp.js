// ============ MCP 连接管理模块 ============
import { t } from '../../../i18n.js';
import { API_BASE_URL } from '../state.js';

// API 封装
async function mcpFetch(url, options = {}) {
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
let connections = [];
let refreshInterval = null;

// ============ 加载连接列表 ============
export async function loadMCPConnections() {
    const tbody = document.getElementById('mcp-connections-tbody');
    if (!tbody) return;

    try {
        connections = await mcpFetch('/mcp/connections');
        renderConnections();
    } catch (error) {
        console.error('加载MCP连接失败:', error);
        tbody.innerHTML = `<tr><td colspan="5" class="text-center text-gray-400 py-8">${t('loadError')}</td></tr>`;
    }
}

function renderConnections() {
    const tbody = document.getElementById('mcp-connections-tbody');
    if (!tbody) return;

    if (!connections || connections.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" class="text-center text-gray-400 py-8">${t('mcpNoConnections')}</td></tr>`;
        return;
    }

    tbody.innerHTML = connections.map(conn => {
        const statusIcon = getStatusIcon(conn.status);
        const statusText = getStatusText(conn);
        const maskedEndpoint = maskEndpoint(conn.mcp_endpoint);
        const actions = getConnectionActions(conn);

        return `
            <tr>
                <td>
                    <div class="flex items-center gap-2">
                        <span class="mcp-status-dot ${conn.status}">${statusIcon}</span>
                        <div>
                            <div class="font-medium">${escapeHtml(conn.name)}</div>
                            <div class="text-xs text-gray-400">${maskedEndpoint}</div>
                        </div>
                    </div>
                </td>
                <td>
                    <span class="mcp-status-badge ${conn.status}">${statusText}</span>
                </td>
                <td class="text-sm text-gray-500">${conn.uptime_seconds ? formatUptime(conn.uptime_seconds) : '-'}</td>
                <td class="text-sm text-gray-500">${conn.auto_start ? t('mcpAutoStartYes') : t('mcpAutoStartNo')}</td>
                <td>${actions}</td>
            </tr>
        `;
    }).join('');
}

function getStatusIcon(status) {
    switch (status) {
        case 'running': return '<span style="color:#22c55e;">&#9679;</span>';
        case 'error': return '<span style="color:#ef4444;">&#9679;</span>';
        default: return '<span style="color:#9ca3af;">&#9679;</span>';
    }
}

function getStatusText(conn) {
    switch (conn.status) {
        case 'running': return t('mcpStatusRunning');
        case 'error': return conn.error_message || t('mcpStatusError');
        default: return t('mcpStatusStopped');
    }
}

function maskEndpoint(endpoint) {
    if (!endpoint) return '';
    if (endpoint.length > 40) {
        return endpoint.substring(0, 30) + '***';
    }
    return endpoint;
}

function formatUptime(seconds) {
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return `${h}h ${m}m`;
}

function getConnectionActions(conn) {
    const actions = [];
    if (conn.status === 'running') {
        actions.push(`<button class="action-btn" data-action="mcpStop" data-conn-id="${conn.id}" title="${t('mcpStop')}">${t('mcpStop')}</button>`);
        actions.push(`<button class="action-btn" data-action="mcpRestart" data-conn-id="${conn.id}" title="${t('mcpRestart')}">${t('mcpRestart')}</button>`);
    } else {
        actions.push(`<button class="action-btn add-btn" data-action="mcpStart" data-conn-id="${conn.id}" title="${t('mcpStart')}">${t('mcpStart')}</button>`);
    }
    actions.push(`<button class="action-btn" data-action="mcpEdit" data-conn-id="${conn.id}" title="${t('edit')}">${t('edit')}</button>`);
    if (conn.status !== 'running') {
        actions.push(`<button class="action-btn delete-btn" data-action="mcpDelete" data-conn-id="${conn.id}" title="${t('delete')}">${t('delete')}</button>`);
    }
    return `<div class="flex gap-2 flex-wrap">${actions.join('')}</div>`;
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// ============ 添加连接 ============
export function showAddMCPModal() {
    const modal = document.getElementById('mcp-modal');
    if (!modal) return;

    // 重置表单
    document.getElementById('mcp-modal-title').textContent = t('mcpAddConnection');
    document.getElementById('mcp-conn-id').value = '';
    document.getElementById('mcp-conn-name').value = '';
    document.getElementById('mcp-conn-endpoint').value = '';
    document.getElementById('mcp-conn-role').value = 'operate';
    document.getElementById('mcp-conn-autostart').checked = true;
    document.getElementById('mcp-modal-error').style.display = 'none';

    modal.classList.add('show');
}

export function closeMCPModal() {
    const modal = document.getElementById('mcp-modal');
    if (modal) modal.classList.remove('show');
}

export async function handleSaveMCP() {
    const connId = document.getElementById('mcp-conn-id').value;
    const name = document.getElementById('mcp-conn-name').value.trim();
    const endpoint = document.getElementById('mcp-conn-endpoint').value.trim();
    const role = document.getElementById('mcp-conn-role').value;
    const autoStart = document.getElementById('mcp-conn-autostart').checked;
    const errorDiv = document.getElementById('mcp-modal-error');

    if (!name || !endpoint) {
        errorDiv.textContent = t('fillAllFields');
        errorDiv.style.display = 'block';
        return;
    }

    try {
        if (connId) {
            // 编辑模式
            await mcpFetch(`/mcp/connections/${connId}`, {
                method: 'PUT',
                body: JSON.stringify({ name, mcp_endpoint: endpoint, role, auto_start: autoStart })
            });
        } else {
            // 新建模式
            await mcpFetch('/mcp/connections', {
                method: 'POST',
                body: JSON.stringify({ name, mcp_endpoint: endpoint, role, auto_start: autoStart })
            });
        }
        closeMCPModal();
        await loadMCPConnections();
    } catch (error) {
        console.error('保存MCP连接失败:', error);
        errorDiv.textContent = error.data?.detail || t('operationFailed');
        errorDiv.style.display = 'block';
    }
}

// ============ 编辑连接 ============
export function editMCPConnection(connId) {
    const conn = connections.find(c => c.id === connId);
    if (!conn) return;

    const modal = document.getElementById('mcp-modal');
    if (!modal) return;

    document.getElementById('mcp-modal-title').textContent = t('mcpEditConnection');
    document.getElementById('mcp-conn-id').value = conn.id;
    document.getElementById('mcp-conn-name').value = conn.name;
    document.getElementById('mcp-conn-endpoint').value = conn.mcp_endpoint;
    document.getElementById('mcp-conn-role').value = conn.role || 'operate';
    document.getElementById('mcp-conn-autostart').checked = conn.auto_start;
    document.getElementById('mcp-modal-error').style.display = 'none';

    modal.classList.add('show');
}

// ============ 启动/停止/重启/删除 ============
export async function startMCPConnection(connId) {
    try {
        await mcpFetch(`/mcp/connections/${connId}/start`, { method: 'POST' });
        await loadMCPConnections();
    } catch (error) {
        console.error('启动失败:', error);
        alert(error.data?.detail || t('operationFailed'));
    }
}

export async function stopMCPConnection(connId) {
    try {
        await mcpFetch(`/mcp/connections/${connId}/stop`, { method: 'POST' });
        await loadMCPConnections();
    } catch (error) {
        console.error('停止失败:', error);
        alert(error.data?.detail || t('operationFailed'));
    }
}

export async function restartMCPConnection(connId) {
    try {
        await mcpFetch(`/mcp/connections/${connId}/restart`, { method: 'POST' });
        await loadMCPConnections();
    } catch (error) {
        console.error('重启失败:', error);
        alert(error.data?.detail || t('operationFailed'));
    }
}

export async function deleteMCPConnection(connId) {
    const conn = connections.find(c => c.id === connId);
    const name = conn ? conn.name : connId;
    if (!confirm(t('mcpConfirmDelete').replace('{name}', name))) return;

    try {
        await mcpFetch(`/mcp/connections/${connId}`, { method: 'DELETE' });
        await loadMCPConnections();
    } catch (error) {
        console.error('删除失败:', error);
        alert(error.data?.detail || t('operationFailed'));
    }
}

// ============ 日志查看 ============
export async function showMCPLogs(connId) {
    try {
        const data = await mcpFetch(`/mcp/connections/${connId}/logs?lines=100`);
        const logs = data.logs || [];
        alert(logs.length > 0 ? logs.join('\n') : t('mcpNoLogs'));
    } catch (error) {
        console.error('获取日志失败:', error);
    }
}

// ============ 自动刷新 ============
export function startMCPRefresh() {
    stopMCPRefresh();
    refreshInterval = setInterval(() => {
        loadMCPConnections();
    }, 10000); // 每10秒刷新
}

export function stopMCPRefresh() {
    if (refreshInterval) {
        clearInterval(refreshInterval);
        refreshInterval = null;
    }
}
