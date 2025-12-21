// ============ 下拉组件 ============
import { t } from '../../../i18n.js';

// ============ 多选下拉组件 ============

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
    if (!dropdown) return [];
    const selectedItems = dropdown.querySelectorAll('.dropdown-item.selected');
    return Array.from(selectedItems).map(item => item.dataset.value);
}

// 更新下拉框显示文本
export function updateDropdownText(dropdownId) {
    const dropdown = document.getElementById(dropdownId);
    if (!dropdown) return;
    const textSpan = dropdown.querySelector('.dropdown-text');
    if (!textSpan) return;
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
    if (!dropdown) return;
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

// ============ 可搜索下拉选择器 ============

// 转义 HTML
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// 转义正则特殊字符
function escapeRegExp(string) {
    return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// 高亮匹配文本
function highlightMatch(text, query) {
    if (!query) return escapeHtml(text);
    const regex = new RegExp(`(${escapeRegExp(query)})`, 'gi');
    return escapeHtml(text).replace(regex, '<span class="searchable-select-highlight">$1</span>');
}

// 打开下拉
function openSearchableDropdown(wrapper) {
    wrapper.classList.add('open');
}

// 关闭下拉
export function closeSearchableDropdown(wrapper) {
    wrapper.classList.remove('open');
    wrapper._highlightIndex = -1;
}

// 更新高亮
function updateHighlight(dropdown, index) {
    dropdown.querySelectorAll('.searchable-select-option').forEach((opt, i) => {
        opt.classList.toggle('highlighted', i === index);
        if (i === index) {
            opt.scrollIntoView({ block: 'nearest' });
        }
    });
}

// 选择选项
function selectSearchableOption(wrapper, value) {
    const config = wrapper._searchableConfig;
    const input = document.getElementById(config.inputId);
    const hidden = document.getElementById(config.hiddenId);

    // 找到对应的产品
    const product = config.products.find(p => p.name === value);
    if (!product) return;

    // 设置值
    hidden.value = value;
    input.value = `${product.name} (${product.sku})`;

    // 更新外观
    wrapper.classList.add('has-value');
    closeSearchableDropdown(wrapper);

    // 调用回调
    if (config.onSelect) {
        config.onSelect(value);
    }
}

// 渲染可搜索选项
function renderSearchableOptions(wrapper, query) {
    const config = wrapper._searchableConfig;
    const dropdown = document.getElementById(config.dropdownId);
    const hidden = document.getElementById(config.hiddenId);

    if (!dropdown || !config.products) return;

    const queryLower = query.toLowerCase();
    const filtered = config.products.filter(p => {
        if (!query) return true;
        return p.name.toLowerCase().includes(queryLower) ||
            p.sku.toLowerCase().includes(queryLower);
    });

    if (filtered.length === 0) {
        dropdown.innerHTML = `<div class="searchable-select-empty">${t('noData') || '暂无数据'}</div>`;
        return;
    }

    dropdown.innerHTML = filtered.map((product, idx) => {
        const isSelected = hidden.value === product.name;
        const isDisabled = product.is_disabled;
        const classes = ['searchable-select-option'];
        if (isSelected) classes.push('selected');
        if (isDisabled) classes.push('disabled-product');

        // 高亮匹配文本
        let nameHtml = escapeHtml(product.name);
        let skuHtml = escapeHtml(product.sku);
        if (query) {
            nameHtml = highlightMatch(product.name, query);
            skuHtml = highlightMatch(product.sku, query);
        }

        let stockHtml = '';
        if (config.showStock) {
            stockHtml = `<span class="option-stock">${t('currentStockCol') || '库存'}: ${product.quantity}</span>`;
        }

        return `
            <div class="${classes.join(' ')}" data-value="${escapeHtml(product.name)}" data-index="${idx}">
                <div class="option-name">${nameHtml} ${stockHtml}</div>
                <div class="option-info"><span class="option-sku">SKU: ${skuHtml}</span></div>
            </div>
        `;
    }).join('');

    // 绑定点击事件
    dropdown.querySelectorAll('.searchable-select-option').forEach(opt => {
        opt.addEventListener('click', function () {
            const value = this.dataset.value;
            selectSearchableOption(wrapper, value);
        });
    });
}

// 初始化可搜索下拉选择器
export function initSearchableSelect(config) {
    const wrapper = document.getElementById(config.wrapperId);
    const input = document.getElementById(config.inputId);
    const dropdown = document.getElementById(config.dropdownId);
    const hidden = document.getElementById(config.hiddenId);

    if (!wrapper || !input || !dropdown || !hidden) return;

    // 设置 placeholder
    if (config.placeholder) {
        input.placeholder = config.placeholder;
    }

    // 存储配置到元素上
    wrapper._searchableConfig = config;
    wrapper._highlightIndex = -1;

    // 渲染下拉选项
    renderSearchableOptions(wrapper, '');

    // 移除旧事件监听器（如果有）
    const newInput = input.cloneNode(true);
    input.parentNode.replaceChild(newInput, input);

    // 输入事件 - 过滤选项
    newInput.addEventListener('input', function (e) {
        const query = e.target.value.trim();
        renderSearchableOptions(wrapper, query);
        openSearchableDropdown(wrapper);
        wrapper._highlightIndex = -1;
    });

    // 点击输入框 - 显示下拉
    newInput.addEventListener('focus', function () {
        renderSearchableOptions(wrapper, newInput.value.trim());
        openSearchableDropdown(wrapper);
    });

    // 键盘导航
    newInput.addEventListener('keydown', function (e) {
        const options = dropdown.querySelectorAll('.searchable-select-option');
        const maxIndex = options.length - 1;

        if (e.key === 'ArrowDown') {
            e.preventDefault();
            wrapper._highlightIndex = Math.min(wrapper._highlightIndex + 1, maxIndex);
            updateHighlight(dropdown, wrapper._highlightIndex);
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            wrapper._highlightIndex = Math.max(wrapper._highlightIndex - 1, 0);
            updateHighlight(dropdown, wrapper._highlightIndex);
        } else if (e.key === 'Enter') {
            e.preventDefault();
            if (wrapper._highlightIndex >= 0 && options[wrapper._highlightIndex]) {
                options[wrapper._highlightIndex].click();
            }
        } else if (e.key === 'Escape') {
            closeSearchableDropdown(wrapper);
            newInput.blur();
        }
    });

    // 点击外部关闭下拉
    document.addEventListener('click', function (e) {
        if (!wrapper.contains(e.target)) {
            closeSearchableDropdown(wrapper);
        }
    });
}

// 清除产品详情选择器
export function clearProductSelector(onClear = null) {
    const wrapper = document.getElementById('product-selector-wrapper');
    const input = document.getElementById('product-selector-input');
    const hidden = document.getElementById('product-selector');

    if (input) input.value = '';
    if (hidden) hidden.value = '';
    if (wrapper) wrapper.classList.remove('has-value');

    if (onClear) onClear('');
}

// 清除新增记录产品选择器
export function clearRecordProductSelector() {
    const wrapper = document.getElementById('record-product-wrapper');
    const input = document.getElementById('record-product-input');
    const hidden = document.getElementById('record-product');

    if (input) input.value = '';
    if (hidden) hidden.value = '';
    if (wrapper) wrapper.classList.remove('has-value');
}

// 设置产品选择器的值（用于外部调用）
export function setProductSelectorValue(productName) {
    const wrapper = document.getElementById('product-selector-wrapper');
    if (!wrapper || !wrapper._searchableConfig) return;

    if (productName) {
        selectSearchableOption(wrapper, productName);
    } else {
        clearProductSelector();
    }
}
