const API_BASE_URL = 'http://localhost:2124/api';

// 初始化图表
let trendChart, categoryChart, topStockChart;

// 全局变量
let allMaterials = []; // 存储所有物料数据
let updateInterval = null; // 自动更新定时器
let countdownInterval = null; // 倒计时定时器
let countdownSeconds = 3; // 倒计时秒数

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', function() {
    initCharts();
    loadAllData();
    initSearchFilter();
    startAutoUpdate();
});

// 初始化图表
function initCharts() {
    trendChart = echarts.init(document.getElementById('trend-chart'));
    categoryChart = echarts.init(document.getElementById('category-chart'));
    topStockChart = echarts.init(document.getElementById('top-stock-chart'));

    // 响应式
    window.addEventListener('resize', function() {
        trendChart.resize();
        categoryChart.resize();
        topStockChart.resize();
    });
}

// 加载所有数据
async function loadAllData() {
    try {
        await Promise.all([
            loadDashboardStats(),
            loadCategoryDistribution(),
            loadWeeklyTrend(),
            loadTopStock(),
            loadAllMaterials()
        ]);
    } catch (error) {
        console.error('加载数据失败:', error);
        alert('加载数据失败，请检查后端服务是否启动');
    }
}

// 刷新数据
function refreshData() {
    loadAllData();
    resetCountdown();
}

// 语言变更回调
function onLanguageChange() {
    document.title = t('pageTitle');
    // 重新渲染表格以更新状态文本
    if (allMaterials.length > 0) {
        const searchInput = document.getElementById('search-input');
        const keyword = searchInput ? searchInput.value.toLowerCase().trim() : '';
        if (keyword) {
            filterMaterials(keyword);
        } else {
            renderInventoryTable(allMaterials);
        }
    }
    // 重新加载图表以更新图例
    loadWeeklyTrend();
}

// 初始化搜索过滤
function initSearchFilter() {
    const searchInput = document.getElementById('search-input');
    searchInput.addEventListener('input', function(e) {
        const keyword = e.target.value.toLowerCase().trim();
        filterMaterials(keyword);
    });
}

// 过滤物料
function filterMaterials(keyword) {
    if (!keyword) {
        renderInventoryTable(allMaterials);
        return;
    }

    const filtered = allMaterials.filter(item =>
        item.name.toLowerCase().includes(keyword) ||
        item.sku.toLowerCase().includes(keyword)
    );
    renderInventoryTable(filtered);
}

// 启动自动更新
function startAutoUpdate() {
    // 清除旧的定时器
    if (updateInterval) clearInterval(updateInterval);
    if (countdownInterval) clearInterval(countdownInterval);

    // 倒计时
    countdownInterval = setInterval(function() {
        countdownSeconds--;
        document.getElementById('countdown').textContent = countdownSeconds;

        if (countdownSeconds <= 0) {
            // 更新所有数据
            loadAllData();
            countdownSeconds = 3;
        }
    }, 1000);
}

// 重置倒计时
function resetCountdown() {
    countdownSeconds = 3;
    document.getElementById('countdown').textContent = countdownSeconds;
}

// 加载统计数据
async function loadDashboardStats() {
    const response = await fetch(`${API_BASE_URL}/dashboard/stats`);
    const data = await response.json();

    document.getElementById('total-stock').textContent = data.total_stock.toLocaleString();
    document.getElementById('today-in').textContent = data.today_in.toLocaleString();
    document.getElementById('today-out').textContent = data.today_out.toLocaleString();
    document.getElementById('low-stock-count').textContent = data.low_stock_count;

    // 更新变化百分比
    const inChange = document.getElementById('in-change');
    inChange.textContent = (data.in_change >= 0 ? '+' : '') + data.in_change + '%';
    inChange.className = data.in_change >= 0 ? 'stat-change positive' : 'stat-change negative';

    const outChange = document.getElementById('out-change');
    outChange.textContent = (data.out_change >= 0 ? '+' : '') + data.out_change + '%';
    outChange.className = data.out_change >= 0 ? 'stat-change positive' : 'stat-change negative';
}

// 加载类型分布
async function loadCategoryDistribution() {
    const response = await fetch(`${API_BASE_URL}/dashboard/category-distribution`);
    const data = await response.json();

    const option = {
        tooltip: {
            trigger: 'item',
            formatter: '{b}: {c} ({d}%)'
        },
        legend: {
            orient: 'vertical',
            right: 10,
            top: 'center',
            textStyle: {
                fontSize: 12
            }
        },
        series: [
            {
                name: '库存分布',
                type: 'pie',
                radius: ['40%', '70%'],
                avoidLabelOverlap: false,
                itemStyle: {
                    borderRadius: 10,
                    borderColor: '#fff',
                    borderWidth: 2
                },
                label: {
                    show: false
                },
                emphasis: {
                    label: {
                        show: true,
                        fontSize: 14,
                        fontWeight: 'bold'
                    }
                },
                labelLine: {
                    show: false
                },
                data: data,
                color: ['#5470c6', '#91cc75', '#fac858', '#ee6666', '#73c0de', '#3ba272', '#fc8452', '#9a60b4']
            }
        ]
    };

    categoryChart.setOption(option);
}

// 加载近7天趋势
async function loadWeeklyTrend() {
    const response = await fetch(`${API_BASE_URL}/dashboard/weekly-trend`);
    const data = await response.json();

    const option = {
        tooltip: {
            trigger: 'axis',
            axisPointer: {
                type: 'cross'
            }
        },
        legend: {
            data: [t('inbound'), t('outbound')],
            textStyle: {
                fontSize: 12
            }
        },
        grid: {
            left: '3%',
            right: '4%',
            bottom: '3%',
            containLabel: true
        },
        xAxis: {
            type: 'category',
            boundaryGap: false,
            data: data.dates,
            axisLine: {
                lineStyle: {
                    color: '#ccc'
                }
            },
            axisLabel: {
                color: '#666'
            }
        },
        yAxis: {
            type: 'value',
            axisLine: {
                lineStyle: {
                    color: '#ccc'
                }
            },
            axisLabel: {
                color: '#666'
            },
            splitLine: {
                lineStyle: {
                    color: '#eee'
                }
            }
        },
        series: [
            {
                name: t('inbound'),
                type: 'line',
                smooth: true,
                data: data.in_data,
                itemStyle: {
                    color: '#5470c6'
                },
                areaStyle: {
                    color: {
                        type: 'linear',
                        x: 0,
                        y: 0,
                        x2: 0,
                        y2: 1,
                        colorStops: [
                            {
                                offset: 0,
                                color: 'rgba(84, 112, 198, 0.3)'
                            },
                            {
                                offset: 1,
                                color: 'rgba(84, 112, 198, 0.05)'
                            }
                        ]
                    }
                }
            },
            {
                name: t('outbound'),
                type: 'line',
                smooth: true,
                data: data.out_data,
                itemStyle: {
                    color: '#ee6666'
                },
                areaStyle: {
                    color: {
                        type: 'linear',
                        x: 0,
                        y: 0,
                        x2: 0,
                        y2: 1,
                        colorStops: [
                            {
                                offset: 0,
                                color: 'rgba(238, 102, 102, 0.3)'
                            },
                            {
                                offset: 1,
                                color: 'rgba(238, 102, 102, 0.05)'
                            }
                        ]
                    }
                }
            }
        ]
    };

    trendChart.setOption(option, true);
}

// 加载库存TOP10
async function loadTopStock() {
    const response = await fetch(`${API_BASE_URL}/dashboard/top-stock`);
    const data = await response.json();

    const option = {
        tooltip: {
            trigger: 'axis',
            axisPointer: {
                type: 'shadow'
            },
            formatter: function(params) {
                const index = params[0].dataIndex;
                return `${data.names[index]}<br/>类型: ${data.categories[index]}<br/>库存: ${params[0].value}`;
            }
        },
        grid: {
            left: '3%',
            right: '4%',
            bottom: '3%',
            top: '3%',
            containLabel: true
        },
        xAxis: {
            type: 'value',
            axisLine: {
                lineStyle: {
                    color: '#ccc'
                }
            },
            axisLabel: {
                color: '#666'
            },
            splitLine: {
                lineStyle: {
                    color: '#eee'
                }
            }
        },
        yAxis: {
            type: 'category',
            data: data.names.map(name => name.length > 12 ? name.substring(0, 12) + '...' : name),
            axisLine: {
                lineStyle: {
                    color: '#ccc'
                }
            },
            axisLabel: {
                color: '#666'
            }
        },
        series: [
            {
                type: 'bar',
                data: data.quantities,
                itemStyle: {
                    color: {
                        type: 'linear',
                        x: 0,
                        y: 0,
                        x2: 1,
                        y2: 0,
                        colorStops: [
                            {
                                offset: 0,
                                color: '#667eea'
                            },
                            {
                                offset: 1,
                                color: '#764ba2'
                            }
                        ]
                    },
                    borderRadius: [0, 4, 4, 0]
                },
                barWidth: '60%'
            }
        ]
    };

    topStockChart.setOption(option);
}

// 加载所有物料
async function loadAllMaterials() {
    const response = await fetch(`${API_BASE_URL}/materials/all`);
    const data = await response.json();

    allMaterials = data;

    // 应用当前搜索条件
    const searchInput = document.getElementById('search-input');
    const keyword = searchInput ? searchInput.value.toLowerCase().trim() : '';

    if (keyword) {
        filterMaterials(keyword);
    } else {
        renderInventoryTable(data);
    }
}

// 渲染库存表格
function renderInventoryTable(data) {
    const tbody = document.getElementById('inventory-tbody');
    tbody.innerHTML = '';

    if (data.length === 0) {
        tbody.innerHTML = `<tr><td colspan="8" style="text-align: center; color: #999;">${t('noData')}</td></tr>`;
        return;
    }

    data.forEach(item => {
        const tr = document.createElement('tr');
        tr.className = 'clickable';

        // 根据状态码获取翻译后的状态文本
        let statusText = '';
        let statusClass = '';
        if (item.status === 'normal') {
            statusText = t('statusNormal');
            statusClass = 'status-normal';
        } else if (item.status === 'warning') {
            statusText = t('statusWarning');
            statusClass = 'status-warning';
        } else {
            statusText = t('statusDanger');
            statusClass = 'status-danger';
        }
        const statusBadge = `<span class="status-badge ${statusClass}">${statusText}</span>`;

        tr.innerHTML = `
            <td>${item.name}</td>
            <td>${item.sku}</td>
            <td>${item.category}</td>
            <td><strong>${item.quantity}</strong></td>
            <td>${item.unit}</td>
            <td>${item.safe_stock}</td>
            <td>${statusBadge}</td>
            <td>${item.location}</td>
        `;

        // 添加点击事件，跳转到产品详情页
        tr.addEventListener('click', function() {
            window.location.href = `product_detail.html?product=${encodeURIComponent(item.name)}`;
        });

        tbody.appendChild(tr);
    });
}
