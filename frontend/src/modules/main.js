// ============ 主入口模块 ============
import { t } from '../../i18n.js';
import { inventoryApi, warehousesApi } from './api.js';
import {
    getAllCategories, setAllCategories, getAllProducts, setAllProducts,
    getCurrentTab, getRecordsCurrentPage, getInventoryCurrentPage, getDetailCurrentPage, getContactsCurrentPage,
    setAllWarehouses, getAllWarehouses
} from './state.js';

// UI 模块
import { initDropdownListeners, initSearchableSelect, setProductSelectorValue, clearProductSelector, clearRecordProductSelector, toggleDropdown, toggleDropdownItem } from './ui/dropdown.js';
import { initFilterDrawers, refreshFilterDrawerI18n } from './ui/filter-drawer.js';
import { switchTab, initFromHash, startAutoUpdate, refreshCurrentTab, goBackToInventory, setTabModules, renderWarehouseSwitcher, toggleWarehouseSwitcher, selectWarehouse } from './ui/tabs.js';

// 功能模块
import { checkAuthStatus, showLoginModal, closeLoginModal, handleLogin, handleLogout, showSetupModal, handleSetup, updateUserDisplay, updatePermissionUI, setAuthCallbacks, initSessionExpiredHandler } from './features/auth.js';
import { initCharts, loadDashboardData, onTotalStockClick, onTodayInClick, onTodayOutClick, onLowStockClick, setDashboardCallbacks } from './features/dashboard.js';
import { loadInventory, inventoryGoToPage, changeInventoryPageSize, applyInventoryFilter, resetInventoryFilter, applyInventoryFilters, setInventoryCallbacks } from './features/inventory.js';
import { loadRecords, recordsGoToPage, changeRecordsPageSize, applyRecordsFilter, resetRecordsFilter, applyRecordsFilters, loadRecordsFilterOptions, showAddRecordModal, showAddRecordModalForProduct, closeAddRecordModal, submitAddRecord, setRecordsCallbacks } from './features/records.js';
import { onProductSelect, initDetailCharts, loadProductDetail, loadProductTrend, loadDetailPieChart, loadProductRecords, detailGoToPage, changeDetailPageSize, refreshProductDetailForLanguage } from './features/product-detail.js';
import { exportInventory, exportRecords, exportProductRecords, showImportModal, closeImportModal, handleFileSelect, confirmImport, closeNewSkuModal, skipNewSkus, confirmNewSkus, setImportExportCallbacks } from './features/import-export.js';
import { loadUsers, showAddUserModal, closeAddUserModal, handleAddUser, showEditUserModal, closeEditUserModal, handleEditUser, toggleUserStatus, setUsersCallbacks, loadTenantInfo } from './features/users.js';
import { loadApiKeys, showAddApiKeyModal, closeAddApiKeyModal, handleAddApiKey, closeShowApiKeyModal, copyApiKey, disableApiKey, toggleApiKeyStatus, deleteApiKey } from './features/api-keys.js';
import { loadContacts, contactsGoToPage, changeContactsPageSize, applyContactsFilter, resetContactsFilter, showAddContactModal, closeContactModal, editContact, handleSaveContact, toggleContactStatus } from './features/contacts.js';
import { exportDatabase, showImportDatabaseModal, closeImportDatabaseModal, handleDatabaseFileSelect, confirmImportDatabase, showClearDatabaseModal, closeClearDatabaseModal, exportThenClearDatabase, directClearDatabase } from './features/database.js';
import { loadMCPConnections, showAddMCPModal, closeMCPModal, handleSaveMCP, editMCPConnection, startMCPConnection, stopMCPConnection, restartMCPConnection, deleteMCPConnection, startMCPRefresh, stopMCPRefresh } from './features/mcp.js';
import { loadWarehouses as loadWarehousesList, showAddWarehouseModal, showEditWarehouseModal, closeWarehouseModal, handleSaveWarehouse, toggleWarehouseStatus, deleteWarehouse, setWarehousesCallbacks, toggleWarehouseGroup } from './features/warehouses.js';
import { loadERPStatus, startERPRefresh, stopERPRefresh, showUploadWizard, closeUploadWizard, handleProviderUpload, saveProviderConfig, runProviderTest, activateProvider, deactivateProvider, deleteProvider, editProviderConfig, wizardNextStep, wizardPrevStep, switchSystemMode, wizardActivate, wizardRunLevel2, wizardGoToResults } from './features/erp.js';
import { fetchDeployMode, renderTenantsPanel, showAddTenantModal, closeAddTenantModal, handleAddTenant, showEditTenantModal, closeEditTenantModal, handleEditTenant, handleDeleteTenant, tenantsPrevPage, tenantsNextPage, getTenantModalsHTML, setTenantsPage } from './features/tenants.js';
import {
    renderFaceRecognitionPanel, switchFaceSubTab, refreshFacePanel,
    saveFaceConfig, testFaceConnection,
    showAddFaceRuleModal, editFaceRule, closeFaceRuleModal, saveFaceRule, deleteFaceRule,
    selectFaceSubject, showFaceEnrollModal, closeFaceEnrollModal, submitFaceEnroll, deleteFaceEnrollment,
    showAddFaceSubjectModal, showEditFaceSubjectModal, closeFaceSubjectModal, saveFaceSubject, deleteFaceSubject,
    applyFaceLogsFilter, resetFaceLogsFilter, faceLogsPrevPage, faceLogsNextPage,
    getFaceModalsHTML, onFaceTenantChange
} from './features/face-recognition.js';

// 语言切换
import { toggleLangDropdown, selectLanguage } from '../../i18n.js';

// ============ 模块回调设置 ============
function setupModuleCallbacks() {
    // 设置 Tab 模块引用
    setTabModules({
        loadDashboardData,
        loadRecords,
        loadRecordsFilterOptions,
        applyRecordsFilters,
        loadInventory,
        applyInventoryFilters,
        loadContacts,
        loadUsers,
        loadTenantInfo,
        loadApiKeys,
        loadProductDetail,
        setProductSelectorValue,
        loadMCPConnections,
        startMCPRefresh,
        stopMCPRefresh,
        loadERPStatus,
        startERPRefresh,
        stopERPRefresh,
        renderTenantsPanel,
        t,
        loadCategories,
        loadAllProducts,
        loadWarehousesList
    });

    // 设置认证回调
    setAuthCallbacks({
        onAuthChange: () => {},
        switchTab,
        refreshCurrentTab,
        onLoginSuccess: async () => {
            // /api/system/mode 需登录才返回，启动时若未登录会拿不到，登录成功后必须重新拉一次，
            // 否则 localStorage.deploy_mode 还是初始 single_tenant，多租户相关 UI（租户管理 tab、仓库分组等）不会显示。
            await fetchDeployMode();
            await loadWarehouses();
        }
    });

    // 设置看板回调
    setDashboardCallbacks({
        switchTab
    });

    // 设置库存回调
    setInventoryCallbacks({
        switchTab
    });

    // 设置记录回调
    setRecordsCallbacks({
        loadDashboardData,
        loadInventory,
        loadProductDetail,
        loadAllProducts
    });

    // 设置用户管理回调
    setUsersCallbacks({
        checkAuthStatus
    });

    // 设置仓库管理回调
    setWarehousesCallbacks({
        refreshSwitcher: loadWarehouses
    });

    // 设置导入导出回调
    setImportExportCallbacks({
        loadAllProducts,
        loadCategories,
        loadInventory,
        loadDashboardData
    });
}

// ============ 加载分类和产品 ============
async function loadCategories() {
    try {
        const categories = await inventoryApi.getCategories();
        setAllCategories(categories);
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

        const firstOption = select.options[0];
        select.innerHTML = '';
        select.appendChild(firstOption);

        getAllCategories().forEach(cat => {
            const option = document.createElement('option');
            option.value = cat;
            option.textContent = cat;
            select.appendChild(option);
        });
    });
}

async function loadAllProducts() {
    try {
        let page = 1;
        let allItems = [];
        let hasMore = true;

        while (hasMore) {
            const data = await inventoryApi.getList({
                page: page,
                pageSize: 100,
                status: ['normal', 'warning', 'danger', 'disabled']
            });
            if (data.items && data.items.length > 0) {
                allItems = allItems.concat(data.items);
                page++;
                hasMore = page <= data.total_pages;
            } else {
                hasMore = false;
            }
        }

        setAllProducts(allItems);
        populateProductSelector();
    } catch (error) {
        console.error('加载产品列表失败:', error);
        setAllProducts([]);
    }
}

function populateProductSelector() {
    if (!getAllProducts() || !Array.isArray(getAllProducts())) {
        setAllProducts([]);
    }

    // 产品详情页选择器
    initSearchableSelect({
        wrapperId: 'product-selector-wrapper',
        inputId: 'product-selector-input',
        dropdownId: 'product-selector-dropdown',
        hiddenId: 'product-selector',
        products: getAllProducts(),
        includeDisabled: true,
        showStock: false,
        onSelect: onProductSelect,
        placeholder: t('searchPlaceholder') || '搜索产品名称或编码...'
    });

    // 新增记录弹窗选择器
    initSearchableSelect({
        wrapperId: 'record-product-wrapper',
        inputId: 'record-product-input',
        dropdownId: 'record-product-dropdown',
        hiddenId: 'record-product',
        products: getAllProducts().filter(p => !p.is_disabled),
        includeDisabled: false,
        showStock: true,
        onSelect: () => {
            const qty = document.getElementById('record-quantity');
            if (qty) qty.focus();
            const recordType = document.querySelector('input[name="record-type"]:checked')?.value;
            if (recordType === 'out') {
                import('./features/records.js').then(m => m.populateBatchSelectForCurrentProduct?.());
            }
        },
        placeholder: t('searchPlaceholder') || '搜索产品名称或编码...'
    });
}

// ============ 仓库初始化 ============
async function loadWarehouses() {
    try {
        const data = await warehousesApi.getMyWarehouses();
        if (data && data.warehouses) {
            setAllWarehouses(data.warehouses);
            renderWarehouseSwitcher();
        }
    } catch (error) {
        console.error('加载仓库列表失败:', error);
    }
}

// ============ 语言变更回调 ============
function onLanguageChange() {
    document.title = t('pageTitle');
    populateProductSelector();
    populateCategorySelect();
    refreshFilterDrawerI18n();

    // 重新渲染当前Tab
    switch (getCurrentTab()) {
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
            refreshProductDetailForLanguage();
            break;
        case 'users':
            loadUsers();
            loadApiKeys();
            loadWarehousesList();
            loadTenantInfo();
            break;
        case 'mcp':
            loadMCPConnections();
            break;
    }
}

// ============ 事件委托系统 ============
const actionHandlers = {
    // 导航
    'switchTab': (el) => switchTab(el.dataset.tab),
    'goBackToInventory': goBackToInventory,
    'refreshCurrentTab': refreshCurrentTab,

    // 语言
    'toggleLangDropdown': toggleLangDropdown,
    'selectLanguage': (el) => selectLanguage(el.dataset.lang),

    // 登录/登出
    'showLoginModal': showLoginModal,
    'closeLoginModal': closeLoginModal,
    'handleLogin': () => handleLogin(new Event('click')),
    'handleLogout': handleLogout,
    'handleSetup': () => handleSetup(new Event('click')),

    // 用户管理
    'showAddUserModal': showAddUserModal,
    'closeAddUserModal': closeAddUserModal,
    'handleAddUser': handleAddUser,
    'showEditUserModal': (el) => showEditUserModal(
        el.dataset.userId,
        el.dataset.username,
        el.dataset.displayName,
        el.dataset.role
    ),
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
    'deleteApiKey': (el) => deleteApiKey(el.dataset.keyId, el.dataset.keyName),

    // 系统设置子 Tab 切换
    'switchSettingsSubTab': (el) => {
        const subTab = el.dataset.subTab;
        // 切换按钮样式（仅对顶层设置 sub-tabs，不影响人脸识别面板内的子标签）
        document.querySelectorAll('#tab-users > .sub-tabs > .sub-tab').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.subTab === subTab);
        });
        // 切换面板
        document.querySelectorAll('#tab-users > .settings-panel').forEach(panel => {
            panel.style.display = 'none';
        });
        const panel = document.getElementById(`settings-panel-${subTab}`);
        if (panel) panel.style.display = '';
        if (subTab === 'face-recognition') {
            renderFaceRecognitionPanel();
        }
    },

    // 人脸识别
    'switchFaceSubTab': (el) => switchFaceSubTab(el.dataset.subTab),
    'refreshFacePanel': refreshFacePanel,
    'saveFaceConfig': saveFaceConfig,
    'testFaceConnection': testFaceConnection,
    'showAddFaceRuleModal': showAddFaceRuleModal,
    'editFaceRule': editFaceRule,
    'closeFaceRuleModal': closeFaceRuleModal,
    'saveFaceRule': saveFaceRule,
    'deleteFaceRule': deleteFaceRule,
    'selectFaceSubject': selectFaceSubject,
    'showAddFaceSubjectModal': showAddFaceSubjectModal,
    'showEditFaceSubjectModal': showEditFaceSubjectModal,
    'closeFaceSubjectModal': closeFaceSubjectModal,
    'saveFaceSubject': saveFaceSubject,
    'deleteFaceSubject': deleteFaceSubject,
    'showFaceEnrollModal': showFaceEnrollModal,
    'closeFaceEnrollModal': closeFaceEnrollModal,
    'submitFaceEnroll': submitFaceEnroll,
    'deleteFaceEnrollment': deleteFaceEnrollment,
    'applyFaceLogsFilter': applyFaceLogsFilter,
    'resetFaceLogsFilter': resetFaceLogsFilter,
    'faceLogsPrevPage': faceLogsPrevPage,
    'faceLogsNextPage': faceLogsNextPage,
    'onFaceTenantChange': onFaceTenantChange,

    // 仓库管理
    'showAddWarehouseModal': (el) => showAddWarehouseModal(el?.dataset?.tenantId),
    'showEditWarehouseModal': (el) => showEditWarehouseModal(
        el.dataset.whId, el.dataset.whName, el.dataset.whSlug,
        el.dataset.whAddress, el.dataset.whIsDefault === 'true'
    ),
    'closeWarehouseModal': closeWarehouseModal,
    'handleSaveWarehouse': handleSaveWarehouse,
    'toggleWarehouseStatus': (el) => toggleWarehouseStatus(el.dataset.whId, el.dataset.isDisabled === 'true'),
    'deleteWarehouse': (el) => deleteWarehouse(el.dataset.whId, el.dataset.whName),
    'toggleWarehouseGroup': (el) => toggleWarehouseGroup(el),

    // 筛选器
    'applyRecordsFilter': applyRecordsFilter,
    'resetRecordsFilter': resetRecordsFilter,
    'applyInventoryFilter': applyInventoryFilter,
    'resetInventoryFilter': resetInventoryFilter,
    'applyContactsFilter': applyContactsFilter,
    'resetContactsFilter': resetContactsFilter,

    // 多选下拉
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
    'clearProductSelector': () => clearProductSelector(onProductSelect),
    'clearRecordProductSelector': clearRecordProductSelector,

    // 库存操作
    'showImportModal': showImportModal,
    'closeImportModal': closeImportModal,
    'triggerFileUpload': () => {
        const fileInput = document.getElementById('excel-file');
        if (fileInput) fileInput.click();
    },
    'handleFileSelect': (el, event) => handleFileSelect(event),
    'confirmImport': confirmImport,
    'exportInventory': exportInventory,

    // 新 SKU 确认
    'closeNewSkuModal': closeNewSkuModal,
    'skipNewSkus': skipNewSkus,
    'confirmNewSkus': confirmNewSkus,

    // 分页
    'recordsPrevPage': () => recordsGoToPage(getRecordsCurrentPage() - 1),
    'recordsNextPage': () => recordsGoToPage(getRecordsCurrentPage() + 1),
    'changeRecordsPageSize': (el) => changeRecordsPageSize(el.value),
    'inventoryPrevPage': () => inventoryGoToPage(getInventoryCurrentPage() - 1),
    'inventoryNextPage': () => inventoryGoToPage(getInventoryCurrentPage() + 1),
    'changeInventoryPageSize': (el) => changeInventoryPageSize(el.value),
    'detailPrevPage': () => detailGoToPage(getDetailCurrentPage() - 1),
    'detailNextPage': () => detailGoToPage(getDetailCurrentPage() + 1),
    'changeDetailPageSize': (el) => changeDetailPageSize(el.value),
    'contactsPrevPage': () => contactsGoToPage(getContactsCurrentPage() - 1),
    'contactsNextPage': () => contactsGoToPage(getContactsCurrentPage() + 1),
    'changeContactsPageSize': (el) => changeContactsPageSize(el.value),

    // 联系方管理
    'showAddContactModal': showAddContactModal,
    'closeContactModal': closeContactModal,
    'handleSaveContact': handleSaveContact,
    'editContact': (el) => editContact(el.dataset.contactId),
    'toggleContactStatus': (el) => toggleContactStatus(el.dataset.contactId, el.dataset.isDisabled === 'true'),

    // 统计卡片点击
    'onTotalStockClick': onTotalStockClick,
    'onTodayInClick': onTodayInClick,
    'onTodayOutClick': onTodayOutClick,
    'onLowStockClick': onLowStockClick,

    // 数据库管理
    'exportDatabase': exportDatabase,
    'showImportDatabaseModal': showImportDatabaseModal,
    'closeImportDatabaseModal': closeImportDatabaseModal,
    'handleDatabaseFileSelect': (el, event) => handleDatabaseFileSelect(event),
    'confirmImportDatabase': confirmImportDatabase,
    'showClearDatabaseModal': showClearDatabaseModal,
    'closeClearDatabaseModal': closeClearDatabaseModal,
    'exportThenClearDatabase': exportThenClearDatabase,
    'directClearDatabase': directClearDatabase,

    // 租户管理
    'showAddTenantModal': showAddTenantModal,
    'closeAddTenantModal': closeAddTenantModal,
    'handleAddTenant': handleAddTenant,
    'editTenant': (el) => showEditTenantModal(
        el.dataset.tenantId,
        el.dataset.tenantName,
        el.dataset.tenantActive
    ),
    'closeEditTenantModal': closeEditTenantModal,
    'handleEditTenant': handleEditTenant,
    'deleteTenant': (el) => handleDeleteTenant(
        parseInt(el.dataset.tenantId),
        el.dataset.tenantName
    ),
    'tenantsPrevPage': tenantsPrevPage,
    'tenantsNextPage': tenantsNextPage,
    'toggleTenantExpand': (el) => import('./features/tenants.js').then(m => m.toggleTenantExpand(el)),
    'goToAddWarehouse': () => import('./features/tenants.js').then(m => m.goToAddWarehouse()),
    'goToUsers': () => import('./features/tenants.js').then(m => m.goToUsers()),
    'refreshTenantsPanel': () => import('./features/tenants.js').then(m => m.refreshTenantsPanel()),

    // ERP 系统模式管理
    'showUploadWizard': showUploadWizard,
    'closeUploadWizard': closeUploadWizard,
    'handleProviderUpload': handleProviderUpload,
    'saveProviderConfig': saveProviderConfig,
    'wizardNextStep': wizardNextStep,
    'wizardPrevStep': wizardPrevStep,
    'switchToSelfOwned': () => switchSystemMode('self_owned'),
    'switchToERP': () => switchSystemMode('external_erp'),
    'erpActivate': (el) => activateProvider(el.dataset.providerId),
    'erpDeactivate': (el) => deactivateProvider(el.dataset.providerId),
    'erpDelete': (el) => deleteProvider(el.dataset.providerId),
    'erpEdit': (el) => editProviderConfig(el.dataset.providerId),
    'erpRunTest1': (el) => runProviderTest(el.dataset.providerId, 1),
    'erpRunTest2': (el) => runProviderTest(el.dataset.providerId, 2),
    'erpWizardRunLevel2': () => wizardRunLevel2(),
    'erpWizardGoToResults': () => wizardGoToResults(),
    'erpFileChanged': (el) => {
        const label = document.getElementById('erp-file-name-label');
        if (label && el.files && el.files[0]) label.textContent = el.files[0].name;
    },
    'erpWizardActivate': () => wizardActivate(),

    // 仓库切换
    'toggleWarehouseSwitcher': toggleWarehouseSwitcher,
    'selectWarehouse': (el) => selectWarehouse(el.dataset.slug),

    // MCP 连接管理
    'showAddMCPModal': showAddMCPModal,
    'closeMCPModal': closeMCPModal,
    'handleSaveMCP': handleSaveMCP,
    'mcpEdit': (el) => editMCPConnection(el.dataset.connId),
    'mcpStart': (el) => startMCPConnection(el.dataset.connId),
    'mcpStop': (el) => stopMCPConnection(el.dataset.connId),
    'mcpRestart': (el) => restartMCPConnection(el.dataset.connId),
    'mcpDelete': (el) => deleteMCPConnection(el.dataset.connId),
};

// 事件委托监听
function initEventDelegation() {
    // Click 事件
    document.addEventListener('click', function (e) {
        const actionEl = e.target.closest('[data-action]');
        if (!actionEl) return;

        const action = actionEl.dataset.action;
        const handler = actionHandlers[action];

        if (handler) {
            // Don't preventDefault for file upload trigger - it blocks the file dialog
            if (action !== 'triggerFileUpload') {
                e.preventDefault();
            }
            handler(actionEl);
        } else {
            console.warn('未定义的 action:', action);
        }
    });

    // Change 事件
    document.addEventListener('change', function (e) {
        const actionEl = e.target.closest('[data-action-change]');
        if (!actionEl) return;

        const action = actionEl.dataset.actionChange;
        const handler = actionHandlers[action];

        if (handler) {
            handler(actionEl, e);
        }
    });
}

// ============ 页面初始化 ============
document.addEventListener('DOMContentLoaded', async function () {
    // 设置模块回调
    setupModuleCallbacks();

    // 初始化 session 过期处理
    initSessionExpiredHandler();

    // 初始化事件委托
    initEventDelegation();

    // 初始化下拉组件监听
    initDropdownListeners();

    // 初始化移动端筛选抽屉（≤640px 生效，桌面端不变）
    initFilterDrawers();

    // 注入租户管理弹窗 HTML
    const tenantModalsContainer = document.createElement('div');
    tenantModalsContainer.innerHTML = getTenantModalsHTML();
    document.body.appendChild(tenantModalsContainer);

    // 注入人脸识别弹窗 HTML
    const faceModalsContainer = document.createElement('div');
    faceModalsContainer.innerHTML = getFaceModalsHTML();
    document.body.appendChild(faceModalsContainer);

    // 获取部署模式（鉴权渲染会依赖它决定多租户入口显隐）
    await fetchDeployMode();

    // 检查认证状态
    await checkAuthStatus();

    // 加载仓库列表（需在 initFromHash 之前，因为 initFromHash 会用到仓库信息）
    await loadWarehouses();

    // 初始化图表
    initCharts();

    // 加载分类和产品
    loadCategories();
    loadAllProducts();

    // 从 URL hash 初始化
    initFromHash();

    // 启动自动更新
    startAutoUpdate();
});

// 导出语言变更回调供 i18n 使用
export { onLanguageChange };
