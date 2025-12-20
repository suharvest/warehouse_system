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
        categoryDist: '库存分类分布',
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
        productCategory: '商品类型',
        recordType: '记录类型',
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
        batch: '批次',
        batchNo: '批次号',
        batchDetails: '批次消耗',

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
        allContacts: '全部联系方',
        allOperators: '全部操作员',

        // 分页
        prevPage: '上一页',
        nextPage: '下一页',
        pageSize: '每页',
        totalRecords: '共',
        recordsUnit: '条记录',
        pageInfo: '第 {page} 页 / 共 {total} 页',

        // 产品选择
        selectProductHint: '请选择产品查看详情',

        // Excel导入导出
        exportInventory: '导出库存',
        importInventory: '导入库存',
        exportRecords: '导出记录',
        backToInventory: '返回列表',
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
        reasonSearchPlaceholder: '搜索原因...',
        disableMissingSkus: '禁用导入文件外的SKU',
        disableMissingSkusHint: '勾选后将把本次未出现的SKU标记为禁用，谨慎操作；不勾选则跳过禁用，其他变更照常执行。',
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
        productNotFound: '未指定产品',

        // 用户认证
        guest: '访客',
        login: '登录',
        logout: '登出',
        username: '用户名',
        password: '密码',
        confirmPassword: '确认密码',
        displayName: '显示名称',
        setupAdmin: '设置管理员',
        setupHint: '首次使用请创建管理员账号',
        createAdmin: '创建管理员',
        loginFailed: '登录失败',
        passwordMismatch: '两次密码不一致',

        // 用户管理
        tabUsers: '用户管理',
        userList: '用户列表',
        addUser: '添加用户',
        editUser: '编辑用户',
        role: '角色',
        roleView: '只读',
        roleOperate: '操作员',
        roleAdmin: '管理员',
        createdAt: '创建时间',
        actions: '操作',
        disable: '禁用',
        enable: '启用',
        edit: '编辑',
        enabled: '正常',
        disabled: '已禁用',
        newPassword: '新密码',
        leaveEmptyToKeep: '留空保持不变',
        passwordTooShort: '密码长度至少4位',

        // API密钥
        apiKeyList: 'API密钥',
        addApiKey: '添加API密钥',
        keyName: '名称',
        lastUsedAt: '最后使用',
        apiKeyCreated: 'API密钥已创建',
        apiKeyWarning: '请妥善保存此密钥，关闭后将无法再次查看：',
        copied: '已复制',
        confirm: '确定',
        never: '从未',

        // 联系方管理
        tabContacts: '联系方',
        contactList: '联系方列表',
        addContact: '添加联系方',
        editContact: '编辑联系方',
        contactName: '名称',
        contactType: '类型',
        supplier: '供应商',
        customer: '客户',
        phone: '电话',
        email: '邮箱',
        address: '地址',
        notes: '备注',
        bothType: '供应商/客户',
        contactMustSelectType: '必须选择供应商或客户至少一项',
        contact: '联系方'
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
        categoryDist: 'Category Distribution',
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
        productCategory: 'Product Category',
        recordType: 'Record Type',
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
        batch: 'Batch',
        batchNo: 'Batch No.',
        batchDetails: 'Batch Consumption',

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
        allContacts: 'All Contacts',
        allOperators: 'All Operators',

        // Pagination
        prevPage: 'Previous',
        nextPage: 'Next',
        pageSize: 'Per Page',
        totalRecords: 'Total:',
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
        reasonSearchPlaceholder: 'Search reason...',
        disableMissingSkus: 'Disable SKUs not in this file',
        disableMissingSkusHint: 'If checked, SKUs missing from this file will be disabled. Leave unchecked to skip disabling; other changes will still apply.',
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
        productNotFound: 'Product not specified',

        // User authentication
        guest: 'Guest',
        login: 'Login',
        logout: 'Logout',
        username: 'Username',
        password: 'Password',
        confirmPassword: 'Confirm Password',
        displayName: 'Display Name',
        setupAdmin: 'Setup Admin',
        setupHint: 'Please create an admin account for first-time use',
        createAdmin: 'Create Admin',
        loginFailed: 'Login failed',
        passwordMismatch: 'Passwords do not match',

        // User management
        tabUsers: 'User Management',
        userList: 'User List',
        addUser: 'Add User',
        editUser: 'Edit User',
        role: 'Role',
        roleView: 'View Only',
        roleOperate: 'Operator',
        roleAdmin: 'Admin',
        createdAt: 'Created At',
        actions: 'Actions',
        disable: 'Disable',
        enable: 'Enable',
        edit: 'Edit',
        enabled: 'Active',
        disabled: 'Disabled',
        newPassword: 'New Password',
        leaveEmptyToKeep: 'Leave empty to keep current',
        passwordTooShort: 'Password must be at least 4 characters',

        // API Keys
        apiKeyList: 'API Keys',
        addApiKey: 'Add API Key',
        keyName: 'Name',
        lastUsedAt: 'Last Used',
        apiKeyCreated: 'API Key Created',
        apiKeyWarning: 'Please save this key safely. It will not be shown again:',
        copied: 'Copied',
        confirm: 'OK',
        never: 'Never',

        // Contact Management
        tabContacts: 'Contacts',
        contactList: 'Contact List',
        addContact: 'Add Contact',
        editContact: 'Edit Contact',
        contactName: 'Name',
        contactType: 'Type',
        supplier: 'Supplier',
        customer: 'Customer',
        phone: 'Phone',
        email: 'Email',
        address: 'Address',
        notes: 'Notes',
        bothType: 'Supplier/Customer',
        contactMustSelectType: 'Must select at least one: Supplier or Customer',
        contact: 'Contact'
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
document.addEventListener('click', function (e) {
    if (!e.target.closest('.lang-dropdown')) {
        const menu = document.getElementById('lang-dropdown-menu');
        if (menu) {
            menu.classList.remove('show');
        }
    }
});

// 页面加载时初始化
document.addEventListener('DOMContentLoaded', function () {
    updatePageTexts();
    updateLangOptionActive();
});
