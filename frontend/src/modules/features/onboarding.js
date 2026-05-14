// ============ 新人引导模块 ============
import { switchTab } from '../ui/tabs.js';
import { showAddMCPModal } from './mcp.js';
import { showImportModal, downloadSampleExcel } from './import-export.js';

let currentStep = 0;
let active = false;
let bubbleEl = null;
let overlayEl = null;

const TOTAL_STEPS = 4;

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
        text: '填写连接信息后保存。Endpoint 旁有 ? 图标可查看地址获取方式',
        advanceOn: null,
        placement: 'bottom',
    },
    4: {
        target: () => document.querySelector('#import-modal .upload-area'),
        text: '选择 Excel 文件导入库存数据。首次使用可点击下方「下载示例文件」获取模板，按格式填写后上传',
        advanceOn: null,
        placement: 'bottom',
    },
};

export function startOnboarding() {
    if (active) return;
    active = true;
    currentStep = 1;
    showStep(1);
}

export function isOnboardingActive() {
    return active;
}

export function getCurrentStep() {
    return currentStep;
}

function showStep(step) {
    dismissBubble();
    const cfg = STEPS[step];
    if (!cfg) { endOnboarding(); return; }

    const target = cfg.target();
    if (!target) {
        // Target not found, skip to next step after a short delay
        setTimeout(() => showStep(step + 1), 300);
        return;
    }

    // Scroll target into view
    target.scrollIntoView({ behavior: 'smooth', block: 'center' });

    // Add spotlight highlight
    target.classList.add('onboarding-spotlight');

    // If sidebar nav item, also ensure the sidebar is scrolled
    if (target.closest('.sidebar')) {
        target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    // Create overlay (blocks clicks to other elements)
    if (!overlayEl) {
        overlayEl = document.createElement('div');
        overlayEl.className = 'onboarding-overlay';
        document.body.appendChild(overlayEl);
    }
    overlayEl.style.display = 'block';

    // Ensure target is above overlay
    target.style.position = 'relative';
    target.style.zIndex = '9999';

    // Show bubble after a short delay (wait for scroll + spotlight animation)
    setTimeout(() => {
        const rect = target.getBoundingClientRect();
        showBubble(rect, cfg.text, step, cfg.placement, () => advanceStep());
    }, 400);
}

function showBubble(targetRect, text, step, placement, onNext) {
    if (bubbleEl) bubbleEl.remove();

    bubbleEl = document.createElement('div');
    bubbleEl.className = 'onboarding-bubble';
    bubbleEl.setAttribute('data-arrow', placement === 'right' ? 'left' : 'top');
    if (placement === 'right') {
        bubbleEl.setAttribute('data-arrow', 'left');
    } else {
        bubbleEl.setAttribute('data-arrow', 'top');
    }

    bubbleEl.innerHTML = `
        <div class="ob-step">步骤 ${step} / ${TOTAL_STEPS}</div>
        <div class="ob-text">${text}</div>
        <div class="ob-actions">
            <button class="ob-btn ob-btn-ghost" data-ob-action="skip">跳过</button>
            <button class="ob-btn ob-btn-primary" data-ob-action="next">${step === TOTAL_STEPS ? '完成' : '下一步'}</button>
        </div>
    `;

    document.body.appendChild(bubbleEl);

    // Position bubble
    const bubbleRect = bubbleEl.getBoundingClientRect();
    let top, left;

    if (placement === 'right') {
        top = targetRect.top + targetRect.height / 2 - bubbleRect.height / 2;
        left = targetRect.right + 16;
    } else {
        top = targetRect.bottom + 12;
        left = targetRect.left + targetRect.width / 2 - bubbleRect.width / 2;
    }

    // Clamp to viewport
    const margin = 16;
    top = Math.max(margin, Math.min(top, window.innerHeight - bubbleRect.height - margin));
    left = Math.max(margin, Math.min(left, window.innerWidth - bubbleRect.width - margin));

    bubbleEl.style.top = top + 'px';
    bubbleEl.style.left = left + 'px';

    // Arrow alignment
    if (placement === 'right') {
        bubbleEl.setAttribute('data-arrow', 'left');
        const arrowTop = targetRect.top + targetRect.height / 2 - top;
        bubbleEl.style.setProperty('--arrow-offset', arrowTop + 'px');
    } else {
        bubbleEl.setAttribute('data-arrow', 'top');
        const arrowLeft = targetRect.left + targetRect.width / 2 - left;
        bubbleEl.style.setProperty('--arrow-offset', arrowLeft + 'px');
    }

    // Button handlers
    bubbleEl.querySelector('[data-ob-action="skip"]').addEventListener('click', (e) => {
        e.stopPropagation();
        endOnboarding();
    });
    bubbleEl.querySelector('[data-ob-action="next"]').addEventListener('click', (e) => {
        e.stopPropagation();
        if (step === TOTAL_STEPS) {
            endOnboarding();
        } else if (step === 1) {
            switchTab('mcp');
            advanceStep();
        } else if (step === 2) {
            showAddMCPModal();
            advanceStep();
        } else if (step === 3) {
            switchTab('inventory');
            // Wait for tab to render, then open import modal
            setTimeout(() => { showImportModal(); advanceStep(); }, 500);
        }
    });

    // Prevent overlay clicks from dismissing (user must click buttons)
    if (overlayEl) {
        overlayEl.onclick = null; // overlay blocks misclicks but doesn't dismiss
    }
}

function dismissBubble() {
    if (bubbleEl) {
        bubbleEl.remove();
        bubbleEl = null;
    }

    // Remove spotlight from all elements
    document.querySelectorAll('.onboarding-spotlight').forEach(el => {
        el.classList.remove('onboarding-spotlight');
        el.style.position = '';
        el.style.zIndex = '';
    });

    if (overlayEl) {
        overlayEl.style.display = 'none';
    }
}

function advanceStep() {
    currentStep++;
    // Small delay so UI updates (modal opens, tab switches etc.) before showing next step
    setTimeout(() => showStep(currentStep), currentStep === 3 ? 500 : 300);
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

// Hook into click events to detect when user clicks the target directly
// (not via our bubble button)
document.addEventListener('click', function (e) {
    if (!active) return;

    const actionEl = e.target.closest('[data-action]');
    if (!actionEl) return;

    const action = actionEl.dataset.action;
    const stepCfg = STEPS[currentStep];
    if (!stepCfg || !stepCfg.advanceOn) return;

    const expected = stepCfg.advanceOn;
    if (expected === 'switchTab-mcp' && action === 'switchTab' && actionEl.dataset.tab === 'mcp') {
        advanceStep();
    } else if (expected === 'showAddMCPModal' && action === 'showAddMCPModal') {
        advanceStep();
    }
}, true);  // capture phase — fire before the action handler
