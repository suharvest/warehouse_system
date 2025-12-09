// 翻译数据
const translations = {
    zh: {
        // 通用
        pageTitle: '仓库管理系统 - 仪表盘',
        detailPageTitle: '产品详情 - 仓库管理系统',
        systemTitle: '仓库管理系统',
        subtitle: '访问统计概览',
        refresh: '刷新',
        back: '返回',
        productDetail: '产品详情',
        productStockDetail: '产品库存详情',

        // Tab 名称
        tabDashboard: '看板',
        tabRecords: '进出库记录',
        tabInventory: '库存列表',
        tabDetail: '产品详情',

        // 统计卡片
        totalStock: '库存总量',
        todayIn: '今日入库',
        todayOut: '今日出库',
        lowStockAlert: '库存预警',
        currentStock: '当前库存',
        safeStock: '安全库存',
        unit: '个/件',
        materialTypes: '种物料',

        // 图表标题
        weeklyTrend: '近7天出入库趋势',
        categoryDistribution: '库存类型分布',
        topStock: '库存TOP10',
        inOutRatio: '出入库占比',

        // 图表图例
        inbound: '入库',
        outbound: '出库',

        // 库存列表
        inventoryList: '库存列表',
        searchPlaceholder: '搜索产品名称...',
        autoUpdate: '自动更新:',
        seconds: '秒',

        // 表头 - 主页
        materialName: '物料名称',
        materialCode: '物料编码',
        type: '类型',
        currentStockCol: '当前库存',
        unitCol: '单位',
        safeStockCol: '安全库存',
        status: '状态',
        location: '存放位置',

        // 表头 - 详情页
        inOutRecords: '出入库记录',
        time: '时间',
        quantity: '数量',
        operator: '操作人',
        reason: '原因',

        // 状态文本
        statusNormal: '正常',
        statusWarning: '偏低',
        statusDanger: '告急',
        statusDisabled: '禁用',

        // 筛选
        filterStartDate: '开始日期',
        filterEndDate: '结束日期',
        filterProduct: '名称/编码',
        filterCategory: '分类',
        filterType: '类型',
        filterStatus: '状态',
        filterBtn: '筛选',
        resetBtn: '重置',
        allTypes: '全部类型',
        allCategories: '全部分类',
        allStatuses: '全部状态',

        // 分页
        prevPage: '上一页',
        nextPage: '下一页',
        pageSize: '每页',
        totalRecords: '共 {count} 条记录',
        recordsUnit: '条',
        pageInfo: '第 {page} 页 / 共 {total} 页',

        // 产品选择
        selectProductHint: '请选择产品查看详情',

        // Excel导入导出
        exportInventory: '导出库存',
        importInventory: '导入库存',
        exportRecords: '导出记录',
        addRecord: '新增记录',
        dropFileHere: '点击选择Excel文件',
        importPreview: '导入预览',
        totalInLabel: '入库总量:',
        totalOutLabel: '出库总量:',
        totalNewLabel: '新增物料:',
        importQty: '导入数量',
        difference: '差异',
        operation: '操作',
        operatorPlaceholder: '请输入操作人',
        reasonPlaceholder: '请输入导入原因',
        cancel: '取消',
        confirmImport: '确认导入',
        newMaterial: '新增',
        noChange: '无变化',

        // 新SKU确认
        confirmNewSku: '确认新增物料',
        newSkuWarning: '以下SKU在系统中不存在，将创建新物料：',
        skipNewSkus: '跳过新增',
        confirmCreate: '确认创建',

        // 新增记录
        addInventoryRecord: '新增出入库记录',
        selectProduct: '选择产品',
        pleaseSelect: '-- 请选择 --',
        operationType: '操作类型',
        submit: '提交',
        productName: '产品',

        // 错误提示
        fillOperatorAndReason: '请填写操作人和原因',
        fillAllFields: '请填写所有字段',
        quantityMustBePositive: '数量必须大于0',
        parseFileFailed: '解析文件失败',
        previewFailed: '预览失败',
        importFailed: '导入失败',
        operationFailed: '操作失败',

        // 其他
        noData: '暂无数据',
        noRecords: '暂无记录',
        loadError: '加载数据失败，请检查后端服务是否启动',
        productNotFound: '未指定产品'
    },
    en: {
        pageTitle: 'Warehouse System - Dashboard',
        detailPageTitle: 'Product Detail - Warehouse System',
        systemTitle: 'Warehouse System',
        subtitle: 'Statistics Overview',
        refresh: 'Refresh',
        back: 'Back',
        productDetail: 'Product Detail',
        productStockDetail: 'Product Stock Detail',

        // Tab names
        tabDashboard: 'Dashboard',
        tabRecords: 'In/Out Records',
        tabInventory: 'Inventory List',
        tabDetail: 'Product Detail',

        totalStock: 'Total Stock',
        todayIn: 'Today In',
        todayOut: 'Today Out',
        lowStockAlert: 'Low Stock Alert',
        currentStock: 'Current Stock',
        safeStock: 'Safe Stock',
        unit: 'pcs',
        materialTypes: 'types',

        weeklyTrend: '7-Day In/Out Trend',
        categoryDistribution: 'Category Distribution',
        topStock: 'Top 10 Stock',
        inOutRatio: 'In/Out Ratio',

        inbound: 'Inbound',
        outbound: 'Outbound',

        inventoryList: 'Inventory List',
        searchPlaceholder: 'Search product name...',
        autoUpdate: 'Auto update:',
        seconds: 's',

        materialName: 'Material Name',
        materialCode: 'Material Code',
        type: 'Type',
        currentStockCol: 'Current Stock',
        unitCol: 'Unit',
        safeStockCol: 'Safe Stock',
        status: 'Status',
        location: 'Location',

        inOutRecords: 'In/Out Records',
        time: 'Time',
        quantity: 'Quantity',
        operator: 'Operator',
        reason: 'Reason',

        statusNormal: 'Normal',
        statusWarning: 'Low',
        statusDanger: 'Critical',
        statusDisabled: 'Disabled',

        // Filtering
        filterStartDate: 'Start Date',
        filterEndDate: 'End Date',
        filterProduct: 'Name/Code',
        filterCategory: 'Category',
        filterType: 'Type',
        filterStatus: 'Status',
        filterBtn: 'Filter',
        resetBtn: 'Reset',
        allTypes: 'All Types',
        allCategories: 'All Categories',
        allStatuses: 'All Statuses',

        // Pagination
        prevPage: 'Previous',
        nextPage: 'Next',
        pageSize: 'Per Page',
        totalRecords: 'Total: {count} records',
        recordsUnit: 'records',
        pageInfo: 'Page {page} of {total}',

        // Product selection
        selectProductHint: 'Please select a product to view details',

        // Excel import/export
        exportInventory: 'Export Inventory',
        importInventory: 'Import Inventory',
        exportRecords: 'Export Records',
        addRecord: 'Add Record',
        dropFileHere: 'Click to select Excel file',
        importPreview: 'Import Preview',
        totalInLabel: 'Total In:',
        totalOutLabel: 'Total Out:',
        totalNewLabel: 'New Materials:',
        importQty: 'Import Qty',
        difference: 'Diff',
        operation: 'Operation',
        operatorPlaceholder: 'Enter operator name',
        reasonPlaceholder: 'Enter import reason',
        cancel: 'Cancel',
        confirmImport: 'Confirm Import',
        newMaterial: 'New',
        noChange: 'No Change',

        // New SKU confirmation
        confirmNewSku: 'Confirm New Materials',
        newSkuWarning: 'The following SKUs do not exist in the system and will be created:',
        skipNewSkus: 'Skip New',
        confirmCreate: 'Confirm Create',

        // Add record
        addInventoryRecord: 'Add Inventory Record',
        selectProduct: 'Select Product',
        pleaseSelect: '-- Please Select --',
        operationType: 'Operation Type',
        submit: 'Submit',
        productName: 'Product',

        // Error messages
        fillOperatorAndReason: 'Please fill in operator and reason',
        fillAllFields: 'Please fill in all fields',
        quantityMustBePositive: 'Quantity must be greater than 0',
        parseFileFailed: 'Failed to parse file',
        previewFailed: 'Preview failed',
        importFailed: 'Import failed',
        operationFailed: 'Operation failed',

        noData: 'No data',
        noRecords: 'No records',
        loadError: 'Failed to load data. Please check if the backend service is running.',
        productNotFound: 'Product not specified'
    }
};

// 当前语言
let currentLang = localStorage.getItem('lang') || 'zh';

// 获取翻译
function t(key) {
    return translations[currentLang][key] || key;
}

// 设置语言
function setLanguage(lang) {
    currentLang = lang;
    localStorage.setItem('lang', lang);
    updatePageTexts();
}

// 更新页面所有静态文本
function updatePageTexts() {
    document.querySelectorAll('[data-i18n]').forEach(el => {
        const key = el.getAttribute('data-i18n');
        if (el.tagName === 'INPUT') {
            el.placeholder = t(key);
        } else {
            el.textContent = t(key);
        }
    });
    updateLangDropdownDisplay();
}

// 更新语言下拉菜单显示
function updateLangDropdownDisplay() {
    const el = document.getElementById('current-lang-text');
    if (el) {
        el.textContent = currentLang === 'zh' ? '中文简体' : 'English';
    }
}

// 切换下拉菜单显示
function toggleLangDropdown() {
    const menu = document.getElementById('lang-dropdown-menu');
    if (menu) {
        menu.classList.toggle('show');
    }
}

// 选择语言
function selectLanguage(lang) {
    setLanguage(lang);
    const menu = document.getElementById('lang-dropdown-menu');
    if (menu) {
        menu.classList.remove('show');
    }
    updateLangOptionActive();
    // 触发页面刷新回调（在各页面JS中定义）
    if (typeof onLanguageChange === 'function') {
        onLanguageChange();
    }
}

// 更新选中状态
function updateLangOptionActive() {
    document.querySelectorAll('.lang-option').forEach((opt, i) => {
        opt.classList.toggle('active', (i === 0 && currentLang === 'zh') || (i === 1 && currentLang === 'en'));
    });
}

// 点击页面其他地方关闭下拉菜单
document.addEventListener('click', function(e) {
    if (!e.target.closest('.lang-dropdown')) {
        const menu = document.getElementById('lang-dropdown-menu');
        if (menu) {
            menu.classList.remove('show');
        }
    }
});

// 页面加载时初始化
document.addEventListener('DOMContentLoaded', function() {
    updatePageTexts();
    updateLangOptionActive();
});
