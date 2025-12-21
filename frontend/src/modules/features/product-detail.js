// ============ 产品详情模块 ============
import * as echarts from 'echarts';
import { t } from '../../../i18n.js';
import { productApi } from '../api.js';
import {
    currentProductName, setCurrentProductName,
    detailCurrentPage, detailPageSize, detailTotalPages,
    setDetailCurrentPage, setDetailPageSize, setDetailTotalPages,
    lastProductStats, setLastProductStats,
    detailTrendChart, detailPieChart,
    setDetailTrendChart, setDetailPieChart
} from '../state.js';

// ============ 产品选择 ============
export function onProductSelect(productName) {
    if (!productName) {
        document.getElementById('product-detail-content').style.display = 'none';
        document.getElementById('no-product-selected').style.display = 'flex';
        setCurrentProductName('');
        return;
    }

    setCurrentProductName(productName);
    document.getElementById('product-detail-content').style.display = 'block';
    document.getElementById('no-product-selected').style.display = 'none';

    initDetailCharts();
    loadProductDetail();
}

// ============ 图表初始化 ============
export function initDetailCharts() {
    if (!detailTrendChart) {
        setDetailTrendChart(echarts.init(document.getElementById('detail-trend-chart')));
    }
    if (!detailPieChart) {
        setDetailPieChart(echarts.init(document.getElementById('detail-pie-chart')));
    }
}

// ============ 数据加载 ============
export async function loadProductDetail() {
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
    const data = await productApi.getStats(currentProductName);

    if (data.error) {
        alert(data.error);
        return;
    }

    setLastProductStats(data);

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

    loadDetailPieChart(data.total_in, data.total_out);
}

export async function loadProductTrend() {
    const data = await productApi.getTrend(currentProductName);

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

export function loadDetailPieChart(totalIn, totalOut) {
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

// ============ 产品记录列表 ============
export async function loadProductRecords() {
    const data = await productApi.getRecords(currentProductName, {
        page: detailCurrentPage,
        pageSize: detailPageSize
    });

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
    setDetailTotalPages(data.total_pages);
    document.getElementById('detail-total').textContent = data.total;
    document.getElementById('detail-current-page').textContent = data.page;
    document.getElementById('detail-total-pages').textContent = data.total_pages;
    document.getElementById('detail-prev-btn').disabled = data.page <= 1;
    document.getElementById('detail-next-btn').disabled = data.page >= data.total_pages;
}

export function detailGoToPage(page) {
    if (page < 1 || page > detailTotalPages) return;
    setDetailCurrentPage(page);
    loadProductRecords();
}

export function changeDetailPageSize(size) {
    setDetailPageSize(parseInt(size));
    setDetailCurrentPage(1);
    loadProductRecords();
}

// ============ 语言变更时重新渲染 ============
export function refreshProductDetailForLanguage() {
    if (!currentProductName) return;

    loadProductTrend();
    loadProductRecords();

    if (lastProductStats) {
        loadDetailPieChart(lastProductStats.total_in, lastProductStats.total_out);

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
