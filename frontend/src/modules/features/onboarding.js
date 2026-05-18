// ============ 新人引导模块 ============
import { getCurrentUser } from '../state.js';
import { switchTab } from '../ui/tabs.js';
import { showAddMCPModal, closeMCPModal } from './mcp.js';

let currentStep = 0;
let active = false;
let bubbleEl = null;
let overlayEl = null;

const TOTAL_STEPS = 5;

const STEPS = {
    1: {
        target: () => document.querySelector('[data-tab="mcp"]'),
        text: '点击左侧「智能体配置」开始配置设备连接',
        advanceOn: 'switchTab-mcp',
        placement: 'right',
    },
    2: {
        target: () => document.querySelector('[data-action="showAddMCPModal"]'),
        text: '点击「添加智能体」创建第一个设备连接',
        advanceOn: 'showAddMCPModal',
        placement: 'bottom',
    },
    3: {
        target: () => document.querySelector('#mcp-conn-endpoint'),
        text: '填写连接信息后点击「保存并启动」。Endpoint 旁有 ? 图标可查看地址获取方式',
        advanceOn: 'handleSaveMCP',
        placement: 'bottom',
    },
    4: {
        target: () => document.querySelector('[data-tab="inventory"]'),
        text: '点击左侧「库存列表」进入库存管理',
        advanceOn: 'switchTab-inventory',
        placement: 'right',
    },
    5: {
        target: () => document.querySelector('[data-action="showImportModal"]'),
        text: '点击「导入库存」上传数据。首次使用可点击下方「下载示例文件」获取模板',
        advanceOn: 'showImportModal',
        placement: 'bottom',
    },
};

export function startOnboarding() {
    if (active) return;
    const user = getCurrentUser();
    if (!user || user.role !== 'admin') return;

    active = true;
    currentStep = 1;
    showStep(1);
}

async function showStep(step) {
    dismissBubble();
    const cfg = STEPS[step];
    if (!cfg) { endOnboarding(); return; }

    await prepareStep(step);

    const target = cfg.target();
    if (!isUsableTarget(target)) {
        setTimeout(() => showStep(step + 1), 500);
        return;
    }

    // Scroll instantly (not smooth) so getBoundingClientRect is correct immediately
    target.scrollIntoView({ behavior: 'instant', block: 'center' });

    target.classList.add('onboarding-spotlight');

    if (target.closest('.sidebar')) {
        target.scrollIntoView({ behavior: 'instant', block: 'center' });
    }

    if (!overlayEl) {
        overlayEl = document.createElement('div');
        overlayEl.className = 'onboarding-overlay';
        document.body.appendChild(overlayEl);
    }
    overlayEl.style.display = 'block';

    elevateModalIfNeeded(target);

    target.style.position = 'relative';
    target.style.zIndex = '9999';

    // Wait for target to be visible and have non-zero dimensions,
    // then show the bubble positioned correctly
    waitForVisible(target, () => {
        const rect = target.getBoundingClientRect();
        showBubble(rect, cfg.text, step, cfg.placement);
    });
}

async function prepareStep(step) {
    if (step === 2) {
        switchTab('mcp');
        await waitForLayout();
    } else if (step === 3) {
        switchTab('mcp');
        await showAddMCPModal();
        await waitForLayout();
    } else if (step === 4) {
        closeMCPModal();
        switchTab('inventory');
        await waitForLayout();
    } else if (step === 5) {
        switchTab('inventory');
        await waitForLayout();
    }
}

function waitForLayout() {
    return new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
}

function elevateModalIfNeeded(target) {
    const modal = target.closest('.modal');
    if (!modal) return;

    modal.classList.add('onboarding-modal-elevated');
    if (!modal.dataset.obPreviousZIndex) {
        modal.dataset.obPreviousZIndex = modal.style.zIndex || '';
    }
    modal.style.zIndex = '9999';
}

function isUsableTarget(target) {
    if (!target) return false;
    const rect = target.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

function waitForVisible(el, cb, maxWait = 2000) {
    const rect = el.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) {
        cb();
        return;
    }
    let elapsed = 0;
    const interval = setInterval(() => {
        elapsed += 50;
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) {
            clearInterval(interval);
            cb();
        } else if (elapsed >= maxWait) {
            clearInterval(interval);
            cb(); // fallback — show anyway, even if position may be off
        }
    }, 50);
}

function showBubble(targetRect, text, step, placement) {
    if (bubbleEl) bubbleEl.remove();

    bubbleEl = document.createElement('div');
    bubbleEl.className = 'onboarding-bubble';
    bubbleEl.setAttribute('data-arrow', placement === 'right' ? 'left' : 'top');

    const isLast = step === TOTAL_STEPS;

    bubbleEl.innerHTML = `
        <div class="ob-step">步骤 ${step} / ${TOTAL_STEPS}</div>
        <div class="ob-text">${text}</div>
        <div class="ob-actions">
            <button class="ob-btn ob-btn-ghost" data-ob-action="skip">跳过</button>
            <button class="ob-btn ob-btn-primary" data-ob-action="next">${isLast ? '完成' : '下一步'}</button>
        </div>
    `;

    document.body.appendChild(bubbleEl);

    const bubbleRect = bubbleEl.getBoundingClientRect();
    let top, left;

    if (placement === 'right') {
        top = targetRect.top + targetRect.height / 2 - bubbleRect.height / 2;
        left = targetRect.right + 16;
    } else {
        top = targetRect.bottom + 12;
        left = targetRect.left + targetRect.width / 2 - bubbleRect.width / 2;
    }

    const margin = 16;
    top = Math.max(margin, Math.min(top, window.innerHeight - bubbleRect.height - margin));
    left = Math.max(margin, Math.min(left, window.innerWidth - bubbleRect.width - margin));

    bubbleEl.style.top = top + 'px';
    bubbleEl.style.left = left + 'px';

    // Button handlers — never auto-perform actions, just advance the step
    bubbleEl.querySelector('[data-ob-action="skip"]').addEventListener('click', (e) => {
        e.stopPropagation();
        endOnboarding();
    });
    bubbleEl.querySelector('[data-ob-action="next"]').addEventListener('click', (e) => {
        e.stopPropagation();
        if (isLast) {
            endOnboarding();
        } else {
            advanceStep();
        }
    });
}

function dismissBubble() {
    if (bubbleEl) {
        bubbleEl.remove();
        bubbleEl = null;
    }

    document.querySelectorAll('.onboarding-spotlight').forEach(el => {
        el.classList.remove('onboarding-spotlight');
        el.style.position = '';
        el.style.zIndex = '';
    });

    document.querySelectorAll('.onboarding-modal-elevated').forEach(el => {
        el.classList.remove('onboarding-modal-elevated');
        el.style.zIndex = el.dataset.obPreviousZIndex || '';
        delete el.dataset.obPreviousZIndex;
    });

    if (overlayEl) {
        overlayEl.style.display = 'none';
    }
}

function advanceStep() {
    currentStep++;
    // longer delay for step 2→3 (modal opening) and step 3→4 (modal closing after save)
    const delay = (currentStep === 3 || currentStep === 4) ? 600 : 350;
    setTimeout(() => showStep(currentStep), delay);
}

function endOnboarding() {
    dismissBubble();
    if (overlayEl) {
        overlayEl.remove();
        overlayEl = null;
    }
    active = false;
    currentStep = 0;
}

// Capture-phase click listener: auto-advance when user clicks the expected target
document.addEventListener('click', function (e) {
    if (!active) return;

    const actionEl = e.target.closest('[data-action]');
    if (!actionEl) return;

    const action = actionEl.dataset.action;
    const stepCfg = STEPS[currentStep];
    if (!stepCfg || !stepCfg.advanceOn) return;

    const expected = stepCfg.advanceOn;
    let matched = false;

    if (expected === 'switchTab-mcp' && action === 'switchTab' && actionEl.dataset.tab === 'mcp') {
        matched = true;
    } else if (expected === 'showAddMCPModal' && action === 'showAddMCPModal') {
        matched = true;
    } else if (expected === 'handleSaveMCP' && action === 'handleSaveMCP') {
        matched = true;
    } else if (expected === 'switchTab-inventory' && action === 'switchTab' && actionEl.dataset.tab === 'inventory') {
        matched = true;
    } else if (expected === 'showImportModal' && action === 'showImportModal') {
        matched = true;
    }

    if (matched) {
        advanceStep();
    }
}, true);
