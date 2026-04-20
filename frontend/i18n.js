// 翻译数据
const translations = {
    zh: {
        // 通用
        pageTitle: '智能仓管系统 - 仪表盘',
        detailPageTitle: '产品详情 - 智能仓管系统',
        systemTitle: '智能仓管系统',
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
        searchPlaceholder: '搜索产品名称或编码...',
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
        reasonCategory: '原因类别',
        reasonNote: '备注',
        reasonNotePlaceholder: '选填，如借给谁',
        allReasonCategories: '全部类别',
        // 原因分类标签
        reasonPurchase: '采购入库',
        reasonReturn: '借还',
        reasonRefund: '退货入库',
        reasonProduce: '生产入库',
        reasonTransferIn: '调拨入库',
        reasonOtherIn: '其他',
        reasonSell: '出售',
        reasonLend: '借出',
        reasonConsume: '领用/消耗',
        reasonLoss: '损耗/损失',
        reasonTransferOut: '调拨出库',
        reasonOtherOut: '其他',
        batch: '批次',
        batchNo: '批次号',
        variant: '规格',
        batchDetails: '批次消耗',
        outboundBatch: '指定批次',
        autoFIFO: '-- FIFO 自动分配 --',
        variantPlaceholder: '如：红、大号（选填）',
        batchLoadFailed: '加载批次列表失败',
        batchLocationChipPrefix: '库位：',
        batchVariantChipPrefix: '变体：',

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
        selectFile: '选择Excel文件',
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
        reasonSearchPlaceholder: '搜索备注...',
        reasonCategoryPlaceholder: '选择原因类别',
        missingSku: '不在导入文件中的SKU',
        items: '项',
        disableMissingSkus: '禁用导入文件外的SKU',
        disableMissingSkusHint: '勾选后将把本次未出现的SKU标记为禁用，谨慎操作；不勾选则跳过禁用，其他变更照常执行。',
        cancel: '取消',
        confirmImport: '确认导入',
        newMaterial: '新增',
        noChange: '无变化',
        noChangesToImport: '数据无变化，无需导入',

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
        sessionExpired: '登录已过期，请重新登录',
        passwordMismatch: '两次密码不一致',

        // 系统设置
        tabUsers: '系统设置',
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
        delete: '删除',
        confirmDeleteApiKey: '确定要删除API密钥 "{name}" 吗？此操作不可撤销。',
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

        // 数据库管理
        databaseManagement: '数据库管理',
        databaseManagementDesc: '管理仓库数据的导出、导入和清空操作。用户账户和API密钥不受影响。',
        exportDatabase: '导出数据库',
        exportDatabaseDesc: '将仓库数据下载为备份文件',
        importDatabase: '导入数据库',
        importDatabaseDesc: '从备份文件恢复仓库数据',
        clearDatabase: '清空数据库',
        clearDatabaseDesc: '删除所有仓库数据（用户不受影响）',
        export: '导出',
        import: '导入',
        clear: '清空',
        selectDatabaseFile: '选择数据库文件',
        selectDbFileHint: '点击选择 .db 文件',
        importDatabaseWarning: '导入将替换所有现有仓库数据（物料、出入库记录、批次、联系方），此操作不可撤销。用户账户和API密钥不受影响。',
        clearDatabaseWarning: '这将永久删除所有仓库数据（物料、出入库记录、批次、联系方）。用户账户和API密钥不受影响。',
        clearDatabaseQuestion: '是否要在清空前先导出现有数据？',
        exportThenClear: '先导出再清空',
        directClear: '直接清空',
        confirmImport: '确认导入',
        importing: '导入中...',
        importFailed: '导入失败，请检查文件格式',
        operationFailed: '操作失败，请重试',
        confirmDirectClear: '确定要直接清空所有仓库数据吗？此操作不可撤销！',

        // 联系方管理
        tabContacts: '联系方管理',
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
        contact: '联系方',

        // MCP (智能体) 管理
        tabMCP: '智能体配置',
        mcpConnectionList: '连接列表',
        mcpAddConnection: '添加智能体',
        mcpEditConnection: '编辑智能体',
        mcpName: '名称',
        mcpRole: '权限角色',
        mcpUptime: '运行时长',
        mcpAutoStart: '自动启动',
        mcpAutoStartYes: '是',
        mcpAutoStartNo: '否',
        mcpAutoStartLabel: '系统启动时自动连接',
        mcpStatusRunning: '运行中',
        mcpStatusStopped: '已停止',
        mcpStatusError: '错误',
        mcpStart: '启动',
        mcpStop: '停止',
        mcpRestart: '重启',
        mcpSaveAndStart: '保存并启动',
        mcpConfirmDelete: '确定要删除智能体 "{name}" 吗？',
        mcpNoConnections: '暂无智能体连接，点击"添加智能体"开始配置',
        mcpNoLogs: '暂无日志',

        // 批次
        batchDetail: '批次明细',
        batchMode: '批次模式',
        newContacts: '新联系方',
        noBatchData: '暂无批次数据',
        noPermission: '权限不足，请先登录或联系管理员',
        batchNoPlaceholder: '留空自动生成',

        // ERP 系统模式
        tabERP: '系统模式',
        erpSelfOwned: '自有系统模式',
        erpSelfOwnedDesc: '使用本地数据库，数据存储在本地',
        erpExternal: '外接 ERP 模式',
        erpExternalDesc: '对接外部 ERP 系统，所有操作路由到远程',
        erpCurrentMode: '当前模式',
        erpProviderList: '已配置的外部系统',
        erpUploadProvider: '上传 Provider',
        erpTargetUrl: '目标地址',
        erpTestStatus: '测试状态',
        erpStatusDashboard: '接口状态看板',
        erpTestLevel1: 'Level 1 只读测试',
        erpTestLevel2: 'Level 2 写操作测试',
        erpActivate: '激活',
        erpDeactivate: '停用',
        erpActive: '激活中',
        erpInactive: '未激活',
        erpTestPassed: '通过',
        erpTestFailed: '未通过',
        erpTestNotRun: '未测试',
        erpUploadTitle: '上传 Provider',
        erpUploadStep1: '上传文件',
        erpUploadStep2: '校验 & 配置',
        erpUploadStep3: 'Level 1 测试',
        erpUploadStep4: 'Level 2 测试',
        erpUploadStep5: '结果 & 激活',
        erpUploadDragHint: '拖拽 .py 文件到此处，或点击选择',
        erpUploadSizeHint: '要求：实现 BaseProvider 的 Python 文件，最大 100KB',
        erpValidationPassed: '文件校验通过',
        erpValidationFailed: '文件校验失败',
        erpConfigTitle: '连接配置',
        erpApiBaseUrl: 'API 地址',
        erpAuthType: '认证方式',
        erpAuthApiKey: 'API Key',
        erpAuthBearer: 'Bearer Token',
        erpAuthBasic: 'Basic Auth',
        erpAuthCustom: '自定义',
        erpTimeout: '超时 (秒)',
        erpTestRunning: '测试中...',
        erpTestAllPassed: '测试全部通过',
        erpTestSomeFailed: '部分测试未通过',
        erpLevel2Warning: '写操作测试会在目标 ERP 中产生真实数据',
        erpLevel2Detail: '系统将执行一次入库 + 一次出库操作（测试数量: 1），测试后不会自动回滚。',
        erpSkipLevel2: '跳过，直接到结果',
        erpRunLevel2: '执行 Level 2 测试',
        erpActivateNow: '立即激活此 Provider',
        erpActivateLater: '稍后激活',
        erpActivateRequireL1: '需要 Level 1 测试全部通过才能激活',
        erpConfirmDelete: '确定要删除 Provider "{name}" 吗？',
        erpConfirmModeSwitch: '确定要切换系统模式吗？',
        erpNoProviders: '暂无 Provider 配置。点击"上传 Provider"开始。',
        erpLastCheck: '上次检测',
        erpServerStatus: '服务器连通性',
        erpNormal: '正常',
        erpAbnormal: '异常',

        // 系统设置子 Tab
        settingsUsersKeys: '用户与密钥',
        settingsWarehouses: '仓库',
        settingsDataMgmt: '数据管理',

        // 仓库
        allWarehouses: '全部仓库',
        switchWarehouse: '切换仓库',
        warehouseManagement: '仓库管理',
        warehouseName: '仓库名称',
        warehouseSlug: '仓库标识',
        warehouseAddress: '仓库地址',
        assignedWarehouses: '授权仓库',
        noWarehouseAccess: '无仓库访问权限',
        selectWarehouse: '请选择仓库',
        defaultWarehouse: '默认仓库',
        addWarehouse: '添加仓库',
        editWarehouse: '编辑仓库',
        deleteWarehouse: '删除仓库',
        confirmDeleteWarehouse: '确定删除仓库',
        deleteWarehouseWarning: '该仓库下的所有物料和记录也会被删除！',
        warehouseNameRequired: '请输入仓库名称',
        warehouseSlugRequired: '请输入仓库标识',
        slugHint: '用于 URL 路径，只能包含小写字母、数字和连字符',
        isDefault: '默认',
        warehouseColumn: '仓库',
        writeRequiresWarehouse: '写操作需要选择具体仓库',
        cannotDeleteDefault: '不能删除默认仓库',
    },
    en: {
        pageTitle: 'Smart Warehouse - Dashboard',
        detailPageTitle: 'Product Detail - Smart Warehouse',
        systemTitle: 'Smart Warehouse',
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
        searchPlaceholder: 'Search by name or code...',
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
        reasonCategory: 'Reason Category',
        reasonNote: 'Note',
        reasonNotePlaceholder: 'Optional, e.g. borrower name',
        allReasonCategories: 'All Categories',
        reasonPurchase: 'Purchase',
        reasonReturn: 'Return',
        reasonRefund: 'Refund',
        reasonProduce: 'Production',
        reasonTransferIn: 'Transfer In',
        reasonOtherIn: 'Other',
        reasonSell: 'Sale',
        reasonLend: 'Lend',
        reasonConsume: 'Consume',
        reasonLoss: 'Loss',
        reasonTransferOut: 'Transfer Out',
        reasonOtherOut: 'Other',
        batch: 'Batch',
        batchNo: 'Batch No.',
        variant: 'Variant',
        batchDetails: 'Batch Consumption',
        outboundBatch: 'Specific Batch',
        autoFIFO: '-- Auto (FIFO) --',
        variantPlaceholder: 'e.g. Red, Large (optional)',
        batchLoadFailed: 'Failed to load batches',
        batchLocationChipPrefix: 'Location: ',
        batchVariantChipPrefix: 'Variant: ',

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
        selectFile: 'Select Excel File',
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
        reasonSearchPlaceholder: 'Search notes...',
        reasonCategoryPlaceholder: 'Select reason category',
        missingSku: 'SKUs not in import file',
        items: 'items',
        disableMissingSkus: 'Disable SKUs not in this file',
        disableMissingSkusHint: 'If checked, SKUs missing from this file will be disabled. Leave unchecked to skip disabling; other changes will still apply.',
        cancel: 'Cancel',
        confirmImport: 'Confirm Import',
        newMaterial: 'New',
        noChange: 'No Change',
        noChangesToImport: 'No changes to import',

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
        sessionExpired: 'Session expired, please log in again',
        passwordMismatch: 'Passwords do not match',

        // System Settings
        tabUsers: 'Settings',
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
        delete: 'Delete',
        confirmDeleteApiKey: 'Are you sure you want to delete API key "{name}"? This action cannot be undone.',
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

        // Database Management
        databaseManagement: 'Database Management',
        databaseManagementDesc: 'Manage warehouse data export, import and clear operations. User accounts and API keys are not affected.',
        exportDatabase: 'Export Database',
        exportDatabaseDesc: 'Download warehouse data as a backup file',
        importDatabase: 'Import Database',
        importDatabaseDesc: 'Restore warehouse data from a backup file',
        clearDatabase: 'Clear Database',
        clearDatabaseDesc: 'Remove all warehouse data (users unaffected)',
        export: 'Export',
        import: 'Import',
        clear: 'Clear',
        selectDatabaseFile: 'Select Database File',
        selectDbFileHint: 'Click to select .db file',
        importDatabaseWarning: 'Importing will replace all existing warehouse data (materials, inventory records, batches, contacts). This action cannot be undone. User accounts and API keys are not affected.',
        clearDatabaseWarning: 'This will permanently delete all warehouse data (materials, inventory records, batches, contacts). User accounts and API keys are not affected.',
        clearDatabaseQuestion: 'Would you like to export the current data before clearing?',
        exportThenClear: 'Export Then Clear',
        directClear: 'Clear Without Export',
        confirmImport: 'Confirm Import',
        importing: 'Importing...',
        importFailed: 'Import failed, please check file format',
        operationFailed: 'Operation failed, please try again',
        confirmDirectClear: 'Are you sure you want to clear all warehouse data? This action cannot be undone!',

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
        contact: 'Contact',

        // MCP (Agent) Management
        tabMCP: 'Agent Config',
        mcpConnectionList: 'Connection List',
        mcpAddConnection: 'Add Agent',
        mcpEditConnection: 'Edit Agent',
        mcpName: 'Name',
        mcpRole: 'Role',
        mcpUptime: 'Uptime',
        mcpAutoStart: 'Auto Start',
        mcpAutoStartYes: 'Yes',
        mcpAutoStartNo: 'No',
        mcpAutoStartLabel: 'Auto-connect on system startup',
        mcpStatusRunning: 'Running',
        mcpStatusStopped: 'Stopped',
        mcpStatusError: 'Error',
        mcpStart: 'Start',
        mcpStop: 'Stop',
        mcpRestart: 'Restart',
        mcpSaveAndStart: 'Save & Start',
        mcpConfirmDelete: 'Are you sure you want to delete agent "{name}"?',
        mcpNoConnections: 'No agent connections. Click "Add Agent" to get started.',
        mcpNoLogs: 'No logs available',

        // Batch
        batchDetail: 'Batch Details',
        batchMode: 'Batch Mode',
        newContacts: 'New Contacts',
        noBatchData: 'No batch data',
        noPermission: 'Permission denied. Please login or contact admin.',
        batchNoPlaceholder: 'Leave empty to auto-generate',

        // ERP System Mode
        tabERP: 'System Mode',
        erpSelfOwned: 'Self-owned Mode',
        erpSelfOwnedDesc: 'Uses local database for data storage',
        erpExternal: 'External ERP Mode',
        erpExternalDesc: 'Connect to external ERP, all operations routed remotely',
        erpCurrentMode: 'Current Mode',
        erpProviderList: 'Configured Providers',
        erpUploadProvider: 'Upload Provider',
        erpTargetUrl: 'Target URL',
        erpTestStatus: 'Test Status',
        erpStatusDashboard: 'API Status Dashboard',
        erpTestLevel1: 'Level 1 Read-only Test',
        erpTestLevel2: 'Level 2 Write Test',
        erpActivate: 'Activate',
        erpDeactivate: 'Deactivate',
        erpActive: 'Active',
        erpInactive: 'Inactive',
        erpTestPassed: 'Passed',
        erpTestFailed: 'Failed',
        erpTestNotRun: 'Not Tested',
        erpUploadTitle: 'Upload Provider',
        erpUploadStep1: 'Upload File',
        erpUploadStep2: 'Validate & Configure',
        erpUploadStep3: 'Level 1 Test',
        erpUploadStep4: 'Level 2 Test',
        erpUploadStep5: 'Results & Activate',
        erpUploadDragHint: 'Drag .py file here, or click to select',
        erpUploadSizeHint: 'Requires: Python file implementing BaseProvider, max 100KB',
        erpValidationPassed: 'File validation passed',
        erpValidationFailed: 'File validation failed',
        erpConfigTitle: 'Connection Configuration',
        erpApiBaseUrl: 'API Base URL',
        erpAuthType: 'Auth Type',
        erpAuthApiKey: 'API Key',
        erpAuthBearer: 'Bearer Token',
        erpAuthBasic: 'Basic Auth',
        erpAuthCustom: 'Custom',
        erpTimeout: 'Timeout (seconds)',
        erpTestRunning: 'Testing...',
        erpTestAllPassed: 'All tests passed',
        erpTestSomeFailed: 'Some tests failed',
        erpLevel2Warning: 'Write tests will create real data in the target ERP',
        erpLevel2Detail: 'System will perform one stock-in and one stock-out (quantity: 1). Data will not be auto-rolled back.',
        erpSkipLevel2: 'Skip to results',
        erpRunLevel2: 'Run Level 2 Test',
        erpActivateNow: 'Activate This Provider Now',
        erpActivateLater: 'Activate Later',
        erpActivateRequireL1: 'Level 1 tests must all pass before activation',
        erpConfirmDelete: 'Are you sure you want to delete Provider "{name}"?',
        erpConfirmModeSwitch: 'Are you sure you want to switch system mode?',
        erpNoProviders: 'No providers configured. Click "Upload Provider" to get started.',
        erpLastCheck: 'Last check',
        erpServerStatus: 'Server connectivity',
        erpNormal: 'Normal',
        erpAbnormal: 'Abnormal',

        // Settings Sub-Tabs
        settingsUsersKeys: 'Users & Keys',
        settingsWarehouses: 'Warehouses',
        settingsDataMgmt: 'Data Management',

        // Warehouse
        allWarehouses: 'All Warehouses',
        switchWarehouse: 'Switch Warehouse',
        warehouseManagement: 'Warehouse Management',
        warehouseName: 'Warehouse Name',
        warehouseSlug: 'Warehouse ID',
        warehouseAddress: 'Address',
        assignedWarehouses: 'Assigned Warehouses',
        noWarehouseAccess: 'No warehouse access',
        selectWarehouse: 'Select Warehouse',
        defaultWarehouse: 'Default Warehouse',
        addWarehouse: 'Add Warehouse',
        editWarehouse: 'Edit Warehouse',
        deleteWarehouse: 'Delete Warehouse',
        confirmDeleteWarehouse: 'Confirm delete warehouse',
        deleteWarehouseWarning: 'All materials and records in this warehouse will be deleted!',
        warehouseNameRequired: 'Please enter warehouse name',
        warehouseSlugRequired: 'Please enter warehouse slug',
        slugHint: 'Used in URL path, only lowercase letters, numbers and hyphens allowed',
        isDefault: 'Default',
        warehouseColumn: 'Warehouse',
        writeRequiresWarehouse: 'Write operations require selecting a specific warehouse',
        cannotDeleteDefault: 'Cannot delete default warehouse',
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
    if (!e.target.closest('.warehouse-switcher')) {
        const menu = document.getElementById('warehouseDropdown');
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

// 导出到 window 对象（用于 ES Module 环境）
window.t = t;
window.translations = translations;
window.currentLang = currentLang;
window.setLanguage = setLanguage;
window.updatePageTexts = updatePageTexts;
window.toggleLangDropdown = toggleLangDropdown;
window.selectLanguage = selectLanguage;

// ES Module 导出
export { translations, t, setLanguage, updatePageTexts, toggleLangDropdown, selectLanguage };
