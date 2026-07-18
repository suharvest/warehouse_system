// ============ Tab 切换模块 ============
import {
    currentTab, setCurrentTab,
    countdownSeconds, setCountdownSeconds,
    countdownInterval, setCountdownInterval,
    trendChart, categoryChart, topStockChart,
    detailTrendChart, detailPieChart,
    currentProductName,
    currentWarehouse, setCurrentWarehouse, allWarehouses, setAllWarehouses, getStoredWarehouseSlug,
    getCurrentUser,
    bumpWarehouseEpoch,
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

    // 离开 users tab 时停止 ERP 刷新
    if (tabId !== 'users' && modules.stopERPRefresh) {
        modules.stopERPRefresh();
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
            if (modules.loadWarehousesList) modules.loadWarehousesList();
            if (modules.loadERPStatus) modules.loadERPStatus();
            if (modules.startERPRefresh) modules.startERPRefresh();
            if (modules.loadTenantInfo) modules.loadTenantInfo();
            break;
        case 'tenants':
            if (modules.renderTenantsPanel) modules.renderTenantsPanel();
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

// 从URL初始化（路径 + hash）
export function initFromHash() {
    // 解析路径中的仓库上下文: /w/<slug>/
    initWarehouseFromPath();

    const hash = window.location.hash;
    if (hash) {
        const params = new URLSearchParams(hash.substring(1));
        const tab = params.get('tab');
        if (tab && ['dashboard', 'records', 'inventory', 'detail', 'contacts', 'users', 'mcp', 'tenants'].includes(tab)) {
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

// 从 URL path 解析仓库上下文
// 优先级：URL > localStorage > （多仓库时为 null，单仓库由 renderWarehouseSwitcher 自动选）
function initWarehouseFromPath() {
    const path = window.location.pathname;
    const match = path.match(/^\/w\/([a-z0-9][a-z0-9\-]*)\/?\s*$/);
    if (match && allWarehouses.length > 0) {
        const slug = match[1];
        const wh = allWarehouses.find(w => w.slug === slug);
        if (wh) {
            setCurrentWarehouse(wh);
            updateWarehouseSwitcherDisplay();
            return;
        }
    }
    // URL 没有 → 尝试从 localStorage 恢复（避免刷新或访问 / 时丢仓库上下文）
    const storedSlug = getStoredWarehouseSlug();
    if (storedSlug && allWarehouses.length > 0) {
        const wh = allWarehouses.find(w => w.slug === storedSlug);
        if (wh) {
            setCurrentWarehouse(wh);
            // 同步 URL，让后续刷新仍然指向具体仓库
            const newPath = `/w/${wh.slug}/`;
            if (window.location.pathname !== newPath) {
                window.history.replaceState(null, '', newPath + window.location.hash);
            }
            updateWarehouseSwitcherDisplay();
            return;
        }
    }
    // 单仓库时保持已自动选中的默认仓库，不重置为全局视图
    if (currentWarehouse) return;
    setCurrentWarehouse(null);
    updateWarehouseSwitcherDisplay();
}

// ============ 仓库切换 ============

export function switchWarehouse(warehouse) {
    // FIRST：递增 epoch，让任何"上一仓"还在 flight 的 loader 在响应回来
    // 时检测到过时，自动丢弃。否则快速切仓 A→B 会让 A 的慢响应覆盖 B 的
    // currentWarehouse 对应数据。
    bumpWarehouseEpoch();

    setCurrentWarehouse(warehouse);
    updateWarehouseSwitcherDisplay();

    // 更新 URL 路径
    if (warehouse) {
        const newPath = `/w/${warehouse.slug}/`;
        if (window.location.pathname !== newPath) {
            window.history.pushState(null, '', newPath + window.location.hash);
        }
    } else {
        if (window.location.pathname !== '/') {
            window.history.pushState(null, '', '/' + window.location.hash);
        }
    }

    // 重新加载当前tab的数据（仓库上下文变了）
    refreshCurrentTab();
    // 刷新分类和产品列表
    if (modules.loadCategories) modules.loadCategories();
    if (modules.loadAllProducts) modules.loadAllProducts();
}

export function updateWarehouseSwitcherDisplay() {
    const nameEl = document.getElementById('currentWarehouseName');
    if (nameEl) {
        if (currentWarehouse) {
            nameEl.textContent = currentWarehouse.name;
        } else if (allWarehouses.length === 0) {
            nameEl.textContent = modules.t ? modules.t('noWarehouseAccess') : '无仓库访问权限';
        } else {
            nameEl.textContent = modules.t ? modules.t('allWarehouses') : '全部仓库';
        }
    }
}

export function renderWarehouseSwitcher() {
    const switcher = document.getElementById('warehouseSwitcher');
    if (!switcher) return;

    // 换账号后仓库列表可能完全不同，检查当前仓库是否还在列表里，不在则重置
    if (currentWarehouse && !allWarehouses.find(w => w.id === currentWarehouse.id)) {
        setCurrentWarehouse(null);
    }

    // 单仓库时自动选中默认仓库
    if (allWarehouses.length === 1 && !currentWarehouse) {
        setCurrentWarehouse(allWarehouses[0]);
    }

    // 始终显示切换器（即使只有一个仓库，也需要看到当前仓库）
    switcher.style.display = '';
    updateWarehouseSwitcherDisplay();

    // 渲染下拉列表
    const dropdown = document.getElementById('warehouseDropdown');
    if (!dropdown) return;

    const t = modules.t || (k => k);

    if (allWarehouses.length === 0) {
        dropdown.innerHTML = `<div class="warehouse-option disabled">${t('noWarehouseAccess')}</div>`;
        return;
    }

    let html = `<div class="warehouse-option${!currentWarehouse ? ' active' : ''}" data-action="selectWarehouse" data-slug="">
        ${t('allWarehouses')}
    </div>`;

    // 跨租户场景（全局 admin）：按 tenant_name 分组，避免同名仓库无法区分
    const distinctTenants = new Set(
        allWarehouses.map(w => w.tenant_id ?? null).filter(t => t !== null)
    );
    const isCrossTenant = distinctTenants.size > 1;

    if (isCrossTenant) {
        const groups = new Map();
        for (const wh of allWarehouses) {
            const key = wh.tenant_name || `Tenant #${wh.tenant_id ?? '?'}`;
            if (!groups.has(key)) groups.set(key, []);
            groups.get(key).push(wh);
        }
        for (const [tenantName, list] of groups) {
            html += `<div class="warehouse-group-label">${escapeHtml(tenantName)}</div>`;
            for (const wh of list) {
                const active = currentWarehouse && currentWarehouse.id === wh.id ? ' active' : '';
                html += `<div class="warehouse-option warehouse-option-indent${active}" data-action="selectWarehouse" data-slug="${wh.slug}">
                    ${escapeHtml(wh.name)}${wh.is_default ? ' ★' : ''}
                </div>`;
            }
        }
    } else {
        for (const wh of allWarehouses) {
            const active = currentWarehouse && currentWarehouse.id === wh.id ? ' active' : '';
            html += `<div class="warehouse-option${active}" data-action="selectWarehouse" data-slug="${wh.slug}">
                ${escapeHtml(wh.name)}${wh.is_default ? ' ★' : ''}
            </div>`;
        }
    }

    dropdown.innerHTML = html;
}

function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
}

export function toggleWarehouseSwitcher() {
    const dropdown = document.getElementById('warehouseDropdown');
    if (dropdown) {
        dropdown.classList.toggle('show');
    }
}

export function selectWarehouse(slug) {
    const dropdown = document.getElementById('warehouseDropdown');
    if (dropdown) dropdown.classList.remove('show');

    if (!slug) {
        switchWarehouse(null);
    } else {
        const wh = allWarehouses.find(w => w.slug === slug);
        if (wh) switchWarehouse(wh);
    }
}

// ============ 自动刷新 ============
let visibilityHandlerRegistered = false;

export function stopAutoUpdate() {
    if (countdownInterval) {
        clearInterval(countdownInterval);
        setCountdownInterval(null);
    }
}

export function startAutoUpdate() {
    stopAutoUpdate();

    // 页面从后台切回前台时立即刷新一次。后台标签的 setInterval 会被浏览器
    // 限流（隐藏时约 1min 才 tick），否则切走再切回 / 笔记本唤醒后，看板要等
    // 最多一个限流周期数字才更新。只注册一次，避免多次登录叠加监听。
    if (!visibilityHandlerRegistered) {
        visibilityHandlerRegistered = true;
        document.addEventListener('visibilitychange', function () {
            if (document.visibilityState === 'visible') {
                refreshCurrentTab();
            }
        });
    }

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
    // 未登录时不发请求，避免刷出满屏 401
    if (!getCurrentUser()) return;

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
