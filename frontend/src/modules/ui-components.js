// ============ UI 组件（下拉多选等） ============
import { t } from '../../i18n.js';

// 切换下拉框开关
export function toggleDropdown(dropdownId) {
    const dropdown = document.getElementById(dropdownId);
    const isOpen = dropdown.classList.contains('open');

    // 关闭所有下拉框
    document.querySelectorAll('.dropdown-multiselect.open').forEach(d => {
        d.classList.remove('open');
    });

    // 切换当前下拉框
    if (!isOpen) {
        dropdown.classList.add('open');
    }
}

// 切换下拉项选中状态
export function toggleDropdownItem(item) {
    item.classList.toggle('selected');
    const dropdown = item.closest('.dropdown-multiselect');
    updateDropdownText(dropdown.id);
}

// 获取下拉框选中的值
export function getDropdownSelectedValues(dropdownId) {
    const dropdown = document.getElementById(dropdownId);
    const selectedItems = dropdown.querySelectorAll('.dropdown-item.selected');
    return Array.from(selectedItems).map(item => item.dataset.value);
}

// 更新下拉框显示文本
export function updateDropdownText(dropdownId) {
    const dropdown = document.getElementById(dropdownId);
    const textSpan = dropdown.querySelector('.dropdown-text');
    const selected = getDropdownSelectedValues(dropdownId);

    if (selected.length === 0 || selected.length === 4) {
        textSpan.textContent = t('allStatuses');
    } else {
        const labels = [];
        selected.forEach(val => {
            if (val === 'normal') labels.push(t('statusNormal'));
            else if (val === 'warning') labels.push(t('statusWarning'));
            else if (val === 'danger') labels.push(t('statusDanger'));
            else if (val === 'disabled') labels.push(t('statusDisabled'));
        });
        textSpan.textContent = labels.join(', ');
    }
}

// 重置下拉框选择
export function resetDropdownSelection(dropdownId, defaultValues = ['normal', 'warning', 'danger']) {
    const dropdown = document.getElementById(dropdownId);
    dropdown.querySelectorAll('.dropdown-item').forEach(item => {
        if (defaultValues.includes(item.dataset.value)) {
            item.classList.add('selected');
        } else {
            item.classList.remove('selected');
        }
    });
    updateDropdownText(dropdownId);
}

// 初始化下拉框全局点击监听
export function initDropdownListeners() {
    document.addEventListener('click', function (e) {
        if (!e.target.closest('.dropdown-multiselect')) {
            document.querySelectorAll('.dropdown-multiselect.open').forEach(d => {
                d.classList.remove('open');
            });
        }
    });
}

// ============ Toast 通知 ============

/**
 * 显示 Toast 通知
 * @param {string} message 消息内容
 * @param {string} type 类型: success, error, info
 * @param {number} duration 持续时间(ms)
 */
export function showToast(message, type = 'success', duration = 3000) {
    let container = document.querySelector('.toast-container');
    if (!container) {
        container = document.createElement('div');
        container.className = 'toast-container';
        document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    
    let icon = '';
    if (type === 'success') {
        icon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="toast-icon"><polyline points="20 6 9 17 4 12"></polyline></svg>';
    } else if (type === 'error') {
        icon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="toast-icon"><circle cx="12" cy="12" r="10"></circle><line x1="15" y1="9" x2="9" y2="15"></line><line x1="9" y1="9" x2="15" y2="15"></line></svg>';
    } else {
        icon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="toast-icon"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>';
    }

    toast.innerHTML = `
        <div class="toast-icon">${icon}</div>
        <div class="toast-content">${message}</div>
    `;

    container.appendChild(toast);

    setTimeout(() => {
        toast.classList.add('hide');
        setTimeout(() => toast.remove(), 300);
    }, duration);
}

/**
 * 在模态框中显示成功状态
 * @param {HTMLElement} modal 模态框元素
 * @param {Object} options 选项 { title, message, buttons: [{ text, action, primary }] }
 */
export function showModalSuccessState(modal, options) {
    const body = modal.querySelector('.modal-body');
    const footer = modal.querySelector('.modal-footer');
    if (!body || !footer) return;

    body.innerHTML = `
        <div class="flex flex-col items-center py-6 text-center">
            <div class="w-16 h-16 bg-success/10 text-success rounded-full flex items-center justify-center mb-4">
                <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                    <polyline points="20 6 9 17 4 12"></polyline>
                </svg>
            </div>
            <h4 class="text-lg font-bold text-text-primary mb-2">${options.title || t('success')}</h4>
            <p class="text-sm text-text-secondary">${options.message || ''}</p>
        </div>
    `;

    footer.innerHTML = options.buttons.map(btn => `
        <button class="btn ${btn.primary ? 'confirm-btn' : 'cancel-btn'}" data-action="${btn.action}">
            ${btn.text}
        </button>
    `).join('');
}
