// ============ 全局状态管理 ============

export const API_BASE_URL = '/api';

// 当前 Tab
export let currentTab = 'dashboard';
export function setCurrentTab(tab) { currentTab = tab; }

// 自动刷新
export let countdownInterval = null;
export let countdownSeconds = 20;
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
export function setAllCategories(categories) { allCategories = categories; }

// 用户认证状态
export let currentUser = null;
export let isSystemInitialized = false;
export function setCurrentUser(user) { currentUser = user; }
export function setIsSystemInitialized(val) { isSystemInitialized = val; }

// 库存列表分页状态
export let inventoryCurrentPage = 1;
export let inventoryPageSize = 20;
export let inventoryTotalPages = 1;
export function setInventoryCurrentPage(page) { inventoryCurrentPage = page; }
export function setInventoryPageSize(size) { inventoryPageSize = size; }
export function setInventoryTotalPages(total) { inventoryTotalPages = total; }

// 进出库记录分页状态
export let recordsCurrentPage = 1;
export let recordsPageSize = 20;
export let recordsTotalPages = 1;
export function setRecordsCurrentPage(page) { recordsCurrentPage = page; }
export function setRecordsPageSize(size) { recordsPageSize = size; }
export function setRecordsTotalPages(total) { recordsTotalPages = total; }

// 产品详情状态
export let currentProductName = '';
export let detailCurrentPage = 1;
export let detailPageSize = 20;
export let detailTotalPages = 1;
export let lastProductStats = null;
export function setCurrentProductName(name) { currentProductName = name; }
export function setDetailCurrentPage(page) { detailCurrentPage = page; }
export function setDetailPageSize(size) { detailPageSize = size; }
export function setDetailTotalPages(total) { detailTotalPages = total; }
export function setLastProductStats(stats) { lastProductStats = stats; }

// 所有产品列表（用于产品选择器和新增记录）
export let allProducts = [];
export function setAllProducts(products) { allProducts = products; }

// 可搜索下拉组件状态
export let productSelectorHighlightIndex = -1;
export let recordProductHighlightIndex = -1;
export function setProductSelectorHighlightIndex(idx) { productSelectorHighlightIndex = idx; }
export function setRecordProductHighlightIndex(idx) { recordProductHighlightIndex = idx; }

// 联系方分页状态
export let contactsCurrentPage = 1;
export let contactsPageSize = 20;
export let contactsTotalPages = 1;
export function setContactsCurrentPage(page) { contactsCurrentPage = page; }
export function setContactsPageSize(size) { contactsPageSize = size; }
export function setContactsTotalPages(total) { contactsTotalPages = total; }
