const API_BASE_URL = 'http://localhost:2124/api';

// ============ 全局变量 ============
let currentTab = 'dashboard';
let countdownInterval = null;
let countdownSeconds = 20;

// 图表实例
let trendChart, categoryChart, topStockChart;
let detailTrendChart, detailPieChart;

// 分类数据
let allCategories = [];

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

// ============ 页面初始化 ============
document.addEventListener('DOMContentLoaded', function () {
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
        if (tab && ['dashboard', 'records', 'inventory', 'detail'].includes(tab)) {
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
            break;
        case 'records':
            applyRecordsFilters(filters);
            loadRecords();
            break;
        case 'inventory':
            applyInventoryFilters(filters);
            loadInventory();
            break;
        case 'detail':
            if (filters.product) {
                document.getElementById('product-selector').value = filters.product;
                onProductSelect(filters.product);
            }
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
    const select = document.getElementById('filter-inventory-category');
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
    const selector = document.getElementById('product-selector');
    const recordProduct = document.getElementById('record-product');

    if (!allProducts || !Array.isArray(allProducts)) {
        allProducts = [];
    }

    if (selector) {
        const firstOption = selector.options[0];
        selector.innerHTML = '';
        if (firstOption) {
            selector.appendChild(firstOption);
        } else {
            const opt = document.createElement('option');
            opt.value = '';
            opt.textContent = t('selectProductHint');
            selector.appendChild(opt);
        }

        allProducts.forEach(product => {
            const option = document.createElement('option');
            option.value = product.name;
            option.textContent = `${product.name} (${product.sku})${product.is_disabled ? ' [' + t('statusDisabled') + ']' : ''}`;
            selector.appendChild(option);
        });
    }

    if (recordProduct) {
        const firstOption = recordProduct.options[0];
        recordProduct.innerHTML = '';
        if (firstOption) {
            recordProduct.appendChild(firstOption);
        } else {
            const opt = document.createElement('option');
            opt.value = '';
            opt.textContent = t('pleaseSelect');
            recordProduct.appendChild(opt);
        }

        allProducts.filter(p => !p.is_disabled).forEach(product => {
            const option = document.createElement('option');
            option.value = product.name;
            option.textContent = `${product.name} (${product.sku}) - ${t('currentStockCol')}: ${product.quantity}`;
            recordProduct.appendChild(option);
        });
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
    const recordType = document.getElementById('filter-record-type').value;
    const selectedStatuses = getDropdownSelectedValues('filter-record-status-dropdown');

    const params = new URLSearchParams({
        page: recordsCurrentPage,
        page_size: recordsPageSize
    });

    if (startDate) params.set('start_date', startDate);
    if (endDate) params.set('end_date', endDate);
    if (productName) params.set('product_name', productName);
    if (recordType) params.set('record_type', recordType);
    if (selectedStatuses.length > 0) {
        params.set('status', selectedStatuses.join(','));
    }

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
        tbody.innerHTML = `<tr><td colspan="8" style="text-align: center; color: #999;">${t('noRecords')}</td></tr>`;
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

        tr.innerHTML = `
            <td>${item.created_at}</td>
            <td>${item.material_name}</td>
            <td>${item.material_sku}</td>
            <td><span class="type-badge ${typeClass}">${typeText}</span></td>
            <td><strong>${item.quantity}</strong></td>
            <td>${item.operator}</td>
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
    document.getElementById('filter-record-type').value = '';
    // 重置状态多选：选中除禁用外的所有选项
    resetDropdownSelection('filter-record-status-dropdown');
    recordsCurrentPage = 1;
    loadRecords();
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
    window.location.href = `${API_BASE_URL}/materials/export-excel`;
}

function exportRecords() {
    const startDate = document.getElementById('filter-start-date').value;
    const endDate = document.getElementById('filter-end-date').value;
    const productName = document.getElementById('filter-records-product').value.trim();

    const params = new URLSearchParams();
    if (startDate) params.set('start_date', startDate);
    if (endDate) params.set('end_date', endDate);
    if (productName) params.set('product_name', productName);

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

    const operator = document.getElementById('import-operator').value.trim();
    const reason = document.getElementById('import-reason').value.trim();

    if (!operator || !reason) {
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
    const operator = document.getElementById('import-operator').value.trim();
    const reason = document.getElementById('import-reason').value.trim();
    const confirmDisableMissing = document.getElementById('confirm-disable-missing')?.checked || false;

    try {
        const response = await fetch(`${API_BASE_URL}/materials/import-excel/confirm`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                changes: importPreviewData.preview,
                operator: operator,
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
}

function closeAddRecordModal() {
    document.getElementById('add-record-modal').classList.remove('show');
    document.getElementById('add-record-form').reset();
}

async function submitAddRecord() {
    const productName = addRecordForProduct
        ? currentProductName
        : document.getElementById('record-product').value;
    const type = document.querySelector('input[name="record-type"]:checked')?.value;
    const quantity = parseInt(document.getElementById('record-quantity').value);
    const operator = document.getElementById('record-operator').value.trim();
    const reason = document.getElementById('record-reason').value.trim();

    // 必填项校验
    if (!productName || !type || !document.getElementById('record-quantity').value || !operator || !reason) {
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
            body: JSON.stringify({
                product_name: productName,
                type: type,
                quantity: quantity,
                operator: operator,
                reason: reason
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
    }
}
