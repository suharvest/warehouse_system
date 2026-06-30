// ============ MCP 连接管理模块 ============
import { t } from '../../../i18n.js';
import { API_BASE_URL } from '../state.js';
import { getCurrentWarehouseId, warehousesApi } from '../api.js';
import { getCurrentUser } from '../state.js';

// API 封装
async function mcpFetch(url, options = {}) {
    const response = await fetch(`${API_BASE_URL}${url}`, {
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', ...options.headers },
        ...options
    });
    if (!response.ok) {
        const error = new Error(`HTTP ${response.status}`);
        try {
            error.data = await response.json();
            if (error.data?.detail) {
                error.message = typeof error.data.detail === 'string'
                    ? error.data.detail
                    : JSON.stringify(error.data.detail);
            }
        } catch {}
        error.status = response.status;
        throw error;
    }
    return response.json();
}

// 状态
let connections = [];
let refreshInterval = null;
// 设备子面板状态：connId -> { devices: [...] }；openDevicePanels 记录哪些智能体的设备区已展开。
let deviceState = {};
const openDevicePanels = new Set();

// ============ 加载连接列表 ============
export async function loadMCPConnections() {
    const tbody = document.getElementById('mcp-connections-tbody');
    if (!tbody) return;

    try {
        const whId = getCurrentWarehouseId();
        const url = whId ? `/mcp/connections?warehouse_id=${whId}` : '/mcp/connections';
        connections = await mcpFetch(url);
        renderConnections();
    } catch (error) {
        console.error('加载MCP连接失败:', error);
        const detail = error.status ? `HTTP ${error.status}: ${escapeHtml(error.message)}` : escapeHtml(error.message || t('loadError'));
        tbody.innerHTML = `<tr><td colspan="8" class="text-center text-gray-400 py-8">${t('loadError')}<br><span class="text-xs">${detail}</span></td></tr>`;
    }
}

function renderConnections() {
    const tbody = document.getElementById('mcp-connections-tbody');
    if (!tbody) return;

    // 全局管理员（tenant_id=null）显示租户列
    const user = getCurrentUser();
    const showTenantCol = user && (user.tenant_id === null || user.tenant_id === undefined);
    document.querySelectorAll('.mcp-tenant-col').forEach(el => {
        el.style.display = showTenantCol ? '' : 'none';
    });
    const colSpan = showTenantCol ? 8 : 7;

    if (!connections || connections.length === 0) {
        tbody.innerHTML = `<tr><td colspan="${colSpan}" class="text-center text-gray-400 py-8">${t('mcpNoConnections')}</td></tr>`;
        return;
    }

    tbody.innerHTML = connections.map(conn => {
        const statusIcon = getStatusIcon(conn.status);
        const statusText = getStatusText(conn);
        const wsStatus = getWebSocketStatus(conn);
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
                ${showTenantCol ? `<td class="mcp-tenant-col text-sm">${escapeHtml(conn.tenant_name || '-')}</td>` : ''}
                <td class="text-sm">${escapeHtml(conn.warehouse_name || '-')}</td>
                <td>
                    <span class="mcp-status-badge ${conn.status}">${statusText}</span>
                </td>
                <td>
                    <span class="mcp-status-badge ${wsStatus.className}" title="${escapeHtml(wsStatus.title)}">${wsStatus.text}</span>
                </td>
                <td class="text-sm text-gray-500">${conn.uptime_seconds ? formatUptime(conn.uptime_seconds) : '-'}</td>
                <td class="text-sm text-gray-500">${conn.auto_start ? t('mcpAutoStartYes') : t('mcpAutoStartNo')}</td>
                <td>${actions}</td>
            </tr>
            <tr class="mcp-device-detail-row" id="mcp-devices-row-${conn.id}" style="display:none;">
                <td colspan="${colSpan}" style="background:#f9fafb;padding:0;border-top:none;">
                    <div id="mcp-devices-panel-${conn.id}" style="padding:12px 16px;"></div>
                </td>
            </tr>
        `;
    }).join('');

    // 自动刷新会重建 tbody，把已展开的设备区抹掉。重新展开此前打开的面板。
    openDevicePanels.forEach(cid => {
        const row = document.getElementById(`mcp-devices-row-${cid}`);
        if (!row) { openDevicePanels.delete(cid); return; }
        row.style.display = '';
        if (deviceState[cid]) renderDevicePanel(cid);
        else loadDevices(cid);
    });
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

function getWebSocketStatus(conn) {
    const status = conn.websocket_status || 'not_started';
    const error = conn.websocket_error || '';
    switch (status) {
        case 'connected':
            return { text: t('mcpWsConnected'), className: 'connected', title: t('mcpWsConnected') };
        case 'connecting':
            return { text: t('mcpWsConnecting'), className: 'connecting', title: t('mcpWsConnecting') };
        case 'disconnected':
            return { text: t('mcpWsDisconnected'), className: 'disconnected', title: error || t('mcpWsDisconnected') };
        case 'error':
            return { text: t('mcpWsError'), className: 'error', title: error || t('mcpWsError') };
        default:
            return { text: t('mcpWsNotStarted'), className: 'stopped', title: t('mcpWsNotStarted') };
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
        actions.push(`<button class="action-btn delete-btn" data-action="mcpDelete" data-conn-id="${conn.id}" title="${t('delete')}">${t('delete')}</button>`);
    }
    actions.push(`<button class="action-btn" data-action="mcpEdit" data-conn-id="${conn.id}" title="${t('edit')}">${t('edit')}</button>`);
    const devCount = deviceState[conn.id]?.devices?.length;
    const devLabel = devCount ? `${t('mcpDevices')} (${devCount})` : t('mcpDevices');
    actions.push(`<button class="action-btn" data-action="mcpDevices" data-conn-id="${conn.id}" title="${t('mcpDeviceList')}">${devLabel}</button>`);
    return `<div class="flex gap-2 flex-wrap">${actions.join('')}</div>`;
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// ============ 添加连接 ============
export async function showAddMCPModal() {
    const modal = document.getElementById('mcp-modal');
    if (!modal) return;

    // 重置表单
    document.getElementById('mcp-modal-title').textContent = t('mcpAddConnection');
    document.getElementById('mcp-conn-id').value = '';
    document.getElementById('mcp-conn-name').value = '';
    document.getElementById('mcp-conn-endpoint').value = '';
    document.getElementById('mcp-conn-autostart').checked = true;
    document.getElementById('mcp-modal-error').style.display = 'none';

    // 加载仓库列表
    const whSelect = document.getElementById('mcp-conn-warehouse');
    whSelect.innerHTML = `<option value='' data-i18n='selectWarehouse'>${t('selectWarehouse')}</option>`;
    try {
        const data = await warehousesApi.getList();
        const warehouses = data.warehouses || data || [];
        warehouses.forEach(w => {
            const opt = document.createElement('option');
            opt.value = w.id;
            opt.textContent = w.name;
            whSelect.appendChild(opt);
        });
        const curWhId = getCurrentWarehouseId();
        if (curWhId) whSelect.value = curWhId;
    } catch (e) {
        console.error('加载仓库列表失败:', e);
    }

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
    const role = 'operate';  // MCP 智能体固定使用操作员权限
    const autoStart = document.getElementById('mcp-conn-autostart').checked;
    const whId = document.getElementById('mcp-conn-warehouse').value;
    const errorDiv = document.getElementById('mcp-modal-error');

    if (!name || !endpoint) {
        errorDiv.textContent = t('fillAllFields');
        errorDiv.style.display = 'block';
        return;
    }
    if (!whId) {
        errorDiv.textContent = t('selectWarehouse');
        errorDiv.style.display = 'block';
        return;
    }

    try {
        if (connId) {
            // 编辑模式
            await mcpFetch(`/mcp/connections/${connId}`, {
                method: 'PUT',
                body: JSON.stringify({ name, mcp_endpoint: endpoint, role, auto_start: autoStart, warehouse_id: parseInt(whId, 10) })
            });
        } else {
            // 新建模式
            await mcpFetch('/mcp/connections', {
                method: 'POST',
                body: JSON.stringify({ name, mcp_endpoint: endpoint, role, auto_start: autoStart, warehouse_id: parseInt(whId, 10) })
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
export async function editMCPConnection(connId) {
    const conn = connections.find(c => c.id === connId);
    if (!conn) return;

    const modal = document.getElementById('mcp-modal');
    if (!modal) return;

    document.getElementById('mcp-modal-title').textContent = t('mcpEditConnection');
    document.getElementById('mcp-conn-id').value = conn.id;
    document.getElementById('mcp-conn-name').value = conn.name;
    document.getElementById('mcp-conn-endpoint').value = conn.mcp_endpoint;
    document.getElementById('mcp-conn-autostart').checked = conn.auto_start;
    document.getElementById('mcp-modal-error').style.display = 'none';

    // 加载仓库列表
    const whSelect = document.getElementById('mcp-conn-warehouse');
    whSelect.innerHTML = `<option value='' data-i18n='selectWarehouse'>${t('selectWarehouse')}</option>`;
    try {
        const data = await warehousesApi.getList();
        const warehouses = data.warehouses || data || [];
        warehouses.forEach(w => {
            const opt = document.createElement('option');
            opt.value = w.id;
            opt.textContent = w.name;
            whSelect.appendChild(opt);
        });
        if (conn.warehouse_id) whSelect.value = conn.warehouse_id;
    } catch (e) {
        console.error('加载仓库列表失败:', e);
    }

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
        const data = await mcpFetch(`/mcp/connections/${connId}/logs?lines=200`);
        const logs = data.logs || [];
        if (logs.length === 0) {
            alert(t('mcpNoLogs') || '暂无日志');
            return;
        }
        const win = window.open('', '_blank', 'width=900,height=600,scrollbars=yes');
        if (win) {
            win.document.write(`<pre style="font-family:monospace;font-size:12px;padding:10px;white-space:pre-wrap;word-break:break-all;">${logs.map(l => l.replace(/</g, '&lt;')).join('\n')}</pre>`);
            win.document.close();
        } else {
            alert(logs.join('\n'));
        }
    } catch (error) {
        console.error('获取日志失败:', error);
    }
}

// ============ 调试模式切换 ============
export async function toggleMCPDebug(connId, enable) {
    try {
        await mcpFetch(`/mcp/connections/${connId}/debug`, {
            method: 'POST',
            body: JSON.stringify({ enable: enable === '1' || enable === true })
        });
        await loadMCPConnections();
    } catch (error) {
        console.error('切换调试模式失败:', error);
        alert(error.data?.detail || t('operationFailed'));
    }
}

// ============ 智能体下挂的物理设备（一对多子表）============
async function deviceFetch(connId, path = '', options = {}) {
    return mcpFetch(`/mcp/connections/${connId}/devices${path}`, options);
}

export async function toggleMCPDevices(connId) {
    const row = document.getElementById(`mcp-devices-row-${connId}`);
    if (!row) return;
    const showing = row.style.display !== 'none';
    if (showing) {
        row.style.display = 'none';
        openDevicePanels.delete(connId);
        return;
    }
    row.style.display = '';
    openDevicePanels.add(connId);
    await loadDevices(connId);
}

async function loadDevices(connId) {
    const panel = document.getElementById(`mcp-devices-panel-${connId}`);
    if (!panel) return;
    panel.innerHTML = `<div class="text-sm text-gray-400">...</div>`;
    try {
        const devices = await deviceFetch(connId);
        deviceState[connId] = { devices };
        renderDevicePanel(connId);
        updateDeviceButtonCount(connId);
    } catch (error) {
        console.error('加载设备失败:', error);
        panel.innerHTML = `<div class="text-sm" style="color:#ef4444;">${escapeHtml(error.message || t('loadError'))}</div>`;
    }
}

// 设备增删后即时刷新该智能体行上"设备"按钮的计数，无需等整表重渲染。
function updateDeviceButtonCount(connId) {
    const btn = document.querySelector(`button[data-action="mcpDevices"][data-conn-id="${connId}"]`);
    if (!btn) return;
    const n = deviceState[connId]?.devices?.length;
    btn.textContent = n ? `${t('mcpDevices')} (${n})` : t('mcpDevices');
}

function renderDevicePanel(connId) {
    const panel = document.getElementById(`mcp-devices-panel-${connId}`);
    if (!panel) return;
    const devices = deviceState[connId]?.devices || [];

    const listHtml = devices.length === 0
        ? `<div class="text-sm text-gray-400" style="padding:8px 0;">${t('mcpDeviceNone')}</div>`
        : `<table style="width:100%;font-size:13px;margin-bottom:8px;">
            <thead><tr style="text-align:left;color:#6b7280;">
                <th style="padding:4px 8px;">${t('mcpDeviceName')}</th>
                <th style="padding:4px 8px;">${t('mcpDeviceId')}</th>
                <th style="padding:4px 8px;">${t('mcpDeviceIp')}</th>
                <th style="padding:4px 8px;">${t('mcpDeviceFaceEnabled')}</th>
                <th style="padding:4px 8px;">${t('actions')}</th>
            </tr></thead>
            <tbody>${devices.map(d => `
                <tr>
                    <td style="padding:4px 8px;">${escapeHtml(d.name || '-')}</td>
                    <td style="padding:4px 8px;font-family:monospace;">${escapeHtml(d.device_id || '-')}</td>
                    <td style="padding:4px 8px;font-family:monospace;">${escapeHtml(d.ip || '-')}:${d.port}</td>
                    <td style="padding:4px 8px;">
                        <span class="mcp-status-badge ${d.face_enabled ? 'connected' : 'stopped'}">${d.face_enabled ? t('mcpDeviceFaceOn') : t('mcpDeviceFaceOff')}</span>
                    </td>
                    <td style="padding:4px 8px;">
                        ${d.face_enabled ? `<button class="action-btn add-btn" data-action="mcpDevicePushFaces" data-conn-id="${connId}" data-dev-id="${d.id}">${t('mcpDevicePushFaces')}</button>` : ''}
                        <button class="action-btn" data-action="mcpDeviceEdit" data-conn-id="${connId}" data-dev-id="${d.id}">${t('edit')}</button>
                        <button class="action-btn delete-btn" data-action="mcpDeviceDelete" data-conn-id="${connId}" data-dev-id="${d.id}">${t('delete')}</button>
                    </td>
                </tr>`).join('')}</tbody>
           </table>`;

    panel.innerHTML = `
        <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px;">
            <div style="font-weight:600;">${t('mcpDeviceList')}</div>
            <button class="action-btn add-btn" data-action="mcpDeviceAdd" data-conn-id="${connId}">${t('mcpDeviceAdd')}</button>
        </div>
        ${listHtml}`;
}

// ---- 设备新增/编辑弹窗（对齐智能体编辑弹窗的视觉与交互） ----
// 弹窗 DOM 静态写在 index.html（#mcp-device-modal），此处仅引用并填充字段。
let deviceModalConnId = null;

function openDeviceModal(connId, dev) {
    deviceModalConnId = connId;
    const modal = document.getElementById('mcp-device-modal');
    if (!modal) return;
    const isEdit = !!dev;
    const titleEl = document.getElementById('mcp-device-modal-title');
    if (titleEl) titleEl.textContent = isEdit ? t('mcpEditDevice') : t('mcpDeviceAdd');
    const errorDiv = document.getElementById('mcp-device-modal-error');
    if (errorDiv) {
        errorDiv.textContent = '';
        errorDiv.style.display = 'none';
    }
    document.getElementById('mcp-device-id').value = dev?.id ?? '';
    document.getElementById('mcp-device-deviceId').value = dev?.device_id ?? '';
    document.getElementById('mcp-device-name').value = dev?.name ?? '';
    document.getElementById('mcp-device-ip').value = dev?.ip ?? '';
    document.getElementById('mcp-device-port').value = dev?.port ?? 80;
    document.getElementById('mcp-device-faceEnabled').checked = !!dev?.face_enabled;
    modal.classList.add('show');
}

export function showAddMCPDeviceModal(connId) {
    openDeviceModal(connId, null);
}

export function editMCPDevice(connId, devId) {
    const dev = (deviceState[connId]?.devices || []).find(d => String(d.id) === String(devId));
    if (!dev) return;
    openDeviceModal(connId, dev);
}

export function closeMCPDeviceModal() {
    const modal = document.getElementById('mcp-device-modal');
    if (modal) modal.classList.remove('show');
    deviceModalConnId = null;
}

export async function saveMCPDevice() {
    const connId = deviceModalConnId;
    if (connId == null) return;
    const errorDiv = document.getElementById('mcp-device-modal-error');
    const devId = document.getElementById('mcp-device-id').value;
    const ip = document.getElementById('mcp-device-ip').value.trim();
    if (!ip) {
        errorDiv.textContent = t('mcpDeviceIpRequired');
        errorDiv.style.display = 'block';
        return;
    }
    const payload = {
        device_id: document.getElementById('mcp-device-deviceId').value.trim() || null,
        name: document.getElementById('mcp-device-name').value.trim() || null,
        ip,
        port: parseInt(document.getElementById('mcp-device-port').value, 10) || 80,
        face_enabled: document.getElementById('mcp-device-faceEnabled').checked,
    };
    try {
        if (devId) {
            await deviceFetch(connId, `/${devId}`, { method: 'PUT', body: JSON.stringify(payload) });
        } else {
            await deviceFetch(connId, '', { method: 'POST', body: JSON.stringify(payload) });
        }
        closeMCPDeviceModal();
        await loadDevices(connId);
    } catch (error) {
        console.error('保存设备失败:', error);
        errorDiv.textContent = error.data?.detail || t('operationFailed');
        errorDiv.style.display = 'block';
    }
}

export async function pushFacesToDevice(connId, devId) {
    const dev = (deviceState[connId]?.devices || []).find(d => String(d.id) === String(devId));
    const name = dev ? (dev.name || dev.device_id || dev.ip || devId) : devId;
    const btn = document.querySelector(`button[data-action="mcpDevicePushFaces"][data-conn-id="${connId}"][data-dev-id="${devId}"]`);
    const origLabel = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = t('mcpDevicePushing'); }
    try {
        const result = await deviceFetch(connId, `/${devId}/push-faces`, { method: 'POST' });
        if (result && result.success) {
            alert(t('mcpDevicePushSuccess').replace('{name}', name).replace('{count}', result.pushed_count ?? 0));
        } else {
            alert(t('mcpDevicePushFailed').replace('{name}', name).replace('{error}', (result && result.error) || t('operationFailed')));
        }
    } catch (error) {
        console.error('下发人脸失败:', error);
        alert(t('mcpDevicePushFailed').replace('{name}', name).replace('{error}', error.data?.detail || error.message || t('operationFailed')));
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = origLabel; }
    }
}

export async function deleteMCPDevice(connId, devId) {
    const dev = (deviceState[connId]?.devices || []).find(d => String(d.id) === String(devId));
    const name = dev ? (dev.name || dev.device_id || dev.ip || devId) : devId;
    if (!confirm(t('mcpDeviceConfirmDelete').replace('{name}', name))) return;
    try {
        await deviceFetch(connId, `/${devId}`, { method: 'DELETE' });
        await loadDevices(connId);
    } catch (error) {
        console.error('删除设备失败:', error);
        alert(error.data?.detail || t('operationFailed'));
    }
}

// ============ 自动刷新 ============
export function startMCPRefresh() {
    stopMCPRefresh();
    refreshInterval = setInterval(() => {
        // 设备区展开时跳过刷新，避免重建 tbody 抹掉正在编辑的设备表单。
        if (openDevicePanels.size > 0) return;
        loadMCPConnections();
    }, 10000); // 每10秒刷新
}

export function stopMCPRefresh() {
    if (refreshInterval) {
        clearInterval(refreshInterval);
        refreshInterval = null;
    }
}
