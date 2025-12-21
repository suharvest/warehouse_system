// ============ Dashboard 模块 ============
import * as echarts from 'echarts';
import { t } from '../../../i18n.js';
import { dashboardApi } from '../api.js';
import {
    trendChart, categoryChart, topStockChart,
    setTrendChart, setCategoryChart, setTopStockChart,
    detailTrendChart, detailPieChart,
    setDetailTrendChart, setDetailPieChart
} from '../state.js';

// 回调函数引用
let switchTabFn = null;

// 设置回调
export function setDashboardCallbacks(callbacks) {
    switchTabFn = callbacks.switchTab;
}

// ============ 卡片点击 ============
export function onTotalStockClick() {
    if (switchTabFn) switchTabFn('inventory');
}

export function onTodayInClick() {
    const today = new Date().toISOString().split('T')[0];
    if (switchTabFn) switchTabFn('records', { type: 'in', start_date: today, end_date: today });
}

export function onTodayOutClick() {
    const today = new Date().toISOString().split('T')[0];
    if (switchTabFn) switchTabFn('records', { type: 'out', start_date: today, end_date: today });
}

export function onLowStockClick() {
    if (switchTabFn) switchTabFn('inventory', { status: 'warning,danger' });
}

// ============ 图表初始化 ============
export function initCharts() {
    setTrendChart(echarts.init(document.getElementById('trend-chart')));
    setCategoryChart(echarts.init(document.getElementById('category-chart')));
    setTopStockChart(echarts.init(document.getElementById('top-stock-chart')));

    window.addEventListener('resize', function () {
        trendChart && trendChart.resize();
        categoryChart && categoryChart.resize();
        topStockChart && topStockChart.resize();
        detailTrendChart && detailTrendChart.resize();
        detailPieChart && detailPieChart.resize();
    });
}

export function initDetailCharts() {
    if (!detailTrendChart) {
        setDetailTrendChart(echarts.init(document.getElementById('detail-trend-chart')));
    }
    if (!detailPieChart) {
        setDetailPieChart(echarts.init(document.getElementById('detail-pie-chart')));
    }
}

// ============ 数据加载 ============
export async function loadDashboardData() {
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
    const data = await dashboardApi.getStats();

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
    const data = await dashboardApi.getCategoryDistribution();

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
    const data = await dashboardApi.getWeeklyTrend();

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
    const data = await dashboardApi.getTopStock();

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
