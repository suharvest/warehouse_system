// ============ Mobile Filter Drawer ============
// Enhances existing `.filter-bar` blocks: on small screens (≤640px), the
// inline filter bar is hidden and replaced by a "🔍 筛选 (N)" trigger button
// that opens the filter bar as a bottom-sheet drawer with a 2-column grid.
//
// Desktop behaviour is completely untouched — the trigger bar is hidden via
// CSS at >640px and `.filter-bar` keeps its original inline layout.
//
// Strategy: pure progressive enhancement, no per-page HTML changes required.
// We scan for every `.filter-bar`, inject a sibling `<div class="filter-trigger-bar">`,
// and toggle a body-level class to show/hide the drawer.

import { t } from '../../../i18n.js';

const ENHANCED = new WeakSet();
let activeDrawer = null;

// A filter is considered "applied" if its value is truthy AND not a default.
function countAppliedFilters(filterBar) {
    let n = 0;
    // Native inputs
    filterBar.querySelectorAll('input').forEach((el) => {
        if (el.type === 'hidden') return;
        if (el.type === 'checkbox' || el.type === 'radio') {
            if (el.checked) n++;
            return;
        }
        if ((el.value || '').trim() !== '') n++;
    });
    // Native selects (treat empty value "" as default — that matches all the
    // "全部 xxx" placeholder options used in this app)
    filterBar.querySelectorAll('select').forEach((el) => {
        if ((el.value || '').trim() !== '') n++;
    });
    // Multi-select dropdowns (custom). Count as 1 if any item is *unselected*
    // away from its initial state OR if the trigger text differs from the
    // default. Simplest heuristic: count as applied if trigger text is
    // non-empty AND not the localized "全部状态/全部类型" default. We just
    // check whether the dropdown's selected state is non-default by counting
    // selected items vs total items — if not all (or no default config),
    // treat as filtered.
    filterBar.querySelectorAll('.dropdown-multiselect').forEach((dd) => {
        const items = dd.querySelectorAll('.dropdown-item');
        if (!items.length) return;
        const selected = dd.querySelectorAll('.dropdown-item.selected');
        // Default state = all selected (means "show everything"). Any
        // deviation counts as one applied filter.
        if (selected.length !== items.length) n++;
    });
    return n;
}

function updateBadge(triggerBtn, filterBar) {
    const n = countAppliedFilters(filterBar);
    const badge = triggerBtn.querySelector('.filter-trigger-badge');
    if (!badge) return;
    badge.textContent = String(n);
    triggerBtn.classList.toggle('has-filters', n > 0);
}

function openDrawer(filterBar, triggerBtn) {
    if (activeDrawer && activeDrawer !== filterBar) {
        closeDrawer(activeDrawer);
    }
    activeDrawer = filterBar;
    filterBar.classList.add('is-drawer-open');
    document.body.classList.add('filter-drawer-open');
    // Ensure backdrop exists
    let bd = document.getElementById('filter-drawer-backdrop');
    if (!bd) {
        bd = document.createElement('div');
        bd.id = 'filter-drawer-backdrop';
        bd.className = 'filter-drawer-backdrop';
        bd.addEventListener('click', () => closeDrawer(filterBar));
        document.body.appendChild(bd);
    }
    bd.classList.add('show');
    updateBadge(triggerBtn, filterBar);
}

function closeDrawer(filterBar) {
    if (!filterBar) return;
    filterBar.classList.remove('is-drawer-open');
    document.body.classList.remove('filter-drawer-open');
    const bd = document.getElementById('filter-drawer-backdrop');
    if (bd) bd.classList.remove('show');
    if (activeDrawer === filterBar) activeDrawer = null;
}

function buildTriggerBar(filterBar) {
    const wrap = document.createElement('div');
    wrap.className = 'filter-trigger-bar';

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'filter-trigger-btn';
    btn.innerHTML = `
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"></polygon>
        </svg>
        <span class="filter-trigger-label" data-i18n="filterMobileBtn">${t ? t('filterMobileBtn') || '筛选' : '筛选'}</span>
        <span class="filter-trigger-badge">0</span>
    `;
    btn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        openDrawer(filterBar, btn);
    });
    wrap.appendChild(btn);
    return { wrap, btn };
}

function injectDrawerHeader(filterBar, triggerBtn) {
    if (filterBar.querySelector('.filter-drawer-header')) return;
    const header = document.createElement('div');
    header.className = 'filter-drawer-header';
    header.innerHTML = `
        <span class="filter-drawer-title" data-i18n="filterMobileBtn">${t ? t('filterMobileBtn') || '筛选' : '筛选'}</span>
        <button type="button" class="filter-drawer-close" aria-label="Close">&times;</button>
    `;
    header.querySelector('.filter-drawer-close').addEventListener('click', () => closeDrawer(filterBar));
    filterBar.insertBefore(header, filterBar.firstChild);
}

function enhanceFilterBar(filterBar) {
    if (ENHANCED.has(filterBar)) return;
    ENHANCED.add(filterBar);

    const { wrap, btn } = buildTriggerBar(filterBar);
    filterBar.parentNode.insertBefore(wrap, filterBar);
    injectDrawerHeader(filterBar, btn);

    // Listen for value changes to update badge
    const refresh = () => updateBadge(btn, filterBar);
    filterBar.addEventListener('input', refresh);
    filterBar.addEventListener('change', refresh);
    // Multi-select dropdowns mutate via JS (toggle .selected); use mutation
    // observer to catch those.
    const mo = new MutationObserver(refresh);
    filterBar.querySelectorAll('.dropdown-multiselect').forEach((dd) => {
        mo.observe(dd, { attributes: true, attributeFilter: ['class'], subtree: true });
    });

    // Auto-close drawer on apply/reset (their existing handlers will run via
    // the global delegator first; we just close after).
    filterBar.querySelectorAll('.filter-actions [data-action]').forEach((b) => {
        b.addEventListener('click', () => {
            // Close after the current event tick so the action handler runs
            setTimeout(() => closeDrawer(filterBar), 0);
            setTimeout(() => updateBadge(btn, filterBar), 50);
        });
    });

    refresh();
}

export function initFilterDrawers(root = document) {
    root.querySelectorAll('.filter-bar').forEach(enhanceFilterBar);

    // Close on Escape
    if (!window.__filterDrawerEscBound) {
        window.__filterDrawerEscBound = true;
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && activeDrawer) closeDrawer(activeDrawer);
        });
    }
}

// Re-translate trigger button labels when language changes
export function refreshFilterDrawerI18n() {
    document.querySelectorAll('.filter-trigger-label, .filter-drawer-title').forEach((el) => {
        if (t) el.textContent = t('filterMobileBtn') || el.textContent;
    });
}
