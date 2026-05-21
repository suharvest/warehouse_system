// ============ 全局状态管理 ============

export const API_BASE_URL = '/api';

// 当前 Tab
export let currentTab = 'dashboard';
export const getCurrentTab = () => currentTab;
export function setCurrentTab(tab) { currentTab = tab; }

// 自动刷新
export let countdownInterval = null;
export let countdownSeconds = 20;
export const getCountdownSeconds = () => countdownSeconds;
export function setCountdownInterval(val) { countdownInterval = val; }
export function setCountdownSeconds(val) { countdownSeconds = val; }

// 图表实例
export let trendChart = null;
export let categoryChart = null;
export let topStockChart = null;
export let detailTrendChart = null;
export let detailPieChart = null;
export function setTrendChart(chart) { trendChart = chart; }
export function setCategoryChart(chart) { categoryChart = chart; }
export function setTopStockChart(chart) { topStockChart = chart; }
export function setDetailTrendChart(chart) { detailTrendChart = chart; }
export function setDetailPieChart(chart) { detailPieChart = chart; }

// 分类数据
export let allCategories = [];
export const getAllCategories = () => allCategories;
export function setAllCategories(categories) { allCategories = categories; }

// 用户认证状态
export let currentUser = null;
export let isSystemInitialized = false;
export const getCurrentUser = () => currentUser;
export const getIsSystemInitialized = () => isSystemInitialized;
export function setCurrentUser(user) { currentUser = user; }
export function setIsSystemInitialized(val) { isSystemInitialized = val; }

// 库存列表分页状态
export let inventoryCurrentPage = 1;
export let inventoryPageSize = 20;
export let inventoryTotalPages = 1;
export const getInventoryCurrentPage = () => inventoryCurrentPage;
export function setInventoryCurrentPage(page) { inventoryCurrentPage = page; }
export function setInventoryPageSize(size) { inventoryPageSize = size; }
export function setInventoryTotalPages(total) { inventoryTotalPages = total; }

// 进出库记录分页状态
export let recordsCurrentPage = 1;
export let recordsPageSize = 20;
export let recordsTotalPages = 1;
export const getRecordsCurrentPage = () => recordsCurrentPage;
export function setRecordsCurrentPage(page) { recordsCurrentPage = page; }
export function setRecordsPageSize(size) { recordsPageSize = size; }
export function setRecordsTotalPages(total) { recordsTotalPages = total; }

// 产品详情状态
export let currentProductName = '';
export let detailCurrentPage = 1;
export let detailPageSize = 20;
export let detailTotalPages = 1;
export let lastProductStats = null;
export const getCurrentProductName = () => currentProductName;
export const getDetailCurrentPage = () => detailCurrentPage;
export function setCurrentProductName(name) { currentProductName = name; }
export function setDetailCurrentPage(page) { detailCurrentPage = page; }
export function setDetailPageSize(size) { detailPageSize = size; }
export function setDetailTotalPages(total) { detailTotalPages = total; }
export function setLastProductStats(stats) { lastProductStats = stats; }

// 所有产品列表（用于产品选择器和新增记录）
export let allProducts = [];
export const getAllProducts = () => allProducts;
export function setAllProducts(products) { allProducts = products; }

// 可搜索下拉组件状态
export let productSelectorHighlightIndex = -1;
export let recordProductHighlightIndex = -1;
export function setProductSelectorHighlightIndex(idx) { productSelectorHighlightIndex = idx; }
export function setRecordProductHighlightIndex(idx) { recordProductHighlightIndex = idx; }

// 仓库状态
const WAREHOUSE_SLUG_KEY = 'current_warehouse_slug';
export let currentWarehouse = null;  // { id, slug, name, is_default } or null (全局视图)
export let allWarehouses = [];
export const getCurrentWarehouse = () => currentWarehouse;
export const getAllWarehouses = () => allWarehouses;

// 切仓 epoch：每次切仓递增。loaders 在 fetch 前 capture，commit state 前
// 比对——过时的响应（用户已切到别的仓）直接丢弃，避免 A 慢响应覆盖 B
// 的数据（防"快速 A→B 切仓"race，参考 codex audit agentId a6b9aa8cf2d4799ed）。
let warehouseEpoch = 0;
export const getWarehouseEpoch = () => warehouseEpoch;
export function bumpWarehouseEpoch() {
    warehouseEpoch += 1;
}
export function setCurrentWarehouse(wh) {
    currentWarehouse = wh;
    // 持久化 slug：刷新或后续会话能恢复上下文，避免 URL 是 / 时丢仓库
    try {
        if (wh && wh.slug) {
            localStorage.setItem(WAREHOUSE_SLUG_KEY, wh.slug);
        } else {
            localStorage.removeItem(WAREHOUSE_SLUG_KEY);
        }
    } catch (_) { /* 隐私模式下 localStorage 不可用，忽略 */ }
}
export function getStoredWarehouseSlug() {
    try { return localStorage.getItem(WAREHOUSE_SLUG_KEY); }
    catch (_) { return null; }
}
export function setAllWarehouses(list) { allWarehouses = list; }

// 联系方分页状态
export let contactsCurrentPage = 1;
export let contactsPageSize = 20;
export let contactsTotalPages = 1;
export const getContactsCurrentPage = () => contactsCurrentPage;
export function setContactsCurrentPage(page) { contactsCurrentPage = page; }
export function setContactsPageSize(size) { contactsPageSize = size; }
export function setContactsTotalPages(total) { contactsTotalPages = total; }

// 租户状态
export let currentTenant = null;
// deployMode 是部署元信息（single_tenant / multi_tenant），由 fetchDeployMode() 从 /api/system/mode
// 拉取并通过 setDeployMode 写入。模块加载时先用 localStorage 缓存（上次会话的值）作为占位，
// 避免在 fetchDeployMode 完成前 UI 用错误的默认值闪一下；fetch 完成后会覆盖为权威值。
export let deployMode = localStorage.getItem('deploy_mode') || 'single_tenant';
export const getDeployMode = () => deployMode;
export function setCurrentTenant(tenant) { currentTenant = tenant; }
export function setDeployMode(mode) { deployMode = mode; localStorage.setItem('deploy_mode', mode); }

