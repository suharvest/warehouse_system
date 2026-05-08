// ============ 联系方管理模块 ============
import { t } from '../../../i18n.js';
import { contactsApi } from '../api.js';
import {
    contactsCurrentPage, contactsPageSize, contactsTotalPages,
    setContactsCurrentPage, setContactsPageSize, setContactsTotalPages,
    getCurrentUser, API_BASE_URL
} from '../state.js';

// 全局 admin (tenant_id == null) 创建联系方时需选择目标租户
function isGlobalAdmin() {
    const u = getCurrentUser();
    return !!(u && u.role === 'admin' && (u.tenant_id === null || u.tenant_id === undefined));
}

async function _populateContactTenantSelect() {
    const group = document.getElementById('contact-tenant-group');
    const select = document.getElementById('contact-tenant');
    if (!group || !select) return;
    if (!isGlobalAdmin()) {
        group.style.display = 'none';
        return;
    }
    group.style.display = '';
    select.innerHTML = '<option value="">请选择租户</option>';
    try {
        const resp = await fetch(`${API_BASE_URL}/tenants`, { credentials: 'include' });
        if (!resp.ok) return;
        const tenants = await resp.json();
        for (const tn of tenants) {
            if (tn.is_active === false) continue;
            const opt = document.createElement('option');
            opt.value = tn.id;
            opt.textContent = tn.name;
            select.appendChild(opt);
        }
    } catch (e) {
        console.error('加载租户列表失败:', e);
    }
}

// ============ 联系方列表 ============
export async function loadContacts() {
    try {
        const name = document.getElementById('filter-contact-name')?.value || '';
        const contactType = document.getElementById('filter-contact-type')?.value || '';

        const params = {
            page: contactsCurrentPage,
            pageSize: contactsPageSize,
            search: name || undefined,
            type: contactType || undefined
        };

        const data = await contactsApi.getList(params);
        renderContactsTable(data.items);
        updateContactsPagination(data);
    } catch (error) {
        console.error('加载联系方列表失败:', error);
    }
}

function renderContactsTable(contacts) {
    const tbody = document.getElementById('contacts-tbody');
    if (!tbody) return;

    if (!contacts || contacts.length === 0) {
        tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;color:#999;">${t('noData')}</td></tr>`;
        return;
    }

    tbody.innerHTML = contacts.map(contact => {
        let typeText = '';
        if (contact.is_supplier && contact.is_customer) {
            typeText = t('bothType');
        } else if (contact.is_supplier) {
            typeText = t('supplier');
        } else if (contact.is_customer) {
            typeText = t('customer');
        }

        return `
            <tr>
                <td>${contact.name}</td>
                <td>${typeText}</td>
                <td>${contact.phone || '-'}</td>
                <td>${contact.email || '-'}</td>
                <td>${contact.address || '-'}</td>
                <td>${contact.is_disabled ? `<span style="color:#ff4d4f;">${t('disabled')}</span>` : `<span style="color:#52c41a;">${t('enabled')}</span>`}</td>
                <td>
                    <button class="action-btn-small" data-action="editContact" data-contact-id="${contact.id}">${t('edit')}</button>
                    ${contact.is_disabled
                        ? `<button class="action-btn-small success" data-action="toggleContactStatus" data-contact-id="${contact.id}" data-is-disabled="true">${t('enable')}</button>`
                        : `<button class="action-btn-small danger" data-action="toggleContactStatus" data-contact-id="${contact.id}" data-is-disabled="false">${t('disable')}</button>`
                    }
                </td>
            </tr>
        `;
    }).join('');
}

// ============ 分页 ============
function updateContactsPagination(data) {
    setContactsTotalPages(data.total_pages);

    document.getElementById('contacts-total').textContent = data.total;
    document.getElementById('contacts-current-page').textContent = contactsCurrentPage;
    document.getElementById('contacts-total-pages').textContent = contactsTotalPages;

    document.getElementById('contacts-prev-btn').disabled = contactsCurrentPage <= 1;
    document.getElementById('contacts-next-btn').disabled = contactsCurrentPage >= contactsTotalPages;
}

export function contactsGoToPage(page) {
    if (page < 1 || page > contactsTotalPages) return;
    setContactsCurrentPage(page);
    loadContacts();
}

export function changeContactsPageSize(size) {
    setContactsPageSize(parseInt(size));
    setContactsCurrentPage(1);
    loadContacts();
}

// ============ 筛选 ============
export function applyContactsFilter() {
    setContactsCurrentPage(1);
    loadContacts();
}

export function resetContactsFilter() {
    document.getElementById('filter-contact-name').value = '';
    document.getElementById('filter-contact-type').value = '';
    setContactsCurrentPage(1);
    loadContacts();
}

// ============ 添加/编辑联系方 ============
export async function showAddContactModal() {
    document.getElementById('contact-modal-title').textContent = t('addContact');
    document.getElementById('contact-id').value = '';
    document.getElementById('contact-form').reset();
    document.getElementById('contact-error').style.display = 'none';
    await _populateContactTenantSelect();
    document.getElementById('contact-modal').classList.add('show');
    document.getElementById('contact-name').focus();
}

export function closeContactModal() {
    document.getElementById('contact-modal').classList.remove('show');
    document.getElementById('contact-form').reset();
    document.getElementById('contact-error').style.display = 'none';
}

export async function editContact(contactId) {
    try {
        const contact = await contactsApi.getById(contactId);

        document.getElementById('contact-modal-title').textContent = t('editContact');
        document.getElementById('contact-id').value = contact.id;
        document.getElementById('contact-name').value = contact.name;
        document.getElementById('contact-is-supplier').checked = contact.is_supplier;
        document.getElementById('contact-is-customer').checked = contact.is_customer;
        document.getElementById('contact-phone').value = contact.phone || '';
        document.getElementById('contact-email').value = contact.email || '';
        document.getElementById('contact-address').value = contact.address || '';
        document.getElementById('contact-notes').value = contact.notes || '';
        document.getElementById('contact-error').style.display = 'none';
        document.getElementById('contact-modal').classList.add('show');
    } catch (error) {
        if (error.status === 401) return;
        console.error('获取联系方详情失败:', error);
        alert(t('operationFailed'));
    }
}

export async function handleSaveContact() {
    const contactId = document.getElementById('contact-id').value;
    const name = document.getElementById('contact-name').value.trim();
    const isSupplier = document.getElementById('contact-is-supplier').checked;
    const isCustomer = document.getElementById('contact-is-customer').checked;
    const phone = document.getElementById('contact-phone').value.trim();
    const email = document.getElementById('contact-email').value.trim();
    const address = document.getElementById('contact-address').value.trim();
    const notes = document.getElementById('contact-notes').value.trim();
    const errorDiv = document.getElementById('contact-error');

    if (!name) {
        errorDiv.textContent = t('fillAllFields');
        errorDiv.style.display = 'block';
        return;
    }

    if (!isSupplier && !isCustomer) {
        errorDiv.textContent = t('contactMustSelectType');
        errorDiv.style.display = 'block';
        return;
    }

    const data = {
        name,
        is_supplier: isSupplier,
        is_customer: isCustomer,
        phone: phone || null,
        email: email || null,
        address: address || null,
        notes: notes || null
    };

    // 全局 admin 创建时必须带 tenant_id
    if (!contactId && isGlobalAdmin()) {
        const tenantSel = document.getElementById('contact-tenant');
        const tenantId = tenantSel ? tenantSel.value : '';
        if (!tenantId) {
            errorDiv.textContent = '请选择租户';
            errorDiv.style.display = 'block';
            return;
        }
        data.tenant_id = parseInt(tenantId, 10);
    }

    try {
        if (contactId) {
            await contactsApi.update(contactId, data);
        } else {
            await contactsApi.create(data);
        }
        closeContactModal();
        loadContacts();
    } catch (error) {
        console.error('保存联系方失败:', error);
        errorDiv.textContent = error.message || t('operationFailed');
        errorDiv.style.display = 'block';
    }
}

// ============ 切换联系方状态 ============
export async function toggleContactStatus(contactId, isDisabled) {
    try {
        await contactsApi.toggleStatus(contactId, !isDisabled);
        loadContacts();
    } catch (error) {
        if (error.status === 401) return;
        console.error('更新联系方状态失败:', error);
        alert(error.message || t('operationFailed'));
    }
}
