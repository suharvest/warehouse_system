const API_BASE_URL = 'http://localhost:2124/api';

// 初始化图表
let trendChart, pieChart;
let productName = '';

// 保存产品统计数据用于语言切换
let lastProductStats = null;
// 保存记录数据用于语言切换
let lastRecords = [];

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', function() {
    // 从URL参数获取产品名称
    const urlParams = new URLSearchParams(window.location.search);
    productName = urlParams.get('product') || '';

    if (!productName) {
        alert(t('productNotFound'));
        goBack();
        return;
    }

    document.getElementById('product-title').textContent = productName;

    initCharts();
    loadProductData();
});

// 语言变更回调
function onLanguageChange() {
    document.title = t('detailPageTitle');
    // 重新渲染需要翻译的动态内容
    if (lastProductStats) {
        updateStockStatus(lastProductStats);
    }
    if (lastRecords.length > 0) {
        renderRecordsTable(lastRecords);
    }
    // 重新加载图表以更新图例
    loadProductTrend();
    if (lastProductStats) {
        loadPieChart(lastProductStats.total_in, lastProductStats.total_out);
    }
}

// 更新库存状态显示
function updateStockStatus(data) {
    const statusElem = document.getElementById('stock-status');
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
}

// 返回首页
function goBack() {
    window.location.href = 'index.html';
}

// 初始化图表
function initCharts() {
    trendChart = echarts.init(document.getElementById('trend-chart'));
    pieChart = echarts.init(document.getElementById('pie-chart'));

    // 响应式
    window.addEventListener('resize', function() {
        trendChart.resize();
        pieChart.resize();
    });
}

// 加载产品数据
async function loadProductData() {
    try {
        await Promise.all([
            loadProductStats(),
            loadProductTrend(),
            loadProductRecords()
        ]);
    } catch (error) {
        console.error('加载数据失败:', error);
        alert(t('loadError'));
    }
}

// 加载产品统计数据
async function loadProductStats() {
    const response = await fetch(`${API_BASE_URL}/materials/product-stats?name=${encodeURIComponent(productName)}`);
    const data = await response.json();

    if (data.error) {
        alert(data.error);
        goBack();
        return;
    }

    // 保存数据用于语言切换
    lastProductStats = data;

    // 更新统计卡片
    document.getElementById('current-stock').textContent = data.current_stock.toLocaleString();
    document.getElementById('stock-unit').textContent = data.unit;
    document.getElementById('today-in').textContent = data.today_in.toLocaleString();
    document.getElementById('today-out').textContent = data.today_out.toLocaleString();
    document.getElementById('safe-stock').textContent = data.safe_stock.toLocaleString();

    // 更新变化百分比
    const inChange = document.getElementById('in-change');
    inChange.textContent = (data.in_change >= 0 ? '+' : '') + data.in_change + '%';
    inChange.className = data.in_change >= 0 ? 'stat-change positive' : 'stat-change negative';

    const outChange = document.getElementById('out-change');
    outChange.textContent = (data.out_change >= 0 ? '+' : '') + data.out_change + '%';
    outChange.className = data.out_change >= 0 ? 'stat-change positive' : 'stat-change negative';

    // 更新库存状态
    updateStockStatus(data);

    // 更新饼图
    loadPieChart(data.total_in, data.total_out);
}

// 加载产品趋势数据
async function loadProductTrend() {
    const response = await fetch(`${API_BASE_URL}/materials/product-trend?name=${encodeURIComponent(productName)}`);
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

// 加载饼图
function loadPieChart(totalIn, totalOut) {
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
                name: t('inOutRatio'),
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
                data: [
                    { value: totalIn, name: t('inbound'), itemStyle: { color: '#5470c6' } },
                    { value: totalOut, name: t('outbound'), itemStyle: { color: '#ee6666' } }
                ]
            }
        ]
    };

    pieChart.setOption(option, true);
}

// 加载出入库记录
async function loadProductRecords() {
    const response = await fetch(`${API_BASE_URL}/materials/product-records?name=${encodeURIComponent(productName)}`);
    const data = await response.json();

    // 保存记录数据用于语言切换
    lastRecords = data;

    renderRecordsTable(data);
}

// 渲染出入库记录表格
function renderRecordsTable(records) {
    const tbody = document.getElementById('records-tbody');
    tbody.innerHTML = '';

    if (records.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: #999;">${t('noRecords')}</td></tr>`;
        return;
    }

    records.forEach(record => {
        const tr = document.createElement('tr');

        const typeText = record.type === 'in' ? t('inbound') : t('outbound');
        const typeClass = record.type === 'in' ? 'type-in' : 'type-out';

        tr.innerHTML = `
            <td>${record.created_at}</td>
            <td><span class="type-badge ${typeClass}">${typeText}</span></td>
            <td><strong>${record.quantity}</strong></td>
            <td>${record.operator}</td>
            <td>${record.reason}</td>
        `;
        tbody.appendChild(tr);
    });
}
