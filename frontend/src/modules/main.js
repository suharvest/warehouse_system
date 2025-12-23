// ============ 主入口模块 ============
import { t } from '../../i18n.js';
import { inventoryApi } from './api.js';
import {
    allCategories, setAllCategories, allProducts, setAllProducts,
    currentTab, recordsCurrentPage, inventoryCurrentPage, detailCurrentPage, contactsCurrentPage
} from './state.js';

// UI 模块
import { initDropdownListeners, initSearchableSelect, setProductSelectorValue, clearProductSelector, clearRecordProductSelector, toggleDropdown, toggleDropdownItem } from './ui/dropdown.js';
import { switchTab, initFromHash, startAutoUpdate, refreshCurrentTab, goBackToInventory, setTabModules } from './ui/tabs.js';

// 功能模块
import { checkAuthStatus, showLoginModal, closeLoginModal, handleLogin, handleLogout, showSetupModal, handleSetup, updateUserDisplay, updatePermissionUI, setAuthCallbacks } from './features/auth.js';
import { initCharts, loadDashboardData, onTotalStockClick, onTodayInClick, onTodayOutClick, onLowStockClick, setDashboardCallbacks } from './features/dashboard.js';
import { loadInventory, inventoryGoToPage, changeInventoryPageSize, applyInventoryFilter, resetInventoryFilter, applyInventoryFilters, setInventoryCallbacks } from './features/inventory.js';
import { loadRecords, recordsGoToPage, changeRecordsPageSize, applyRecordsFilter, resetRecordsFilter, applyRecordsFilters, loadRecordsFilterOptions, showAddRecordModal, showAddRecordModalForProduct, closeAddRecordModal, submitAddRecord, setRecordsCallbacks } from './features/records.js';
import { onProductSelect, initDetailCharts, loadProductDetail, loadProductTrend, loadDetailPieChart, loadProductRecords, detailGoToPage, changeDetailPageSize, refreshProductDetailForLanguage } from './features/product-detail.js';
import { exportInventory, exportRecords, exportProductRecords, showImportModal, closeImportModal, handleFileSelect, confirmImport, closeNewSkuModal, skipNewSkus, confirmNewSkus, setImportExportCallbacks } from './features/import-export.js';
import { loadUsers, showAddUserModal, closeAddUserModal, handleAddUser, showEditUserModal, closeEditUserModal, handleEditUser, toggleUserStatus, setUsersCallbacks } from './features/users.js';
import { loadApiKeys, showAddApiKeyModal, closeAddApiKeyModal, handleAddApiKey, closeShowApiKeyModal, copyApiKey, disableApiKey, toggleApiKeyStatus, deleteApiKey } from './features/api-keys.js';
import { loadContacts, contactsGoToPage, changeContactsPageSize, applyContactsFilter, resetContactsFilter, showAddContactModal, closeContactModal, editContact, handleSaveContact, toggleContactStatus } from './features/contacts.js';
import { exportDatabase, showImportDatabaseModal, closeImportDatabaseModal, handleDatabaseFileSelect, confirmImportDatabase, showClearDatabaseModal, closeClearDatabaseModal, exportThenClearDatabase, directClearDatabase } from './features/database.js';

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
        loadApiKeys,
        loadProductDetail,
        setProductSelectorValue
    });

    // 设置认证回调
    setAuthCallbacks({
        onAuthChange: () => {},
        switchTab,
        refreshCurrentTab
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
    if (!allProducts || !Array.isArray(allProducts)) {
        setAllProducts([]);
    }

    // 产品详情页选择器
    initSearchableSelect({
        wrapperId: 'product-selector-wrapper',
        inputId: 'product-selector-input',
        dropdownId: 'product-selector-dropdown',
        hiddenId: 'product-selector',
        products: allProducts,
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
        products: allProducts.filter(p => !p.is_disabled),
        includeDisabled: false,
        showStock: true,
        onSelect: null,
        placeholder: t('searchPlaceholder') || '搜索产品名称或编码...'
    });
}

// ============ 语言变更回调 ============
function onLanguageChange() {
    document.title = t('pageTitle');
    populateProductSelector();
    populateCategorySelect();

    // 重新渲染当前Tab
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
            refreshProductDetailForLanguage();
            break;
        case 'users':
            loadUsers();
            loadApiKeys();
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

    // 初始化事件委托
    initEventDelegation();

    // 初始化下拉组件监听
    initDropdownListeners();

    // 检查认证状态
    await checkAuthStatus();

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
