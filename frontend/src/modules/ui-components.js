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
