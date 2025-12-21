// Import ECharts from npm package (local installation)
console.log('App module initializing...');
import * as echarts from 'echarts';

// Import i18n module
import { toggleLangDropdown, selectLanguage } from './i18n.js';

const API_BASE_URL = '/api';

// ============ 全局变量 ============
let currentTab = 'dashboard';
let countdownInterval = null;
let countdownSeconds = 20;

// 图表实例
let trendChart, categoryChart, topStockChart;
let detailTrendChart, detailPieChart;

// 分类数据
let allCategories = [];

// 用户认证状态
let currentUser = null;  // { id, username, display_name, role }
let isSystemInitialized = false;

function goBackToInventory() {
    switchTab('inventory');
}

// 库存列表分页状态
let inventoryCurrentPage = 1;
let inventoryPageSize = 20;
let inventoryTotalPages = 1;

// 进出库记录分页状态
let recordsCurrentPage = 1;
let recordsPageSize = 20;
let recordsTotalPages = 1;

// 产品详情状态
let currentProductName = '';
let detailCurrentPage = 1;
let detailPageSize = 20;
let detailTotalPages = 1;
let lastProductStats = null;

// 所有产品列表（用于产品选择器和新增记录）
let allProducts = [];

// 可搜索下拉组件状态
let productSelectorHighlightIndex = -1;
let recordProductHighlightIndex = -1;

// 联系方分页状态
let contactsCurrentPage = 1;
let contactsPageSize = 20;
let contactsTotalPages = 1;

// ============ 页面初始化 ============
document.addEventListener('DOMContentLoaded', async function () {
    // 首先检查认证状态
    await checkAuthStatus();

    initCharts();
    loadCategories();
    loadAllProducts();
    initFromHash();
    startAutoUpdate();
});

// 从URL hash初始化
function initFromHash() {
    const hash = window.location.hash;
    if (hash) {
        const params = new URLSearchParams(hash.substring(1));
        const tab = params.get('tab');
        if (tab && ['dashboard', 'records', 'inventory', 'detail', 'contacts', 'users'].includes(tab)) {
            // 解析筛选参数
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
    loadDashboardData();
}

// ============ Tab 切换 ============
function switchTab(tabId, filters = {}) {
    currentTab = tabId;

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

    // 加载对应数据
    switch (tabId) {
        case 'dashboard':
            loadDashboardData();
            // 切换回看板时，强制重绘图表以修正宽度
            setTimeout(() => {
                trendChart && trendChart.resize();
                categoryChart && categoryChart.resize();
                topStockChart && topStockChart.resize();
            }, 100);
            break;
        case 'records':
            applyRecordsFilters(filters);
            loadRecordsFilterOptions();
            loadRecords();
            break;
        case 'inventory':
            applyInventoryFilters(filters);
            loadInventory();
            break;
        case 'detail':
            if (filters.product) {
                // 使用可搜索选择器的设置方法
                setTimeout(() => {
                    setProductSelectorValue(filters.product);
                }, 100);
            }
            // 切换到详情页时，强制重绘图表
            setTimeout(() => {
                detailTrendChart && detailTrendChart.resize();
                detailPieChart && detailPieChart.resize();
            }, 100);
            break;
        case 'contacts':
            loadContacts();
            break;
        case 'users':
            loadUsers();
            loadApiKeys();
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

// 应用记录筛选器值
function applyRecordsFilters(filters) {
    if (filters.start_date) document.getElementById('filter-start-date').value = filters.start_date;
    if (filters.end_date) document.getElementById('filter-end-date').value = filters.end_date;
    if (filters.type) document.getElementById('filter-record-type').value = filters.type;
    if (filters.product_name) document.getElementById('filter-records-product').value = filters.product_name;
}

// 应用库存筛选器值
function applyInventoryFilters(filters) {
    if (filters.name) document.getElementById('filter-inventory-name').value = filters.name;
    if (filters.category) document.getElementById('filter-inventory-category').value = filters.category;
    if (filters.status) {
        resetDropdownSelection('filter-inventory-status-dropdown', filters.status.split(','));
    }
}

// ============ Dashboard 卡片点击 ============
function onTotalStockClick() {
    switchTab('inventory');
}

function onTodayInClick() {
    const today = new Date().toISOString().split('T')[0];
    switchTab('records', { type: 'in', start_date: today, end_date: today });
}

function onTodayOutClick() {
    const today = new Date().toISOString().split('T')[0];
    switchTab('records', { type: 'out', start_date: today, end_date: today });
}

function onLowStockClick() {
    // 筛选所有库存偏低和告急的产品
    switchTab('inventory', { status: 'warning,danger' });
}

// ============ 自动更新 ============
function startAutoUpdate() {
    if (countdownInterval) clearInterval(countdownInterval);

    countdownInterval = setInterval(function () {
        countdownSeconds--;
        const countdownEl = document.getElementById('countdown');
        if (countdownEl) countdownEl.textContent = countdownSeconds;

        if (countdownSeconds <= 0) {
            refreshCurrentTab();
            countdownSeconds = 20;
        }
    }, 1000);
}

function resetCountdown() {
    countdownSeconds = 20;
    const countdownEl = document.getElementById('countdown');
    if (countdownEl) countdownEl.textContent = countdownSeconds;
}

function refreshCurrentTab() {
    switch (currentTab) {
        case 'dashboard':
            loadDashboardData();
            break;
        case 'records':
            loadRecords();
            break;
        case 'inventory':
            loadInventory();
            break;
        case 'detail':
            if (currentProductName) loadProductDetail();
            break;
    }
    resetCountdown();
}

// ============ 图表初始化 ============
function initCharts() {
    trendChart = echarts.init(document.getElementById('trend-chart'));
    categoryChart = echarts.init(document.getElementById('category-chart'));
    topStockChart = echarts.init(document.getElementById('top-stock-chart'));

    window.addEventListener('resize', function () {
        trendChart && trendChart.resize();
        categoryChart && categoryChart.resize();
        topStockChart && topStockChart.resize();
        detailTrendChart && detailTrendChart.resize();
        detailPieChart && detailPieChart.resize();
    });
}

function initDetailCharts() {
    if (!detailTrendChart) {
        detailTrendChart = echarts.init(document.getElementById('detail-trend-chart'));
    }
    if (!detailPieChart) {
        detailPieChart = echarts.init(document.getElementById('detail-pie-chart'));
    }
}

// ============ 下拉多选组件 ============
function toggleDropdown(dropdownId) {
    const dropdown = document.getElementById(dropdownId);
    const isOpen = dropdown.classList.contains('open');

    // 关闭所有下拉框
    document.querySelectorAll('.dropdown-multiselect.open').forEach(d => {
        d.classList.remove('open');
    });

    // 切换当前下拉框
    if (!isOpen) {
        dropdown.classList.add('open');
    }
}

function toggleDropdownItem(item) {
    item.classList.toggle('selected');
    const dropdown = item.closest('.dropdown-multiselect');
    updateDropdownText(dropdown.id);
}

function getDropdownSelectedValues(dropdownId) {
    const dropdown = document.getElementById(dropdownId);
    const selectedItems = dropdown.querySelectorAll('.dropdown-item.selected');
    return Array.from(selectedItems).map(item => item.dataset.value);
}

function updateDropdownText(dropdownId) {
    const dropdown = document.getElementById(dropdownId);
    const textSpan = dropdown.querySelector('.dropdown-text');
    const selected = getDropdownSelectedValues(dropdownId);

    if (selected.length === 0 || selected.length === 4) {
        textSpan.textContent = t('allStatuses');
    } else {
        const labels = [];
        selected.forEach(val => {
            if (val === 'normal') labels.push(t('statusNormal'));
            else if (val === 'warning') labels.push(t('statusWarning'));
            else if (val === 'danger') labels.push(t('statusDanger'));
            else if (val === 'disabled') labels.push(t('statusDisabled'));
        });
        textSpan.textContent = labels.join(', ');
    }
}

function resetDropdownSelection(dropdownId, defaultValues = ['normal', 'warning', 'danger']) {
    const dropdown = document.getElementById(dropdownId);
    dropdown.querySelectorAll('.dropdown-item').forEach(item => {
        if (defaultValues.includes(item.dataset.value)) {
            item.classList.add('selected');
        } else {
            item.classList.remove('selected');
        }
    });
    updateDropdownText(dropdownId);
}

// 点击页面其他地方关闭下拉框
document.addEventListener('click', function (e) {
    if (!e.target.closest('.dropdown-multiselect')) {
        document.querySelectorAll('.dropdown-multiselect.open').forEach(d => {
            d.classList.remove('open');
        });
    }
});

// ============ 加载分类和产品列表 ============
async function loadCategories() {
    try {
        const response = await fetch(`${API_BASE_URL}/materials/categories`);
        allCategories = await response.json();
        populateCategorySelect();
    } catch (error) {
        console.error('加载分类失败:', error);
    }
}

function populateCategorySelect() {
    const selects = [
        document.getElementById('filter-inventory-category'),
        document.getElementById('filter-records-category')
    ];

    selects.forEach(select => {
        if (!select) return;

        // 保留第一个选项
        const firstOption = select.options[0];
        select.innerHTML = '';
        select.appendChild(firstOption);

        allCategories.forEach(cat => {
            const option = document.createElement('option');
            option.value = cat;
            option.textContent = cat;
            select.appendChild(option);
        });
    });
}

async function loadAllProducts() {
    try {
        // 分页获取所有产品（API限制page_size最大100）
        let page = 1;
        let allItems = [];
        let hasMore = true;

        while (hasMore) {
            const response = await fetch(`${API_BASE_URL}/materials/list?page=${page}&page_size=100&status=normal,warning,danger,disabled`);
            const data = await response.json();
            if (data.items && data.items.length > 0) {
                allItems = allItems.concat(data.items);
                page++;
                hasMore = page <= data.total_pages;
            } else {
                hasMore = false;
            }
        }

        allProducts = allItems;
        populateProductSelector();
    } catch (error) {
        console.error('加载产品列表失败:', error);
        allProducts = [];
    }
}

function populateProductSelector() {
    if (!allProducts || !Array.isArray(allProducts)) {
        allProducts = [];
    }

    // 初始化产品详情页的可搜索选择器
    initSearchableSelect({
        wrapperId: 'product-selector-wrapper',
        inputId: 'product-selector-input',
        dropdownId: 'product-selector-dropdown',
        hiddenId: 'product-selector',
        products: allProducts,
        includeDisabled: true,
        showStock: false,
        onSelect: (productName) => {
            onProductSelect(productName);
        },
        placeholder: t('searchPlaceholder') || '搜索产品名称或编码...'
    });

    // 初始化新增记录弹窗的可搜索选择器
    initSearchableSelect({
        wrapperId: 'record-product-wrapper',
        inputId: 'record-product-input',
        dropdownId: 'record-product-dropdown',
        hiddenId: 'record-product',
        products: allProducts.filter(p => !p.is_disabled),
        includeDisabled: false,
        showStock: true,
        onSelect: null,
        placeholder: t('searchPlaceholder') || '搜索产品名称或编码...'
    });
}

// 初始化可搜索下拉选择器
function initSearchableSelect(config) {
    const wrapper = document.getElementById(config.wrapperId);
    const input = document.getElementById(config.inputId);
    const dropdown = document.getElementById(config.dropdownId);
    const hidden = document.getElementById(config.hiddenId);

    if (!wrapper || !input || !dropdown || !hidden) return;

    // 设置 placeholder
    if (config.placeholder) {
        input.placeholder = config.placeholder;
    }

    // 存储配置到元素上
    wrapper._searchableConfig = config;
    wrapper._highlightIndex = -1;

    // 渲染下拉选项
    renderSearchableOptions(wrapper, '');

    // 移除旧事件监听器（如果有）
    const newInput = input.cloneNode(true);
    input.parentNode.replaceChild(newInput, input);

    // 输入事件 - 过滤选项
    newInput.addEventListener('input', function (e) {
        const query = e.target.value.trim();
        renderSearchableOptions(wrapper, query);
        openSearchableDropdown(wrapper);
        wrapper._highlightIndex = -1;
    });

    // 点击输入框 - 显示下拉
    newInput.addEventListener('focus', function () {
        renderSearchableOptions(wrapper, newInput.value.trim());
        openSearchableDropdown(wrapper);
    });

    // 键盘导航
    newInput.addEventListener('keydown', function (e) {
        const options = dropdown.querySelectorAll('.searchable-select-option');
        const maxIndex = options.length - 1;

        if (e.key === 'ArrowDown') {
            e.preventDefault();
            wrapper._highlightIndex = Math.min(wrapper._highlightIndex + 1, maxIndex);
            updateHighlight(dropdown, wrapper._highlightIndex);
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            wrapper._highlightIndex = Math.max(wrapper._highlightIndex - 1, 0);
            updateHighlight(dropdown, wrapper._highlightIndex);
        } else if (e.key === 'Enter') {
            e.preventDefault();
            if (wrapper._highlightIndex >= 0 && options[wrapper._highlightIndex]) {
                options[wrapper._highlightIndex].click();
            }
        } else if (e.key === 'Escape') {
            closeSearchableDropdown(wrapper);
            newInput.blur();
        }
    });

    // 点击外部关闭下拉
    document.addEventListener('click', function (e) {
        if (!wrapper.contains(e.target)) {
            closeSearchableDropdown(wrapper);
        }
    });
}

// 渲染可搜索选项
function renderSearchableOptions(wrapper, query) {
    const config = wrapper._searchableConfig;
    const dropdown = document.getElementById(config.dropdownId);
    const hidden = document.getElementById(config.hiddenId);

    if (!dropdown || !config.products) return;

    const queryLower = query.toLowerCase();
    const filtered = config.products.filter(p => {
        if (!query) return true;
        return p.name.toLowerCase().includes(queryLower) ||
            p.sku.toLowerCase().includes(queryLower);
    });

    if (filtered.length === 0) {
        dropdown.innerHTML = `<div class="searchable-select-empty">${t('noData') || '暂无数据'}</div>`;
        return;
    }

    dropdown.innerHTML = filtered.map((product, idx) => {
        const isSelected = hidden.value === product.name;
        const isDisabled = product.is_disabled;
        const classes = ['searchable-select-option'];
        if (isSelected) classes.push('selected');
        if (isDisabled) classes.push('disabled-product');

        // 高亮匹配文本
        let nameHtml = escapeHtml(product.name);
        let skuHtml = escapeHtml(product.sku);
        if (query) {
            nameHtml = highlightMatch(product.name, query);
            skuHtml = highlightMatch(product.sku, query);
        }

        let stockHtml = '';
        if (config.showStock) {
            stockHtml = `<span class="option-stock">${t('currentStockCol') || '库存'}: ${product.quantity}</span>`;
        }

        return `
            <div class="${classes.join(' ')}" data-value="${escapeHtml(product.name)}" data-index="${idx}">
                <div class="option-name">${nameHtml} ${stockHtml}</div>
                <div class="option-info"><span class="option-sku">SKU: ${skuHtml}</span></div>
            </div>
        `;
    }).join('');

    // 绑定点击事件
    dropdown.querySelectorAll('.searchable-select-option').forEach(opt => {
        opt.addEventListener('click', function () {
            const value = this.dataset.value;
            selectSearchableOption(wrapper, value);
        });
    });
}

// 选择选项
function selectSearchableOption(wrapper, value) {
    const config = wrapper._searchableConfig;
    const input = document.getElementById(config.inputId);
    const hidden = document.getElementById(config.hiddenId);

    // 找到对应的产品
    const product = config.products.find(p => p.name === value);
    if (!product) return;

    // 设置值
    hidden.value = value;
    input.value = `${product.name} (${product.sku})`;

    // 更新外观
    wrapper.classList.add('has-value');
    closeSearchableDropdown(wrapper);

    // 调用回调
    if (config.onSelect) {
        config.onSelect(value);
    }
}

// 打开下拉
function openSearchableDropdown(wrapper) {
    wrapper.classList.add('open');
}

// 关闭下拉
function closeSearchableDropdown(wrapper) {
    wrapper.classList.remove('open');
    wrapper._highlightIndex = -1;
}

// 更新高亮
function updateHighlight(dropdown, index) {
    dropdown.querySelectorAll('.searchable-select-option').forEach((opt, i) => {
        opt.classList.toggle('highlighted', i === index);
        if (i === index) {
            opt.scrollIntoView({ block: 'nearest' });
        }
    });
}

// 高亮匹配文本
function highlightMatch(text, query) {
    if (!query) return escapeHtml(text);
    const regex = new RegExp(`(${escapeRegExp(query)})`, 'gi');
    return escapeHtml(text).replace(regex, '<span class="searchable-select-highlight">$1</span>');
}

// 转义正则特殊字符
function escapeRegExp(string) {
    return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// 转义 HTML
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// 清除产品详情选择器
function clearProductSelector() {
    const wrapper = document.getElementById('product-selector-wrapper');
    const input = document.getElementById('product-selector-input');
    const hidden = document.getElementById('product-selector');

    if (input) input.value = '';
    if (hidden) hidden.value = '';
    if (wrapper) wrapper.classList.remove('has-value');

    onProductSelect('');
}

// 清除新增记录产品选择器
function clearRecordProductSelector() {
    const wrapper = document.getElementById('record-product-wrapper');
    const input = document.getElementById('record-product-input');
    const hidden = document.getElementById('record-product');

    if (input) input.value = '';
    if (hidden) hidden.value = '';
    if (wrapper) wrapper.classList.remove('has-value');
}

// 设置产品选择器的值（用于外部调用）
function setProductSelectorValue(productName) {
    const wrapper = document.getElementById('product-selector-wrapper');
    if (!wrapper || !wrapper._searchableConfig) return;

    if (productName) {
        selectSearchableOption(wrapper, productName);
    } else {
        clearProductSelector();
    }
}

// ============ Dashboard 数据加载 ============
async function loadDashboardData() {
    try {
        await Promise.all([
            loadDashboardStats(),
            loadCategoryDistribution(),
            loadWeeklyTrend(),
            loadTopStock()
        ]);
    } catch (error) {
        console.error('加载Dashboard数据失败:', error);
    }
}

async function loadDashboardStats() {
    const response = await fetch(`${API_BASE_URL}/dashboard/stats`);
    const data = await response.json();

    document.getElementById('total-stock').textContent = data.total_stock.toLocaleString();
    document.getElementById('today-in').textContent = data.today_in.toLocaleString();
    document.getElementById('today-out').textContent = data.today_out.toLocaleString();
    document.getElementById('low-stock-count').textContent = data.low_stock_count;

    const inChange = document.getElementById('in-change');
    inChange.textContent = (data.in_change >= 0 ? '+' : '') + data.in_change + '%';
    inChange.className = data.in_change >= 0 ? 'stat-change positive' : 'stat-change negative';

    const outChange = document.getElementById('out-change');
    outChange.textContent = (data.out_change >= 0 ? '+' : '') + data.out_change + '%';
    outChange.className = data.out_change >= 0 ? 'stat-change positive' : 'stat-change negative';
}

async function loadCategoryDistribution() {
    const response = await fetch(`${API_BASE_URL}/dashboard/category-distribution`);
    const data = await response.json();

    const option = {
        tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
        legend: { orient: 'vertical', right: 10, top: 'center', textStyle: { fontSize: 12 } },
        series: [{
            name: '库存分布',
            type: 'pie',
            radius: ['40%', '70%'],
            avoidLabelOverlap: false,
            itemStyle: { borderRadius: 10, borderColor: '#fff', borderWidth: 2 },
            label: { show: false },
            emphasis: { label: { show: true, fontSize: 14, fontWeight: 'bold' } },
            labelLine: { show: false },
            data: data,
            color: ['#5470c6', '#91cc75', '#fac858', '#ee6666', '#73c0de', '#3ba272', '#fc8452', '#9a60b4']
        }]
    };

    categoryChart.setOption(option);
}

async function loadWeeklyTrend() {
    const response = await fetch(`${API_BASE_URL}/dashboard/weekly-trend`);
    const data = await response.json();

    const option = {
        tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
        legend: { data: [t('inbound'), t('outbound')], textStyle: { fontSize: 12 } },
        grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
        xAxis: {
            type: 'category',
            boundaryGap: false,
            data: data.dates,
            axisLine: { lineStyle: { color: '#ccc' } },
            axisLabel: { color: '#666' }
        },
        yAxis: {
            type: 'value',
            axisLine: { lineStyle: { color: '#ccc' } },
            axisLabel: { color: '#666' },
            splitLine: { lineStyle: { color: '#eee' } }
        },
        series: [
            {
                name: t('inbound'),
                type: 'line',
                smooth: true,
                data: data.in_data,
                itemStyle: { color: '#5470c6' },
                areaStyle: {
                    color: {
                        type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
                        colorStops: [{ offset: 0, color: 'rgba(84, 112, 198, 0.3)' }, { offset: 1, color: 'rgba(84, 112, 198, 0.05)' }]
                    }
                }
            },
            {
                name: t('outbound'),
                type: 'line',
                smooth: true,
                data: data.out_data,
                itemStyle: { color: '#ee6666' },
                areaStyle: {
                    color: {
                        type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
                        colorStops: [{ offset: 0, color: 'rgba(238, 102, 102, 0.3)' }, { offset: 1, color: 'rgba(238, 102, 102, 0.05)' }]
                    }
                }
            }
        ]
    };

    trendChart.setOption(option, true);
}

async function loadTopStock() {
    const response = await fetch(`${API_BASE_URL}/dashboard/top-stock`);
    const data = await response.json();

    const option = {
        tooltip: {
            trigger: 'axis',
            axisPointer: { type: 'shadow' },
            formatter: function (params) {
                const index = params[0].dataIndex;
                return `${data.names[index]}<br/>类型: ${data.categories[index]}<br/>库存: ${params[0].value}`;
            }
        },
        grid: { left: '3%', right: '4%', bottom: '3%', top: '3%', containLabel: true },
        xAxis: {
            type: 'value',
            axisLine: { lineStyle: { color: '#ccc' } },
            axisLabel: { color: '#666' },
            splitLine: { lineStyle: { color: '#eee' } }
        },
        yAxis: {
            type: 'category',
            data: data.names.map(name => name.length > 12 ? name.substring(0, 12) + '...' : name),
            axisLine: { lineStyle: { color: '#ccc' } },
            axisLabel: { color: '#666' }
        },
        series: [{
            type: 'bar',
            data: data.quantities,
            itemStyle: {
                color: {
                    type: 'linear', x: 0, y: 0, x2: 1, y2: 0,
                    colorStops: [{ offset: 0, color: '#667eea' }, { offset: 1, color: '#764ba2' }]
                },
                borderRadius: [0, 4, 4, 0]
            },
            barWidth: '60%'
        }]
    };

    topStockChart.setOption(option);
}

// ============ 库存列表 ============
async function loadInventory() {
    const name = document.getElementById('filter-inventory-name').value.trim();
    const category = document.getElementById('filter-inventory-category').value;
    const selectedStatuses = getDropdownSelectedValues('filter-inventory-status-dropdown');

    const params = new URLSearchParams({
        page: inventoryCurrentPage,
        page_size: inventoryPageSize
    });

    if (name) params.set('name', name);
    if (category) params.set('category', category);
    if (selectedStatuses.length > 0) {
        params.set('status', selectedStatuses.join(','));
    }

    try {
        const response = await fetch(`${API_BASE_URL}/materials/list?${params}`);
        const data = await response.json();

        renderInventoryTable(data.items);
        updateInventoryPagination(data);
    } catch (error) {
        console.error('加载库存列表失败:', error);
    }
}

function renderInventoryTable(items) {
    const tbody = document.getElementById('inventory-tbody');
    tbody.innerHTML = '';

    if (items.length === 0) {
        tbody.innerHTML = `<tr><td colspan="8" style="text-align: center; color: #999;">${t('noData')}</td></tr>`;
        return;
    }

    items.forEach(item => {
        const tr = document.createElement('tr');
        tr.className = 'clickable';

        let statusText = '', statusClass = '';
        if (item.is_disabled) {
            statusText = t('statusDisabled');
            statusClass = 'status-disabled';
        } else if (item.status === 'normal') {
            statusText = t('statusNormal');
            statusClass = 'status-normal';
        } else if (item.status === 'warning') {
            statusText = t('statusWarning');
            statusClass = 'status-warning';
        } else {
            statusText = t('statusDanger');
            statusClass = 'status-danger';
        }

        tr.innerHTML = `
            <td>${item.name}</td>
            <td>${item.sku}</td>
            <td>${item.category}</td>
            <td><strong>${item.quantity}</strong></td>
            <td>${item.unit}</td>
            <td>${item.safe_stock}</td>
            <td><span class="status-badge ${statusClass}">${statusText}</span></td>
            <td>${item.location}</td>
        `;

        tr.addEventListener('click', function () {
            switchTab('detail', { product: item.name });
        });

        tbody.appendChild(tr);
    });
}

function updateInventoryPagination(data) {
    inventoryTotalPages = data.total_pages;
    document.getElementById('inventory-total').textContent = data.total;
    document.getElementById('inventory-current-page').textContent = data.page;
    document.getElementById('inventory-total-pages').textContent = data.total_pages;
    document.getElementById('inventory-prev-btn').disabled = data.page <= 1;
    document.getElementById('inventory-next-btn').disabled = data.page >= data.total_pages;
}

function inventoryGoToPage(page) {
    if (page < 1 || page > inventoryTotalPages) return;
    inventoryCurrentPage = page;
    loadInventory();
}

function changeInventoryPageSize(size) {
    inventoryPageSize = parseInt(size);
    inventoryCurrentPage = 1; // Reset to first page
    loadInventory();
}

function applyInventoryFilter() {
    inventoryCurrentPage = 1;
    loadInventory();
}

function resetInventoryFilter() {
    document.getElementById('filter-inventory-name').value = '';
    document.getElementById('filter-inventory-category').value = '';
    // 重置状态多选：选中除禁用外的所有选项
    resetDropdownSelection('filter-inventory-status-dropdown');
    inventoryCurrentPage = 1;
    loadInventory();
}

// ============ 进出库记录 ============
async function loadRecords() {
    const startDate = document.getElementById('filter-start-date').value;
    const endDate = document.getElementById('filter-end-date').value;
    const productName = document.getElementById('filter-records-product').value.trim();
    const category = document.getElementById('filter-records-category').value;
    const recordType = document.getElementById('filter-record-type').value;
    const selectedStatuses = getDropdownSelectedValues('filter-record-status-dropdown');
    const contactId = document.getElementById('filter-record-contact').value;
    const operatorUserId = document.getElementById('filter-record-operator').value;
    const reason = document.getElementById('filter-record-reason').value.trim();

    const params = new URLSearchParams({
        page: recordsCurrentPage,
        page_size: recordsPageSize
    });

    if (startDate) params.set('start_date', startDate);
    if (endDate) params.set('end_date', endDate);
    if (productName) params.set('product_name', productName);
    if (category) params.set('category', category);
    if (recordType) params.set('record_type', recordType);
    if (selectedStatuses.length > 0) {
        params.set('status', selectedStatuses.join(','));
    }
    if (contactId) params.set('contact_id', contactId);
    if (operatorUserId) params.set('operator_user_id', operatorUserId);
    if (reason) params.set('reason', reason);

    try {
        const response = await fetch(`${API_BASE_URL}/inventory/records?${params}`);
        const data = await response.json();

        renderRecordsTable(data.items);
        updateRecordsPagination(data);
    } catch (error) {
        console.error('加载进出库记录失败:', error);
    }
}

function renderRecordsTable(items) {
    const tbody = document.getElementById('records-tbody');
    tbody.innerHTML = '';

    if (items.length === 0) {
        tbody.innerHTML = `<tr><td colspan="11" style="text-align: center; color: #999;">${t('noRecords')}</td></tr>`;
        return;
    }

    items.forEach(item => {
        const tr = document.createElement('tr');

        const typeText = item.type === 'in' ? t('inbound') : t('outbound');
        const typeClass = item.type === 'in' ? 'type-in' : 'type-out';

        let statusText = '', statusClass = '';
        if (item.is_disabled) {
            statusText = t('statusDisabled');
            statusClass = 'status-disabled';
        } else if (item.material_status === 'normal') {
            statusText = t('statusNormal');
            statusClass = 'status-normal';
        } else if (item.material_status === 'warning') {
            statusText = t('statusWarning');
            statusClass = 'status-warning';
        } else {
            statusText = t('statusDanger');
            statusClass = 'status-danger';
        }

        // 批次信息：入库显示批次号，出库显示消耗详情
        let batchDisplay = '-';
        if (item.type === 'in' && item.batch_no) {
            batchDisplay = item.batch_no;
        } else if (item.type === 'out' && item.batch_details) {
            batchDisplay = `<span class="batch-details" title="${item.batch_details}">${item.batch_details}</span>`;
        }

        tr.innerHTML = `
            <td>${item.created_at}</td>
            <td>${item.material_name}</td>
            <td>${item.material_sku}</td>
            <td>${item.category || '-'}</td>
            <td><span class="type-badge ${typeClass}">${typeText}</span></td>
            <td><strong>${item.quantity}</strong></td>
            <td>${batchDisplay}</td>
            <td>${item.contact_name || '-'}</td>
            <td>${item.operator_name || item.operator}</td>
            <td>${item.reason || '-'}</td>
            <td><span class="status-badge ${statusClass}">${statusText}</span></td>
        `;

        tbody.appendChild(tr);
    });
}

function updateRecordsPagination(data) {
    recordsTotalPages = data.total_pages;
    document.getElementById('records-total').textContent = data.total;
    document.getElementById('records-current-page').textContent = data.page;
    document.getElementById('records-total-pages').textContent = data.total_pages;
    document.getElementById('records-prev-btn').disabled = data.page <= 1;
    document.getElementById('records-next-btn').disabled = data.page >= data.total_pages;
}

function recordsGoToPage(page) {
    if (page < 1 || page > recordsTotalPages) return;
    recordsCurrentPage = page;
    loadRecords();
}

function changeRecordsPageSize(size) {
    recordsPageSize = parseInt(size);
    recordsCurrentPage = 1;
    loadRecords();
}

function applyRecordsFilter() {
    recordsCurrentPage = 1;
    loadRecords();
}

function resetRecordsFilter() {
    document.getElementById('filter-start-date').value = '';
    document.getElementById('filter-end-date').value = '';
    document.getElementById('filter-records-product').value = '';
    document.getElementById('filter-records-category').value = '';
    document.getElementById('filter-record-type').value = '';
    document.getElementById('filter-record-contact').value = '';
    document.getElementById('filter-record-operator').value = '';
    document.getElementById('filter-record-reason').value = '';
    // 重置状态多选：选中除禁用外的所有选项
    resetDropdownSelection('filter-record-status-dropdown');
    recordsCurrentPage = 1;
    loadRecords();
}

// 加载记录筛选下拉选项（联系方和操作员）
async function loadRecordsFilterOptions() {
    try {
        // 并行加载联系方和操作员
        const [contactsRes, operatorsRes] = await Promise.all([
            fetch(`${API_BASE_URL}/contacts?page_size=100`, { credentials: 'include' }),
            fetch(`${API_BASE_URL}/operators`, { credentials: 'include' })
        ]);

        // 加载联系方选项
        if (contactsRes.ok) {
            const contactsData = await contactsRes.json();
            const contactSelect = document.getElementById('filter-record-contact');
            contactSelect.innerHTML = `<option value="" data-i18n="allContacts">${t('allContacts')}</option>`;
            contactsData.items.forEach(contact => {
                const option = document.createElement('option');
                option.value = contact.id;
                option.textContent = contact.name;
                contactSelect.appendChild(option);
            });
            console.log(`加载了 ${contactsData.items.length} 个联系方`);
        } else {
            console.error('加载联系方失败:', contactsRes.status);
        }

        // 加载操作员选项
        if (operatorsRes.ok) {
            const operators = await operatorsRes.json();
            const operatorSelect = document.getElementById('filter-record-operator');
            operatorSelect.innerHTML = `<option value="" data-i18n="allOperators">${t('allOperators')}</option>`;
            operators.forEach(op => {
                const option = document.createElement('option');
                option.value = op.user_id;
                option.textContent = op.display_name || op.username;
                operatorSelect.appendChild(option);
            });
            console.log(`加载了 ${operators.length} 个操作员`);
        } else {
            console.error('加载操作员失败:', operatorsRes.status);
        }
    } catch (error) {
        console.error('加载筛选选项失败:', error);
    }
}

// ============ 产品详情 ============
function onProductSelect(productName) {
    if (!productName) {
        document.getElementById('product-detail-content').style.display = 'none';
        document.getElementById('no-product-selected').style.display = 'flex';
        currentProductName = '';
        return;
    }

    currentProductName = productName;
    document.getElementById('product-detail-content').style.display = 'block';
    document.getElementById('no-product-selected').style.display = 'none';

    initDetailCharts();
    loadProductDetail();
}

async function loadProductDetail() {
    if (!currentProductName) return;

    try {
        await Promise.all([
            loadProductStats(),
            loadProductTrend(),
            loadProductRecords()
        ]);
    } catch (error) {
        console.error('加载产品详情失败:', error);
    }
}

async function loadProductStats() {
    const response = await fetch(`${API_BASE_URL}/materials/product-stats?name=${encodeURIComponent(currentProductName)}`);
    const data = await response.json();

    if (data.error) {
        alert(data.error);
        return;
    }

    lastProductStats = data;

    document.getElementById('detail-current-stock').textContent = data.current_stock.toLocaleString();
    document.getElementById('detail-stock-unit').textContent = data.unit;
    document.getElementById('detail-today-in').textContent = data.today_in.toLocaleString();
    document.getElementById('detail-today-out').textContent = data.today_out.toLocaleString();
    document.getElementById('detail-safe-stock').textContent = data.safe_stock.toLocaleString();

    const inChange = document.getElementById('detail-in-change');
    inChange.textContent = (data.in_change >= 0 ? '+' : '') + data.in_change + '%';
    inChange.className = data.in_change >= 0 ? 'stat-change positive' : 'stat-change negative';

    const outChange = document.getElementById('detail-out-change');
    outChange.textContent = (data.out_change >= 0 ? '+' : '') + data.out_change + '%';
    outChange.className = data.out_change >= 0 ? 'stat-change positive' : 'stat-change negative';

    // 更新库存状态
    const statusElem = document.getElementById('detail-stock-status');
    if (data.current_stock >= data.safe_stock) {
        statusElem.textContent = t('statusNormal');
        statusElem.style.color = '#52c41a';
    } else if (data.current_stock >= data.safe_stock * 0.5) {
        statusElem.textContent = t('statusWarning');
        statusElem.style.color = '#faad14';
    } else {
        statusElem.textContent = t('statusDanger');
        statusElem.style.color = '#f5222d';
    }

    // 更新饼图
    loadDetailPieChart(data.total_in, data.total_out);
}

async function loadProductTrend() {
    const response = await fetch(`${API_BASE_URL}/materials/product-trend?name=${encodeURIComponent(currentProductName)}`);
    const data = await response.json();

    const option = {
        tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
        legend: { data: [t('inbound'), t('outbound')], textStyle: { fontSize: 12 } },
        grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
        xAxis: {
            type: 'category',
            boundaryGap: false,
            data: data.dates,
            axisLine: { lineStyle: { color: '#ccc' } },
            axisLabel: { color: '#666' }
        },
        yAxis: {
            type: 'value',
            axisLine: { lineStyle: { color: '#ccc' } },
            axisLabel: { color: '#666' },
            splitLine: { lineStyle: { color: '#eee' } }
        },
        series: [
            {
                name: t('inbound'),
                type: 'line',
                smooth: true,
                data: data.in_data,
                itemStyle: { color: '#5470c6' },
                areaStyle: {
                    color: {
                        type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
                        colorStops: [{ offset: 0, color: 'rgba(84, 112, 198, 0.3)' }, { offset: 1, color: 'rgba(84, 112, 198, 0.05)' }]
                    }
                }
            },
            {
                name: t('outbound'),
                type: 'line',
                smooth: true,
                data: data.out_data,
                itemStyle: { color: '#ee6666' },
                areaStyle: {
                    color: {
                        type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
                        colorStops: [{ offset: 0, color: 'rgba(238, 102, 102, 0.3)' }, { offset: 1, color: 'rgba(238, 102, 102, 0.05)' }]
                    }
                }
            }
        ]
    };

    detailTrendChart.setOption(option, true);
}

function loadDetailPieChart(totalIn, totalOut) {
    const option = {
        tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
        legend: { orient: 'vertical', right: 10, top: 'center', textStyle: { fontSize: 12 } },
        series: [{
            name: t('inOutRatio'),
            type: 'pie',
            radius: ['40%', '70%'],
            avoidLabelOverlap: false,
            itemStyle: { borderRadius: 10, borderColor: '#fff', borderWidth: 2 },
            label: { show: false },
            emphasis: { label: { show: true, fontSize: 14, fontWeight: 'bold' } },
            labelLine: { show: false },
            data: [
                { value: totalIn, name: t('inbound'), itemStyle: { color: '#5470c6' } },
                { value: totalOut, name: t('outbound'), itemStyle: { color: '#ee6666' } }
            ]
        }]
    };

    detailPieChart.setOption(option, true);
}

async function loadProductRecords() {
    const params = new URLSearchParams({
        name: currentProductName,
        page: detailCurrentPage,
        page_size: detailPageSize
    });

    const response = await fetch(`${API_BASE_URL}/materials/product-records?${params}`);
    const data = await response.json();

    renderDetailRecordsTable(data.items);
    updateDetailPagination(data);
}

function renderDetailRecordsTable(items) {
    const tbody = document.getElementById('detail-records-tbody');
    tbody.innerHTML = '';

    if (items.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: #999;">${t('noRecords')}</td></tr>`;
        return;
    }

    items.forEach(item => {
        const tr = document.createElement('tr');

        const typeText = item.type === 'in' ? t('inbound') : t('outbound');
        const typeClass = item.type === 'in' ? 'type-in' : 'type-out';

        tr.innerHTML = `
            <td>${item.created_at}</td>
            <td><span class="type-badge ${typeClass}">${typeText}</span></td>
            <td><strong>${item.quantity}</strong></td>
            <td>${item.operator}</td>
            <td>${item.reason || '-'}</td>
        `;

        tbody.appendChild(tr);
    });
}

function updateDetailPagination(data) {
    detailTotalPages = data.total_pages;
    document.getElementById('detail-total').textContent = data.total;
    document.getElementById('detail-current-page').textContent = data.page;
    document.getElementById('detail-total-pages').textContent = data.total_pages;
    document.getElementById('detail-prev-btn').disabled = data.page <= 1;
    document.getElementById('detail-next-btn').disabled = data.page >= data.total_pages;
}

function detailGoToPage(page) {
    if (page < 1 || page > detailTotalPages) return;
    detailCurrentPage = page;
    loadProductRecords();
}

function changeDetailPageSize(size) {
    detailPageSize = parseInt(size);
    detailCurrentPage = 1;
    loadProductRecords();
}

// ============ 导出功能 ============
function exportInventory() {
    const name = document.getElementById('filter-inventory-name').value.trim();
    const category = document.getElementById('filter-inventory-category').value;

    // 获取选中的状态
    const statusContainer = document.getElementById('filter-inventory-status-dropdown');
    const selectedStatuses = [];
    if (statusContainer) {
        statusContainer.querySelectorAll('.dropdown-item.selected').forEach(item => {
            const value = item.getAttribute('data-value');
            if (value) selectedStatuses.push(value);
        });
    }

    const params = new URLSearchParams();
    if (name) params.set('name', name);
    if (category) params.set('category', category);
    if (selectedStatuses.length > 0) params.set('status', selectedStatuses.join(','));

    window.location.href = `${API_BASE_URL}/materials/export-excel?${params}`;
}

function exportRecords() {
    const startDate = document.getElementById('filter-start-date').value;
    const endDate = document.getElementById('filter-end-date').value;
    const productName = document.getElementById('filter-records-product').value.trim();
    const type = document.getElementById('filter-record-type').value;

    const params = new URLSearchParams();
    if (startDate) params.set('start_date', startDate);
    if (endDate) params.set('end_date', endDate);
    if (productName) params.set('product_name', productName);
    if (type) params.set('record_type', type);

    window.location.href = `${API_BASE_URL}/inventory/export-excel?${params}`;
}

function exportProductRecords() {
    if (!currentProductName) return;
    window.location.href = `${API_BASE_URL}/inventory/export-excel?product_name=${encodeURIComponent(currentProductName)}`;
}

// ============ Excel 导入功能 ============
let importPreviewData = null;
let pendingNewSkus = [];

function showImportModal() {
    document.getElementById('import-modal').classList.add('show');
    document.getElementById('preview-area').style.display = 'none';
    document.getElementById('excel-file').value = '';
    document.getElementById('confirm-import-btn').disabled = true;
    importPreviewData = null;
    pendingNewSkus = [];
    const disableCheckbox = document.getElementById('confirm-disable-missing');
    if (disableCheckbox) disableCheckbox.checked = false;
}

function closeImportModal() {
    document.getElementById('import-modal').classList.remove('show');
    importPreviewData = null;
    pendingNewSkus = [];
}

async function handleFileSelect(event) {
    const file = event.target.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('file', file);

    try {
        const response = await fetch(`${API_BASE_URL}/materials/import-excel/preview`, {
            method: 'POST',
            body: formData
        });
        const data = await response.json();

        if (data.success) {
            importPreviewData = data;
            pendingNewSkus = data.new_skus || [];
            renderImportPreview(data);
        } else {
            alert(data.message || t('parseFileFailed'));
        }
    } catch (error) {
        console.error('预览失败:', error);
        alert(t('previewFailed'));
    }
}

function renderImportPreview(data) {
    document.getElementById('preview-area').style.display = 'block';
    document.getElementById('preview-in').textContent = data.total_in;
    document.getElementById('preview-out').textContent = data.total_out;
    document.getElementById('preview-new').textContent = data.total_new;

    // 判断是否有实际变化（处理可能的 undefined 或字符串值）
    const totalIn = parseInt(data.total_in) || 0;
    const totalOut = parseInt(data.total_out) || 0;
    const totalNew = parseInt(data.total_new) || 0;
    const hasChanges = totalIn > 0 || totalOut > 0 || totalNew > 0;

    // 存储 hasChanges 状态供 confirmImport 使用
    data._hasChanges = hasChanges;

    // 根据是否有变化显示/隐藏原因输入和禁用SKU选项
    const reasonRow = document.getElementById('import-reason').closest('.form-row');
    const disableRow = document.getElementById('confirm-disable-missing').closest('.form-row');

    if (hasChanges) {
        reasonRow.style.display = '';
        disableRow.style.display = '';
    } else {
        reasonRow.style.display = 'none';
        disableRow.style.display = 'none';
    }

    const tbody = document.getElementById('preview-tbody');
    tbody.innerHTML = '';

    data.preview.forEach(item => {
        const tr = document.createElement('tr');

        let opText = '', opClass = '';
        if (item.operation === 'in') {
            opText = t('inbound');
            opClass = 'type-in';
        } else if (item.operation === 'out') {
            opText = t('outbound');
            opClass = 'type-out';
        } else if (item.operation === 'new') {
            opText = t('newMaterial');
            opClass = 'type-new';
        } else {
            opText = t('noChange');
            opClass = 'type-none';
        }

        const currentQty = item.current_quantity !== null ? item.current_quantity : '-';
        const diffDisplay = item.difference > 0 ? `+${item.difference}` : item.difference;

        tr.innerHTML = `
            <td>${item.sku}</td>
            <td>${item.name}</td>
            <td>${currentQty}</td>
            <td>${item.import_quantity}</td>
            <td class="${item.difference > 0 ? 'diff-positive' : item.difference < 0 ? 'diff-negative' : ''}">${diffDisplay}</td>
            <td><span class="type-badge ${opClass}">${opText}</span></td>
        `;
        tbody.appendChild(tr);
    });

    document.getElementById('confirm-import-btn').disabled = false;
}

async function confirmImport() {
    if (!importPreviewData) return;

    // 使用预览时计算的 hasChanges 状态
    const hasChanges = importPreviewData._hasChanges;

    // 如果没有变化，直接关闭模态框
    if (!hasChanges) {
        alert(t('noChangesToImport') || '数据无变化，无需导入');
        closeImportModal();
        return;
    }

    const reason = document.getElementById('import-reason').value.trim();

    if (!reason) {
        alert(t('fillAllFields'));
        return;
    }

    if (pendingNewSkus.length > 0) {
        showNewSkuModal();
        return;
    }

    await executeImport(false);
}

function showNewSkuModal() {
    const list = document.getElementById('new-sku-list');
    list.innerHTML = '';

    pendingNewSkus.forEach(item => {
        const div = document.createElement('div');
        div.className = 'new-sku-item';
        div.innerHTML = `
            <span class="sku">${item.sku}</span>
            <span class="name">${item.name}</span>
            <span class="qty">${t('quantity')}: ${item.import_quantity}</span>
        `;
        list.appendChild(div);
    });

    document.getElementById('new-sku-modal').classList.add('show');
}

function closeNewSkuModal() {
    document.getElementById('new-sku-modal').classList.remove('show');
}

async function skipNewSkus() {
    closeNewSkuModal();
    await executeImport(false);
}

async function confirmNewSkus() {
    closeNewSkuModal();
    await executeImport(true);
}

async function executeImport(confirmNewSkus) {
    const reason = document.getElementById('import-reason').value.trim();
    const confirmDisableMissing = document.getElementById('confirm-disable-missing')?.checked || false;

    try {
        const response = await fetch(`${API_BASE_URL}/materials/import-excel/confirm`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({
                changes: importPreviewData.preview,
                reason: reason,
                confirm_new_skus: confirmNewSkus,
                confirm_disable_missing_skus: confirmDisableMissing
            })
        });

        const data = await response.json();

        if (data.success) {
            alert(data.message);
            closeImportModal();
            loadAllProducts(); // 刷新产品列表
            loadCategories();  // 刷新分类列表
            if (currentTab === 'inventory') loadInventory();
            if (currentTab === 'dashboard') loadDashboardData();
        } else {
            alert(data.message || t('importFailed'));
        }
    } catch (error) {
        console.error('导入失败:', error);
        alert(t('importFailed'));
    }
}

// ============ 新增记录功能 ============
let addRecordForProduct = false;

function showAddRecordModal() {
    addRecordForProduct = false;
    document.getElementById('record-product-group').style.display = 'block';
    document.getElementById('record-product-display-group').style.display = 'none';
    document.getElementById('add-record-modal').classList.add('show');
    document.getElementById('add-record-form').reset();
    // 清理可搜索选择器状态
    clearRecordProductSelector();
    loadContactsForRecord('in');  // 默认入库，加载供应商
    // 监听类型变化
    document.querySelectorAll('input[name="record-type"]').forEach(radio => {
        radio.onchange = () => loadContactsForRecord(radio.value);
    });
}

function showAddRecordModalForProduct() {
    if (!currentProductName) return;
    addRecordForProduct = true;
    document.getElementById('record-product-group').style.display = 'none';
    document.getElementById('record-product-display-group').style.display = 'block';
    document.getElementById('record-product-display').value = currentProductName;
    document.getElementById('add-record-modal').classList.add('show');
    document.getElementById('add-record-form').reset();
    document.getElementById('record-product-display').value = currentProductName;
    loadContactsForRecord('in');  // 默认入库，加载供应商
    // 监听类型变化
    document.querySelectorAll('input[name="record-type"]').forEach(radio => {
        radio.onchange = () => loadContactsForRecord(radio.value);
    });
}

// 加载联系方下拉列表
async function loadContactsForRecord(recordType) {
    const select = document.getElementById('record-contact');
    if (!select) return;

    const endpoint = recordType === 'in' ? '/contacts/suppliers' : '/contacts/customers';

    try {
        const response = await fetch(`${API_BASE_URL}${endpoint}`, {
            credentials: 'include'
        });

        if (response.ok) {
            const contacts = await response.json();
            select.innerHTML = `<option value="">${t('pleaseSelect')}</option>`;
            contacts.forEach(contact => {
                select.innerHTML += `<option value="${contact.id}">${contact.name}</option>`;
            });
        }
    } catch (error) {
        console.error('加载联系方列表失败:', error);
    }
}

function closeAddRecordModal() {
    document.getElementById('add-record-modal').classList.remove('show');
    document.getElementById('add-record-form').reset();
    // 清理可搜索选择器状态
    clearRecordProductSelector();
}

async function submitAddRecord() {
    const productName = addRecordForProduct
        ? currentProductName
        : document.getElementById('record-product').value;
    const type = document.querySelector('input[name="record-type"]:checked')?.value;
    const quantity = parseInt(document.getElementById('record-quantity').value);
    const reason = document.getElementById('record-reason').value.trim();
    const contactId = document.getElementById('record-contact')?.value || null;

    // 必填项校验
    if (!productName || !type || !document.getElementById('record-quantity').value || !reason) {
        alert(t('fillAllFields')); // 请填写所有必填项
        return;
    }

    if (isNaN(quantity) || quantity <= 0) {
        alert(t('quantityMustBePositive'));
        return;
    }

    try {
        const response = await fetch(`${API_BASE_URL}/inventory/add-record`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({
                product_name: productName,
                type: type,
                quantity: quantity,
                reason: reason,
                contact_id: contactId ? parseInt(contactId) : null
            })
        });

        const data = await response.json();

        if (data.success) {
            alert(data.message);
            closeAddRecordModal();
            loadAllProducts();
            if (currentTab === 'records') loadRecords();
            if (currentTab === 'inventory') loadInventory();
            if (currentTab === 'detail' && currentProductName) loadProductDetail();
            if (currentTab === 'dashboard') loadDashboardData();
        } else {
            alert(data.error || data.message || t('operationFailed'));
        }
    } catch (error) {
        console.error('操作失败:', error);
        alert(t('operationFailed'));
    }
}

// ============ 语言变更回调 ============
function onLanguageChange() {
    document.title = t('pageTitle');
    populateProductSelector();
    populateCategorySelect();

    // 重新渲染当前Tab
    switch (currentTab) {
        case 'dashboard':
            loadWeeklyTrend();
            break;
        case 'records':
            loadRecords();
            break;
        case 'inventory':
            loadInventory();
            break;
        case 'detail':
            if (currentProductName) {
                loadProductTrend();
                loadProductRecords();
                if (lastProductStats) {
                    loadDetailPieChart(lastProductStats.total_in, lastProductStats.total_out);
                    // 更新状态文本
                    const statusElem = document.getElementById('detail-stock-status');
                    if (lastProductStats.current_stock >= lastProductStats.safe_stock) {
                        statusElem.textContent = t('statusNormal');
                    } else if (lastProductStats.current_stock >= lastProductStats.safe_stock * 0.5) {
                        statusElem.textContent = t('statusWarning');
                    } else {
                        statusElem.textContent = t('statusDanger');
                    }
                }
            }
            break;
        case 'users':
            loadUsers();
            loadApiKeys();
            break;
    }
}

// ============ 认证相关 ============

// 检查认证状态
async function checkAuthStatus() {
    try {
        const response = await fetch(`${API_BASE_URL}/auth/status`, {
            credentials: 'include'
        });
        const data = await response.json();

        isSystemInitialized = data.initialized;

        if (!data.initialized) {
            // 系统未初始化，显示设置模态框
            showSetupModal();
            return;
        }

        if (data.logged_in && data.user) {
            currentUser = data.user;
        } else {
            currentUser = null;
        }

        updateUserDisplay();
        updatePermissionUI();
    } catch (error) {
        console.error('检查认证状态失败:', error);
        currentUser = null;
        updateUserDisplay();
    }
}

// 更新用户显示
function updateUserDisplay() {
    const nameDisplay = document.getElementById('user-name-display');
    const roleBadge = document.getElementById('user-role-badge');
    const loginBtn = document.getElementById('login-btn');
    const logoutBtn = document.getElementById('logout-btn');

    if (currentUser) {
        nameDisplay.textContent = currentUser.display_name || currentUser.username;
        roleBadge.textContent = t('role' + currentUser.role.charAt(0).toUpperCase() + currentUser.role.slice(1));
        roleBadge.className = 'user-role-badge ' + currentUser.role;
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
function updatePermissionUI() {
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

    // 根据权限控制按钮显示（未来可扩展）
    // 目前operate权限的按钮（入库、出库、导入、导出）不做隐藏，由后端权限控制
}

// 显示登录模态框
function showLoginModal() {
    document.getElementById('login-modal').classList.add('show');
    document.getElementById('login-username').focus();
    document.getElementById('login-error').style.display = 'none';
}

// 关闭登录模态框
function closeLoginModal() {
    document.getElementById('login-modal').classList.remove('show');
    document.getElementById('login-form').reset();
    document.getElementById('login-error').style.display = 'none';
}

// 处理登录
async function handleLogin(event) {
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
        const response = await fetch(`${API_BASE_URL}/auth/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ username, password })
        });
        const data = await response.json();

        if (data.success) {
            currentUser = data.user;
            closeLoginModal();
            updateUserDisplay();
            updatePermissionUI();
            refreshCurrentTab();
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
async function handleLogout() {
    try {
        await fetch(`${API_BASE_URL}/auth/logout`, {
            method: 'POST',
            credentials: 'include'
        });
    } catch (error) {
        console.error('登出失败:', error);
    }

    currentUser = null;
    updateUserDisplay();
    updatePermissionUI();

    // 如果在用户管理页面，切换到看板
    if (currentTab === 'users') {
        switchTab('dashboard');
    }
}

// 显示设置模态框（首次使用）
function showSetupModal() {
    document.getElementById('setup-modal').classList.add('show');
    document.getElementById('setup-username').focus();
    document.getElementById('setup-error').style.display = 'none';
}

// 处理首次设置
async function handleSetup(event) {
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
        const response = await fetch(`${API_BASE_URL}/auth/setup`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({
                username,
                password,
                display_name: displayName || null
            })
        });
        const data = await response.json();

        if (data.success) {
            currentUser = data.user;
            isSystemInitialized = true;
            document.getElementById('setup-modal').classList.remove('show');
            updateUserDisplay();
            updatePermissionUI();
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

// ============ 用户管理 ============

// 加载用户列表
async function loadUsers() {
    try {
        const response = await fetch(`${API_BASE_URL}/users`, {
            credentials: 'include'
        });

        if (response.status === 401 || response.status === 403) {
            return;
        }

        const users = await response.json();
        renderUsersTable(users);
    } catch (error) {
        console.error('加载用户列表失败:', error);
    }
}

// 渲染用户表格
function renderUsersTable(users) {
    const tbody = document.getElementById('users-tbody');
    if (!tbody) return;

    if (!users || users.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;color:#999;">${t('noData')}</td></tr>`;
        return;
    }

    tbody.innerHTML = users.map(user => {
        const displayNameEscaped = (user.display_name || '').replace(/'/g, "\\'").replace(/"/g, '&quot;');
        return `
        <tr>
            <td>${user.username}</td>
            <td>${user.display_name || '-'}</td>
            <td><span class="user-role-badge ${user.role}">${t('role' + user.role.charAt(0).toUpperCase() + user.role.slice(1))}</span></td>
            <td>${user.is_disabled ? `<span style="color:#ff4d4f;">${t('disabled')}</span>` : `<span style="color:#52c41a;">${t('enabled')}</span>`}</td>
            <td>${user.created_at}</td>
            <td>
                <button class="action-btn-small" onclick="showEditUserModal(${user.id}, '${user.username}', '${displayNameEscaped}', '${user.role}')">
                    ${t('edit')}
                </button>
                ${user.id !== currentUser.id ? `
                    <button class="action-btn-small ${user.is_disabled ? '' : 'danger'}" onclick="toggleUserStatus(${user.id}, ${user.is_disabled})">
                        ${user.is_disabled ? t('enable') : t('disable')}
                    </button>
                ` : ''}
            </td>
        </tr>
    `}).join('');
}

// 显示添加用户模态框
function showAddUserModal() {
    document.getElementById('add-user-modal').classList.add('show');
    document.getElementById('new-user-username').focus();
    document.getElementById('add-user-error').style.display = 'none';
}

// 关闭添加用户模态框
function closeAddUserModal() {
    document.getElementById('add-user-modal').classList.remove('show');
    document.getElementById('add-user-form').reset();
    document.getElementById('add-user-error').style.display = 'none';
}

// 处理添加用户
async function handleAddUser() {
    const username = document.getElementById('new-user-username').value.trim();
    const displayName = document.getElementById('new-user-display-name').value.trim();
    const password = document.getElementById('new-user-password').value;
    const role = document.getElementById('new-user-role').value;
    const errorDiv = document.getElementById('add-user-error');

    if (!username || !password) {
        errorDiv.textContent = t('fillAllFields');
        errorDiv.style.display = 'block';
        return;
    }

    try {
        const response = await fetch(`${API_BASE_URL}/users`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({
                username,
                password,
                display_name: displayName || null,
                role
            })
        });

        if (response.ok) {
            closeAddUserModal();
            loadUsers();
        } else {
            const data = await response.json();
            errorDiv.textContent = data.detail || data.error || t('operationFailed');
            errorDiv.style.display = 'block';
        }
    } catch (error) {
        console.error('添加用户失败:', error);
        errorDiv.textContent = t('operationFailed');
        errorDiv.style.display = 'block';
    }
}

// 切换用户状态
async function toggleUserStatus(userId, isDisabled) {
    try {
        const response = await fetch(`${API_BASE_URL}/users/${userId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ is_disabled: !isDisabled })
        });

        if (response.ok) {
            loadUsers();
        } else {
            const data = await response.json();
            alert(data.detail || data.error || t('operationFailed'));
        }
    } catch (error) {
        console.error('更新用户状态失败:', error);
        alert(t('operationFailed'));
    }
}

// 显示编辑用户模态框
function showEditUserModal(userId, username, displayName, role) {
    document.getElementById('edit-user-id').value = userId;
    document.getElementById('edit-user-username').value = username;
    document.getElementById('edit-user-display-name').value = displayName || '';
    document.getElementById('edit-user-password').value = '';
    document.getElementById('edit-user-role').value = role;
    document.getElementById('edit-user-modal').classList.add('show');
    document.getElementById('edit-user-username').focus();
    document.getElementById('edit-user-error').style.display = 'none';
}

// 关闭编辑用户模态框
function closeEditUserModal() {
    document.getElementById('edit-user-modal').classList.remove('show');
    document.getElementById('edit-user-form').reset();
    document.getElementById('edit-user-error').style.display = 'none';
}

// 处理编辑用户
async function handleEditUser() {
    const userId = document.getElementById('edit-user-id').value;
    const username = document.getElementById('edit-user-username').value.trim();
    const displayName = document.getElementById('edit-user-display-name').value.trim();
    const password = document.getElementById('edit-user-password').value;
    const role = document.getElementById('edit-user-role').value;
    const errorDiv = document.getElementById('edit-user-error');

    if (!username) {
        errorDiv.textContent = t('fillAllFields');
        errorDiv.style.display = 'block';
        return;
    }

    const updateData = {
        username,
        display_name: displayName || null,
        role
    };

    // 只有填写了新密码才更新密码
    if (password) {
        if (password.length < 4) {
            errorDiv.textContent = t('passwordTooShort');
            errorDiv.style.display = 'block';
            return;
        }
        updateData.password = password;
    }

    try {
        const response = await fetch(`${API_BASE_URL}/users/${userId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify(updateData)
        });

        if (response.ok) {
            closeEditUserModal();
            loadUsers();
            // 如果修改的是当前用户，刷新用户信息
            if (currentUser && currentUser.id == userId) {
                await checkAuthStatus();
            }
        } else {
            const data = await response.json();
            errorDiv.textContent = data.detail || data.error || t('operationFailed');
            errorDiv.style.display = 'block';
        }
    } catch (error) {
        console.error('编辑用户失败:', error);
        errorDiv.textContent = t('operationFailed');
        errorDiv.style.display = 'block';
    }
}

// ============ API密钥管理 ============

// 加载API密钥列表
async function loadApiKeys() {
    try {
        const response = await fetch(`${API_BASE_URL}/api-keys`, {
            credentials: 'include'
        });

        if (response.status === 401 || response.status === 403) {
            return;
        }

        const keys = await response.json();
        renderApiKeysTable(keys);
    } catch (error) {
        console.error('加载API密钥列表失败:', error);
    }
}

// 渲染API密钥表格
function renderApiKeysTable(keys) {
    const tbody = document.getElementById('api-keys-tbody');
    if (!tbody) return;

    if (!keys || keys.length === 0) {
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
                <button class="action-btn-small danger" onclick="disableApiKey(${key.id})">
                    ${t('disable')}
                </button>
            </td>
        </tr>
    `).join('');
}

// 显示添加API密钥模态框
function showAddApiKeyModal() {
    document.getElementById('add-api-key-modal').classList.add('show');
    document.getElementById('new-api-key-name').focus();
    document.getElementById('add-api-key-error').style.display = 'none';
}

// 关闭添加API密钥模态框
function closeAddApiKeyModal() {
    document.getElementById('add-api-key-modal').classList.remove('show');
    document.getElementById('add-api-key-form').reset();
    document.getElementById('add-api-key-error').style.display = 'none';
}

// 处理添加API密钥
async function handleAddApiKey() {
    const name = document.getElementById('new-api-key-name').value.trim();
    const role = document.getElementById('new-api-key-role').value;
    const errorDiv = document.getElementById('add-api-key-error');

    if (!name) {
        errorDiv.textContent = t('fillAllFields');
        errorDiv.style.display = 'block';
        return;
    }

    try {
        const response = await fetch(`${API_BASE_URL}/api-keys`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ name, role })
        });

        if (response.ok) {
            const data = await response.json();
            closeAddApiKeyModal();
            loadApiKeys();
            // 显示创建的API密钥
            showCreatedApiKey(data.key);
        } else {
            const data = await response.json();
            errorDiv.textContent = data.detail || data.error || t('operationFailed');
            errorDiv.style.display = 'block';
        }
    } catch (error) {
        console.error('添加API密钥失败:', error);
        errorDiv.textContent = t('operationFailed');
        errorDiv.style.display = 'block';
    }
}

// 显示创建的API密钥
function showCreatedApiKey(key) {
    document.getElementById('created-api-key').textContent = key;
    document.getElementById('show-api-key-modal').classList.add('show');
}

// 关闭显示API密钥模态框
function closeShowApiKeyModal() {
    document.getElementById('show-api-key-modal').classList.remove('show');
}

// 复制API密钥
function copyApiKey() {
    const key = document.getElementById('created-api-key').textContent;
    navigator.clipboard.writeText(key).then(() => {
        alert(t('copied'));
    }).catch(err => {
        console.error('复制失败:', err);
    });
}

// 禁用API密钥
async function disableApiKey(keyId) {
    if (!confirm('确定要禁用此API密钥吗？')) return;

    try {
        const response = await fetch(`${API_BASE_URL}/api-keys/${keyId}`, {
            method: 'DELETE',
            credentials: 'include'
        });

        if (response.ok) {
            loadApiKeys();
        } else {
            const data = await response.json();
            alert(data.detail || data.error || t('operationFailed'));
        }
    } catch (error) {
        console.error('禁用API密钥失败:', error);
        alert(t('operationFailed'));
    }
}

// 切换API密钥状态
async function toggleApiKeyStatus(keyId, isDisabled) {
    try {
        const response = await fetch(`${API_BASE_URL}/api-keys/${keyId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ is_disabled: !isDisabled })
        });

        if (response.ok) {
            loadApiKeys();
        } else {
            const data = await response.json();
            alert(data.detail || data.error || t('operationFailed'));
        }
    } catch (error) {
        console.error('更新API密钥状态失败:', error);
        alert(t('operationFailed'));
    }
}

// ============ 联系方管理 ============

// 加载联系方列表
async function loadContacts() {
    try {
        const name = document.getElementById('filter-contact-name')?.value || '';
        const contactType = document.getElementById('filter-contact-type')?.value || '';

        const params = new URLSearchParams({
            page: contactsCurrentPage,
            page_size: contactsPageSize
        });
        if (name) params.append('name', name);
        if (contactType) params.append('contact_type', contactType);

        const response = await fetch(`${API_BASE_URL}/contacts?${params}`, {
            credentials: 'include'
        });

        if (response.ok) {
            const data = await response.json();
            renderContactsTable(data.items);
            updateContactsPagination(data);
        }
    } catch (error) {
        console.error('加载联系方列表失败:', error);
    }
}

// 渲染联系方表格
function renderContactsTable(contacts) {
    const tbody = document.getElementById('contacts-tbody');
    if (!tbody) return;

    if (!contacts || contacts.length === 0) {
        tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;color:#999;">${t('noData')}</td></tr>`;
        return;
    }

    tbody.innerHTML = contacts.map(contact => {
        let typeText = '';
        if (contact.is_supplier && contact.is_customer) {
            typeText = t('bothType');
        } else if (contact.is_supplier) {
            typeText = t('supplier');
        } else if (contact.is_customer) {
            typeText = t('customer');
        }

        return `
            <tr>
                <td>${contact.name}</td>
                <td>${typeText}</td>
                <td>${contact.phone || '-'}</td>
                <td>${contact.email || '-'}</td>
                <td>${contact.address || '-'}</td>
                <td>${contact.is_disabled ? `<span style="color:#ff4d4f;">${t('disabled')}</span>` : `<span style="color:#52c41a;">${t('enabled')}</span>`}</td>
                <td>
                    <button class="action-btn-small" onclick="editContact(${contact.id})">${t('edit')}</button>
                    ${contact.is_disabled
                ? `<button class="action-btn-small success" onclick="toggleContactStatus(${contact.id}, true)">${t('enable')}</button>`
                : `<button class="action-btn-small danger" onclick="toggleContactStatus(${contact.id}, false)">${t('disable')}</button>`
            }
                </td>
            </tr>
        `;
    }).join('');
}

// 更新联系方分页
function updateContactsPagination(data) {
    contactsTotalPages = data.total_pages;

    document.getElementById('contacts-total').textContent = data.total;
    document.getElementById('contacts-current-page').textContent = contactsCurrentPage;
    document.getElementById('contacts-total-pages').textContent = contactsTotalPages;

    document.getElementById('contacts-prev-btn').disabled = contactsCurrentPage <= 1;
    document.getElementById('contacts-next-btn').disabled = contactsCurrentPage >= contactsTotalPages;
}

// 联系方分页导航
function contactsGoToPage(page) {
    if (page < 1 || page > contactsTotalPages) return;
    contactsCurrentPage = page;
    loadContacts();
}

// 改变联系方页面大小
function changeContactsPageSize(size) {
    contactsPageSize = parseInt(size);
    contactsCurrentPage = 1;
    loadContacts();
}

// 应用联系方筛选
function applyContactsFilter() {
    contactsCurrentPage = 1;
    loadContacts();
}

// 重置联系方筛选
function resetContactsFilter() {
    document.getElementById('filter-contact-name').value = '';
    document.getElementById('filter-contact-type').value = '';
    contactsCurrentPage = 1;
    loadContacts();
}

// 显示添加联系方模态框
function showAddContactModal() {
    document.getElementById('contact-modal-title').textContent = t('addContact');
    document.getElementById('contact-id').value = '';
    document.getElementById('contact-form').reset();
    document.getElementById('contact-error').style.display = 'none';
    document.getElementById('contact-modal').classList.add('show');
    document.getElementById('contact-name').focus();
}

// 关闭联系方模态框
function closeContactModal() {
    document.getElementById('contact-modal').classList.remove('show');
    document.getElementById('contact-form').reset();
    document.getElementById('contact-error').style.display = 'none';
}

// 编辑联系方
async function editContact(contactId) {
    try {
        const response = await fetch(`${API_BASE_URL}/contacts/${contactId}`, {
            credentials: 'include'
        });

        if (response.ok) {
            const contact = await response.json();
            document.getElementById('contact-modal-title').textContent = t('editContact');
            document.getElementById('contact-id').value = contact.id;
            document.getElementById('contact-name').value = contact.name;
            document.getElementById('contact-is-supplier').checked = contact.is_supplier;
            document.getElementById('contact-is-customer').checked = contact.is_customer;
            document.getElementById('contact-phone').value = contact.phone || '';
            document.getElementById('contact-email').value = contact.email || '';
            document.getElementById('contact-address').value = contact.address || '';
            document.getElementById('contact-notes').value = contact.notes || '';
            document.getElementById('contact-error').style.display = 'none';
            document.getElementById('contact-modal').classList.add('show');
        }
    } catch (error) {
        console.error('获取联系方详情失败:', error);
        alert(t('operationFailed'));
    }
}

// 保存联系方
async function handleSaveContact() {
    const contactId = document.getElementById('contact-id').value;
    const name = document.getElementById('contact-name').value.trim();
    const isSupplier = document.getElementById('contact-is-supplier').checked;
    const isCustomer = document.getElementById('contact-is-customer').checked;
    const phone = document.getElementById('contact-phone').value.trim();
    const email = document.getElementById('contact-email').value.trim();
    const address = document.getElementById('contact-address').value.trim();
    const notes = document.getElementById('contact-notes').value.trim();
    const errorDiv = document.getElementById('contact-error');

    if (!name) {
        errorDiv.textContent = t('fillAllFields');
        errorDiv.style.display = 'block';
        return;
    }

    if (!isSupplier && !isCustomer) {
        errorDiv.textContent = t('contactMustSelectType');
        errorDiv.style.display = 'block';
        return;
    }

    const data = {
        name,
        is_supplier: isSupplier,
        is_customer: isCustomer,
        phone: phone || null,
        email: email || null,
        address: address || null,
        notes: notes || null
    };

    try {
        const url = contactId
            ? `${API_BASE_URL}/contacts/${contactId}`
            : `${API_BASE_URL}/contacts`;
        const method = contactId ? 'PUT' : 'POST';

        const response = await fetch(url, {
            method,
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify(data)
        });

        if (response.ok) {
            closeContactModal();
            loadContacts();
        } else {
            const result = await response.json();
            errorDiv.textContent = result.detail || result.error || t('operationFailed');
            errorDiv.style.display = 'block';
        }
    } catch (error) {
        console.error('保存联系方失败:', error);
        errorDiv.textContent = t('operationFailed');
        errorDiv.style.display = 'block';
    }
}

// 切换联系方状态
async function toggleContactStatus(contactId, isDisabled) {
    try {
        const response = await fetch(`${API_BASE_URL}/contacts/${contactId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ is_disabled: !isDisabled })
        });

        if (response.ok) {
            loadContacts();
        } else {
            const data = await response.json();
            alert(data.detail || data.error || t('operationFailed'));
        }
    } catch (error) {
        console.error('更新联系方状态失败:', error);
        alert(t('operationFailed'));
    }
}

// ============ 导出到 window 对象（用于 HTML inline handlers） ============
// 由于使用 ES Module，需要将 HTML 中 onclick 等调用的函数暴露到全局

// Tab 切换与导航
window.switchTab = switchTab;
window.goBackToInventory = goBackToInventory;
window.refreshCurrentTab = refreshCurrentTab;

// 统计卡片点击
window.onTotalStockClick = onTotalStockClick;
window.onTodayInClick = onTodayInClick;
window.onTodayOutClick = onTodayOutClick;
window.onLowStockClick = onLowStockClick;

// 语言切换
window.toggleLangDropdown = toggleLangDropdown;
window.selectLanguage = selectLanguage;

// 用户认证
window.showLoginModal = showLoginModal;
window.closeLoginModal = closeLoginModal;
window.handleLogin = handleLogin;
window.handleLogout = handleLogout;
window.handleSetup = handleSetup;

// 用户管理
window.showAddUserModal = showAddUserModal;
window.closeAddUserModal = closeAddUserModal;
window.handleAddUser = handleAddUser;
window.showEditUserModal = showEditUserModal;
window.closeEditUserModal = closeEditUserModal;
window.handleEditUser = handleEditUser;
window.toggleUserStatus = toggleUserStatus;

// API 密钥管理
window.showAddApiKeyModal = showAddApiKeyModal;
window.closeAddApiKeyModal = closeAddApiKeyModal;
window.handleAddApiKey = handleAddApiKey;
window.copyApiKey = copyApiKey;
window.closeShowApiKeyModal = closeShowApiKeyModal;
window.toggleApiKeyStatus = toggleApiKeyStatus;

// 筛选器
window.applyRecordsFilter = applyRecordsFilter;
window.resetRecordsFilter = resetRecordsFilter;
window.applyInventoryFilter = applyInventoryFilter;
window.resetInventoryFilter = resetInventoryFilter;
window.applyContactsFilter = applyContactsFilter;
window.resetContactsFilter = resetContactsFilter;

// 多选下拉组件
window.toggleDropdown = toggleDropdown;
window.toggleDropdownItem = toggleDropdownItem;

// 记录操作
window.showAddRecordModal = showAddRecordModal;
window.closeAddRecordModal = closeAddRecordModal;
window.submitAddRecord = submitAddRecord;
window.showAddRecordModalForProduct = showAddRecordModalForProduct;
window.exportRecords = exportRecords;
window.exportProductRecords = exportProductRecords;

// 产品选择器
window.clearProductSelector = clearProductSelector;
window.clearRecordProductSelector = clearRecordProductSelector;

// 库存操作
window.showImportModal = showImportModal;
window.closeImportModal = closeImportModal;
window.handleFileSelect = handleFileSelect;
window.confirmImport = confirmImport;
window.exportInventory = exportInventory;
window.viewProductDetail = viewProductDetail;

// 新 SKU 确认
window.closeNewSkuModal = closeNewSkuModal;
window.skipNewSkus = skipNewSkus;
window.confirmNewSkus = confirmNewSkus;

// 分页控制
window.recordsGoToPage = recordsGoToPage;
window.changeRecordsPageSize = changeRecordsPageSize;
window.inventoryGoToPage = inventoryGoToPage;
window.changeInventoryPageSize = changeInventoryPageSize;
window.detailGoToPage = detailGoToPage;
window.changeDetailPageSize = changeDetailPageSize;
window.contactsGoToPage = contactsGoToPage;
window.changeContactsPageSize = changeContactsPageSize;

// 联系方管理
window.showAddContactModal = showAddContactModal;
window.closeContactModal = closeContactModal;
window.handleSaveContact = handleSaveContact;
window.showEditContactModal = showEditContactModal;
window.toggleContactStatus = toggleContactStatus;

// 暴露分页状态变量（用于 HTML inline 引用）
// 使用 getter 保持与模块变量同步
Object.defineProperty(window, 'recordsCurrentPage', {
    get: function () { return recordsCurrentPage; }
});
Object.defineProperty(window, 'inventoryCurrentPage', {
    get: function () { return inventoryCurrentPage; }
});
Object.defineProperty(window, 'detailCurrentPage', {
    get: function () { return detailCurrentPage; }
});
Object.defineProperty(window, 'contactsCurrentPage', {
    get: function () { return contactsCurrentPage; }
});

// ============ 事件委托系统 ============
// 使用 data-action 属性替代 inline onclick

const actionHandlers = {
    // 导航
    'switchTab': (el) => switchTab(el.dataset.tab),

    // 语言
    'toggleLangDropdown': toggleLangDropdown,
    'selectLanguage': (el) => selectLanguage(el.dataset.lang),

    // 刷新
    'refreshCurrentTab': refreshCurrentTab,

    // 登录/登出
    'showLoginModal': showLoginModal,
    'closeLoginModal': closeLoginModal,
    'handleLogin': (el) => handleLogin(new Event('click')),
    'handleLogout': handleLogout,
    'handleSetup': (el) => handleSetup(new Event('click')),

    // 用户管理
    'showAddUserModal': showAddUserModal,
    'closeAddUserModal': closeAddUserModal,
    'handleAddUser': handleAddUser,
    'showEditUserModal': (el) => showEditUserModal(el.dataset.userId),
    'closeEditUserModal': closeEditUserModal,
    'handleEditUser': handleEditUser,
    'toggleUserStatus': (el) => toggleUserStatus(el.dataset.userId, el.dataset.isDisabled === 'true'),

    // API 密钥
    'showAddApiKeyModal': showAddApiKeyModal,
    'closeAddApiKeyModal': closeAddApiKeyModal,
    'handleAddApiKey': handleAddApiKey,
    'copyApiKey': copyApiKey,
    'closeShowApiKeyModal': closeShowApiKeyModal,
    'toggleApiKeyStatus': (el) => toggleApiKeyStatus(el.dataset.keyId, el.dataset.isDisabled === 'true'),
    'disableApiKey': (el) => disableApiKey(el.dataset.keyId),

    // 筛选器
    'applyRecordsFilter': applyRecordsFilter,
    'resetRecordsFilter': resetRecordsFilter,
    'applyInventoryFilter': applyInventoryFilter,
    'resetInventoryFilter': resetInventoryFilter,
    'applyContactsFilter': applyContactsFilter,
    'resetContactsFilter': resetContactsFilter,

    // 多选下拉 - dropdown ID 从父元素获取
    'toggleDropdown': (el) => {
        const dropdown = el.closest('.dropdown-multiselect');
        if (dropdown) toggleDropdown(dropdown.id);
    },
    'toggleDropdownItem': (el) => toggleDropdownItem(el),

    // 记录操作
    'showAddRecordModal': showAddRecordModal,
    'closeAddRecordModal': closeAddRecordModal,
    'submitAddRecord': submitAddRecord,
    'showAddRecordModalForProduct': showAddRecordModalForProduct,
    'exportRecords': exportRecords,
    'exportProductRecords': exportProductRecords,

    // 产品选择器
    'clearProductSelector': clearProductSelector,
    'clearRecordProductSelector': clearRecordProductSelector,
    'goBackToInventory': goBackToInventory,

    // 库存操作
    'showImportModal': showImportModal,
    'closeImportModal': closeImportModal,
    'triggerFileUpload': () => document.getElementById('excel-file').click(),
    'handleFileSelect': (el, event) => handleFileSelect(event),
    'confirmImport': confirmImport,
    'exportInventory': exportInventory,
    'viewProductDetail': (el) => viewProductDetail(el.dataset.productId),

    // 新 SKU 确认
    'closeNewSkuModal': closeNewSkuModal,
    'skipNewSkus': skipNewSkus,
    'confirmNewSkus': confirmNewSkus,

    // 分页 - 上一页/下一页
    'recordsPrevPage': () => recordsGoToPage(recordsCurrentPage - 1),
    'recordsNextPage': () => recordsGoToPage(recordsCurrentPage + 1),
    'changeRecordsPageSize': (el) => changeRecordsPageSize(el.value),
    'inventoryPrevPage': () => inventoryGoToPage(inventoryCurrentPage - 1),
    'inventoryNextPage': () => inventoryGoToPage(inventoryCurrentPage + 1),
    'changeInventoryPageSize': (el) => changeInventoryPageSize(el.value),
    'detailPrevPage': () => detailGoToPage(detailCurrentPage - 1),
    'detailNextPage': () => detailGoToPage(detailCurrentPage + 1),
    'changeDetailPageSize': (el) => changeDetailPageSize(el.value),
    'contactsPrevPage': () => contactsGoToPage(contactsCurrentPage - 1),
    'contactsNextPage': () => contactsGoToPage(contactsCurrentPage + 1),
    'changeContactsPageSize': (el) => changeContactsPageSize(el.value),

    // 联系方管理
    'showAddContactModal': showAddContactModal,
    'closeContactModal': closeContactModal,
    'handleSaveContact': handleSaveContact,
    'showEditContactModal': (el) => showEditContactModal(el.dataset.contactId),
    'toggleContactStatus': (el) => toggleContactStatus(el.dataset.contactId, el.dataset.isDisabled === 'true'),

    // 统计卡片点击
    'onTotalStockClick': onTotalStockClick,
    'onTodayInClick': onTodayInClick,
    'onTodayOutClick': onTodayOutClick,
    'onLowStockClick': onLowStockClick,
};

console.log('ActionHandlers initialized successfully:', Object.keys(actionHandlers));

// 事件委托：监听所有点击事件
document.addEventListener('click', function (e) {
    // 查找具有 data-action 属性的元素（包括冒泡路径上的元素）
    const actionEl = e.target.closest('[data-action]');
    if (!actionEl) return;

    const action = actionEl.dataset.action;
    const handler = actionHandlers[action];

    if (handler) {
        e.preventDefault();
        handler(actionEl);
    } else {
        console.warn('未定义的 action:', action);
    }
});

// 处理 change 事件（select、input 等）
document.addEventListener('change', function (e) {
    const actionEl = e.target.closest('[data-action-change]');
    if (!actionEl) return;

    const action = actionEl.dataset.actionChange;
    const handler = actionHandlers[action];

    if (handler) {
        handler(actionEl, e);
    }
});


// ============ Missing Function Stubs (Fix for ReferenceErrors) ============
function viewProductDetail(productId) {
    console.log('viewProductDetail called', productId);
    // Logic to switch to detail tab
    currentProductName = productId;
    switchTab('detail', { product: productId });
}
function showEditContactModal(contactId) { console.log('showEditContactModal not implemented', contactId); }
