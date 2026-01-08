// ============ API 请求封装 ============

export const API_BASE_URL = '/api';

// 全局 session 过期回调
let onSessionExpired = null;

export function setSessionExpiredHandler(handler) {
  onSessionExpired = handler;
}

// 通用请求封装
async function request(url, options = {}) {
  const defaultOptions = {
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...options.headers
    }
  };

  const response = await fetch(`${API_BASE_URL}${url}`, {
    ...defaultOptions,
    ...options
  });

  return response;
}

async function fetchJson(url, options = {}) {
  const response = await request(url, options);
  if (!response.ok) {
    // 检测 session 过期（排除 auth 相关的 API）
    if (response.status === 401 && !url.startsWith('/auth/') && onSessionExpired) {
      onSessionExpired();
    }
    const error = new Error(`HTTP ${response.status}`);
    error.status = response.status;
    try {
      error.data = await response.json();
    } catch {}
    throw error;
  }
  return response.json();
}

// ============ 认证 API ============
export const authApi = {
  // 检查认证状态
  async getStatus() {
    return fetchJson('/auth/status');
  },

  // 登录
  async login(username, password) {
    return fetchJson('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password })
    });
  },

  // 登出
  async logout() {
    return request('/auth/logout', { method: 'POST' });
  },

  // 首次设置
  async setup(username, password, displayName) {
    return fetchJson('/auth/setup', {
      method: 'POST',
      body: JSON.stringify({
        username,
        password,
        display_name: displayName || null
      })
    });
  }
};

// ============ Dashboard API ============
export const dashboardApi = {
  // 获取统计数据
  async getStats() {
    return fetchJson('/dashboard/stats');
  },

  // 获取周趋势
  async getWeeklyTrend() {
    return fetchJson('/dashboard/weekly-trend');
  },

  // 获取分类分布
  async getCategoryDistribution() {
    return fetchJson('/dashboard/category-distribution');
  },

  // 获取库存排名
  async getTopStock(limit = 10) {
    return fetchJson(`/dashboard/top-stock?limit=${limit}`);
  }
};

// ============ 库存 API ============
export const inventoryApi = {
  // 获取库存列表
  async getList(params = {}) {
    const query = new URLSearchParams();
    if (params.page) query.set('page', params.page);
    if (params.pageSize) query.set('page_size', params.pageSize);
    if (params.name) query.set('name', params.name);
    if (params.category) query.set('category', params.category);
    if (params.status && params.status.length > 0) {
      query.set('status', params.status.join(','));
    }
    return fetchJson(`/materials/list?${query.toString()}`);
  },

  // 获取分类列表
  async getCategories() {
    return fetchJson('/materials/categories');
  },

  // 获取所有产品
  async getAllProducts() {
    return fetchJson('/materials/products');
  },

  // 导入库存
  async import(data, createNewSku = false) {
    return fetchJson(`/materials/import?create_new_sku=${createNewSku}`, {
      method: 'POST',
      body: JSON.stringify(data)
    });
  },

  // 导出库存
  async export(params = {}) {
    const query = new URLSearchParams();
    if (params.search) query.set('search', params.search);
    if (params.category) query.set('category', params.category);
    if (params.status && params.status.length > 0) {
      params.status.forEach(s => query.append('status', s));
    }
    const response = await request(`/materials/export?${query.toString()}`);
    return response.blob();
  }
};

// ============ 记录 API ============
export const recordsApi = {
  // 获取记录列表
  async getList(params = {}) {
    const query = new URLSearchParams();
    if (params.page) query.set('page', params.page);
    if (params.pageSize) query.set('page_size', params.pageSize);
    if (params.productName) query.set('product_name', params.productName);
    if (params.category) query.set('category', params.category);
    if (params.recordType) query.set('record_type', params.recordType);
    if (params.startDate) query.set('start_date', params.startDate);
    if (params.endDate) query.set('end_date', params.endDate);
    if (params.operatorUserId) query.set('operator_user_id', params.operatorUserId);
    if (params.contactId) query.set('contact_id', params.contactId);
    if (params.reason) query.set('reason', params.reason);
    if (params.status && params.status.length > 0) {
      query.set('status', params.status.join(','));
    }
    return fetchJson(`/inventory/records?${query.toString()}`);
  },

  // 新增记录
  async create(data) {
    return fetchJson('/inventory/add-record', {
      method: 'POST',
      body: JSON.stringify(data)
    });
  },

  // 导出记录
  async export(params = {}) {
    const query = new URLSearchParams();
    if (params.productName) query.set('product_name', params.productName);
    if (params.category) query.set('category', params.category);
    if (params.recordType) query.set('record_type', params.recordType);
    if (params.startDate) query.set('start_date', params.startDate);
    if (params.endDate) query.set('end_date', params.endDate);
    const response = await request(`/inventory/export-excel?${query.toString()}`);
    return response.blob();
  }
};

// ============ 产品详情 API ============
export const productApi = {
  // 获取产品统计
  async getStats(productName) {
    return fetchJson(`/materials/product-stats?name=${encodeURIComponent(productName)}`);
  },

  // 获取产品趋势
  async getTrend(productName, days = 30) {
    return fetchJson(`/materials/product-trend?name=${encodeURIComponent(productName)}&days=${days}`);
  },

  // 获取产品记录
  async getRecords(productName, params = {}) {
    const query = new URLSearchParams();
    query.set('name', productName);
    if (params.page) query.set('page', params.page);
    if (params.pageSize) query.set('page_size', params.pageSize);
    return fetchJson(`/materials/product-records?${query.toString()}`);
  },

  // 导出产品记录
  async exportRecords(productName) {
    const response = await request(`/materials/product-records/export?name=${encodeURIComponent(productName)}`);
    return response.blob();
  }
};

// ============ 用户管理 API ============
export const usersApi = {
  // 获取用户列表
  async getList() {
    return fetchJson('/users');
  },

  // 添加用户
  async create(data) {
    return fetchJson('/users', {
      method: 'POST',
      body: JSON.stringify(data)
    });
  },

  // 更新用户
  async update(userId, data) {
    return fetchJson(`/users/${userId}`, {
      method: 'PUT',
      body: JSON.stringify(data)
    });
  },

  // 切换用户状态
  async toggleStatus(userId, disabled) {
    return fetchJson(`/users/${userId}`, {
      method: 'PUT',
      body: JSON.stringify({ is_disabled: disabled })
    });
  }
};

// ============ API 密钥管理 ============
export const apiKeysApi = {
  // 获取密钥列表
  async getList() {
    return fetchJson('/api-keys');
  },

  // 创建密钥
  async create(data) {
    return fetchJson('/api-keys', {
      method: 'POST',
      body: JSON.stringify(data)
    });
  },

  // 切换密钥状态
  async toggleStatus(keyId, disabled) {
    return fetchJson(`/api-keys/${keyId}/status`, {
      method: 'PUT',
      body: JSON.stringify({ disabled })
    });
  },

  // 删除密钥
  async delete(keyId) {
    return fetchJson(`/api-keys/${keyId}`, {
      method: 'DELETE'
    });
  }
};

// ============ 联系方管理 API ============
export const contactsApi = {
  // 获取联系方列表
  async getList(params = {}) {
    const query = new URLSearchParams();
    if (params.page) query.set('page', params.page);
    if (params.pageSize) query.set('page_size', params.pageSize);
    if (params.search) query.set('name', params.search);
    if (params.type) query.set('contact_type', params.type);
    if (params.includeDisabled) query.set('include_disabled', 'true');
    return fetchJson(`/contacts?${query.toString()}`);
  },

  // 获取所有联系方（不分页）
  async getAll(type = null, activeOnly = true) {
    const query = new URLSearchParams();
    query.set('page', '1');
    query.set('page_size', '100');
    if (type) query.set('contact_type', type);
    if (!activeOnly) query.set('include_disabled', 'true');
    return fetchJson(`/contacts?${query.toString()}`);
  },

  // 获取单个联系方
  async getById(contactId) {
    return fetchJson(`/contacts/${contactId}`);
  },

  // 创建联系方
  async create(data) {
    return fetchJson('/contacts', {
      method: 'POST',
      body: JSON.stringify(data)
    });
  },

  // 更新联系方
  async update(contactId, data) {
    return fetchJson(`/contacts/${contactId}`, {
      method: 'PUT',
      body: JSON.stringify(data)
    });
  },

  // 切换联系方状态
  async toggleStatus(contactId, disabled) {
    return fetchJson(`/contacts/${contactId}/status`, {
      method: 'PUT',
      body: JSON.stringify({ disabled })
    });
  }
};

// ============ 操作员 API ============
export const operatorsApi = {
  // 获取操作员列表
  async getList() {
    return fetchJson('/operators');
  }
};
