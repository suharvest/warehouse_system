// ============ Tab 切换模块 ============
import {
    currentTab, setCurrentTab,
    countdownSeconds, setCountdownSeconds,
    countdownInterval, setCountdownInterval,
    trendChart, categoryChart, topStockChart,
    detailTrendChart, detailPieChart,
    currentProductName
} from '../state.js';

// 功能模块引用（由 main.js 设置）
let modules = {};

// 设置模块引用
export function setTabModules(mods) {
    modules = mods;
}

// ============ Tab 切换 ============
export function switchTab(tabId, filters = {}) {
    setCurrentTab(tabId);

    // 更新Tab按钮样式
    document.querySelectorAll('.nav-item').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tab === tabId);
    });

    // 更新Tab内容显示
    document.querySelectorAll('.tab-pane').forEach(pane => {
        pane.classList.toggle('active', pane.id === `tab-${tabId}`);
    });

    // 更新URL hash
    updateUrlHash(tabId, filters);

    // 离开 MCP tab 时停止刷新
    if (tabId !== 'mcp' && modules.stopMCPRefresh) {
        modules.stopMCPRefresh();
    }

    // 加载对应数据
    switch (tabId) {
        case 'dashboard':
            if (modules.loadDashboardData) modules.loadDashboardData();
            setTimeout(() => {
                trendChart && trendChart.resize();
                categoryChart && categoryChart.resize();
                topStockChart && topStockChart.resize();
            }, 100);
            break;
        case 'records':
            if (modules.applyRecordsFilters) modules.applyRecordsFilters(filters);
            if (modules.loadRecordsFilterOptions) modules.loadRecordsFilterOptions();
            if (modules.loadRecords) modules.loadRecords();
            break;
        case 'inventory':
            if (modules.applyInventoryFilters) modules.applyInventoryFilters(filters);
            if (modules.loadInventory) modules.loadInventory();
            break;
        case 'detail':
            if (filters.product && modules.setProductSelectorValue) {
                setTimeout(() => {
                    modules.setProductSelectorValue(filters.product);
                }, 100);
            }
            setTimeout(() => {
                detailTrendChart && detailTrendChart.resize();
                detailPieChart && detailPieChart.resize();
            }, 100);
            break;
        case 'contacts':
            if (modules.loadContacts) modules.loadContacts();
            break;
        case 'users':
            if (modules.loadUsers) modules.loadUsers();
            if (modules.loadApiKeys) modules.loadApiKeys();
            break;
        case 'mcp':
            if (modules.loadMCPConnections) modules.loadMCPConnections();
            if (modules.startMCPRefresh) modules.startMCPRefresh();
            break;
    }

    resetCountdown();
}

// 更新URL hash
function updateUrlHash(tabId, filters = {}) {
    const params = new URLSearchParams();
    params.set('tab', tabId);
    for (const [key, value] of Object.entries(filters)) {
        if (value) params.set(key, value);
    }
    window.location.hash = params.toString();
}

// 从URL hash初始化
export function initFromHash() {
    const hash = window.location.hash;
    if (hash) {
        const params = new URLSearchParams(hash.substring(1));
        const tab = params.get('tab');
        if (tab && ['dashboard', 'records', 'inventory', 'detail', 'contacts', 'users', 'mcp'].includes(tab)) {
            const filters = {};
            for (const [key, value] of params.entries()) {
                if (key !== 'tab') {
                    filters[key] = value;
                }
            }
            switchTab(tab, filters);
            return;
        }
    }
    // 默认加载看板
    if (modules.loadDashboardData) modules.loadDashboardData();
}

// ============ 自动刷新 ============
export function startAutoUpdate() {
    if (countdownInterval) clearInterval(countdownInterval);

    const interval = setInterval(function () {
        setCountdownSeconds(countdownSeconds - 1);
        const countdownEl = document.getElementById('countdown');
        if (countdownEl) countdownEl.textContent = countdownSeconds;

        if (countdownSeconds <= 0) {
            refreshCurrentTab();
            setCountdownSeconds(20);
        }
    }, 1000);

    setCountdownInterval(interval);
}

export function resetCountdown() {
    setCountdownSeconds(20);
    const countdownEl = document.getElementById('countdown');
    if (countdownEl) countdownEl.textContent = countdownSeconds;
}

export function refreshCurrentTab() {
    switch (currentTab) {
        case 'dashboard':
            if (modules.loadDashboardData) modules.loadDashboardData();
            break;
        case 'records':
            if (modules.loadRecords) modules.loadRecords();
            break;
        case 'inventory':
            if (modules.loadInventory) modules.loadInventory();
            break;
        case 'detail':
            if (currentProductName && modules.loadProductDetail) modules.loadProductDetail();
            break;
    }
    resetCountdown();
}

// 返回库存列表
export function goBackToInventory() {
    switchTab('inventory');
}
