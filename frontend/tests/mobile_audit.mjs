// Mobile UI audit: cycle through all main tabs + key modals at iPhone 13 Pro
// (375x812) viewport and capture screenshots. Counts JS errors and reports
// horizontal-overflow violations. Output dir is configurable via OUTDIR env.
//
// Run:
//   OUTDIR=/tmp/mobile-before node tests/mobile_audit.mjs
//   OUTDIR=/tmp/mobile-after  node tests/mobile_audit.mjs
//
// Env:
//   FRONTEND_URL   default http://localhost:2124
//   TEST_USER      default admin
//   TEST_PASS      default admin
//   OUTDIR         default /tmp/mobile-before
import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const FRONTEND = process.env.FRONTEND_URL || 'http://localhost:2124';
const USERNAME = process.env.TEST_USER || 'admin';
const PASSWORD = process.env.TEST_PASS || 'admin';
const OUTDIR = process.env.OUTDIR || '/tmp/mobile-before';

fs.mkdirSync(OUTDIR, { recursive: true });

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({
    viewport: { width: 375, height: 812 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
});
const page = await ctx.newPage();

const consoleErrors = [];
const pageErrors = [];
page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
});
page.on('pageerror', (err) => pageErrors.push(String(err)));
page.on('dialog', (d) => d.accept().catch(() => {}));

const overflows = [];
async function checkOverflow(label) {
    const info = await page.evaluate(() => {
        const docW = document.documentElement.scrollWidth;
        const winW = window.innerWidth;
        // Find offending elements wider than viewport
        const offenders = [];
        const all = document.querySelectorAll('body *');
        for (const el of all) {
            const r = el.getBoundingClientRect();
            if (r.width > winW + 1 && r.height > 0) {
                // Skip ancestors covered by descendants — keep elements that
                // actually overflow viewport on the right edge.
                if (r.right > winW + 1) {
                    offenders.push({
                        tag: el.tagName.toLowerCase(),
                        cls: (el.className || '').toString().slice(0, 80),
                        id: el.id || '',
                        w: Math.round(r.width),
                        right: Math.round(r.right),
                    });
                }
            }
            if (offenders.length >= 5) break;
        }
        return { docW, winW, offenders };
    });
    if (info.docW > info.winW + 1 || info.offenders.length) {
        overflows.push({ label, ...info });
    }
}

async function shoot(name) {
    const file = path.join(OUTDIR, `${name}.png`);
    await page.screenshot({ path: file, fullPage: true });
    await checkOverflow(name);
    return file;
}

async function ensureLoggedIn() {
    // Try up to 3 times in case of rate limit
    for (let i = 0; i < 3; i++) {
        const status = await page.evaluate(async () => {
            const r = await fetch('/api/auth/status', { credentials: 'include' });
            return r.json();
        });
        if (status && status.logged_in) return false;

        const loginBtn = page.locator('#login-btn');
        if (await loginBtn.isVisible().catch(() => false)) {
            await loginBtn.click();
        }
        await page.waitForSelector('#login-modal.show', { timeout: 5000 });
        await page.fill('#login-username', USERNAME);
        await page.fill('#login-password', PASSWORD);
        await page.click('[data-action="handleLogin"]');
        try {
            await page.waitForSelector('#login-modal', { state: 'hidden', timeout: 8000 });
            return true;
        } catch (e) {
            // Maybe rate limited; close modal manually if shown then wait
            console.log('Login wait failed, retry', i + 1);
            await page.waitForTimeout(15000);
        }
    }
    throw new Error('login failed');
}

async function clickTab(tabId) {
    const sel = `[data-tab="${tabId}"]`;
    await page.locator(sel).first().click({ force: true });
    await page.waitForTimeout(800);
}

async function tryShootTab(tabId, name) {
    try {
        await clickTab(tabId);
        await shoot(name);
        return true;
    } catch (e) {
        console.log(`tab ${tabId} failed:`, e.message);
        return false;
    }
}

const results = { tabs: [], modals: [] };

try {
    await page.goto(FRONTEND, { waitUntil: 'networkidle' });
    await shoot('00-landing-pre-login');

    const didLogin = await ensureLoggedIn();
    if (didLogin) {
        await page.reload({ waitUntil: 'networkidle' });
    }
    await page.waitForSelector('[data-tab="dashboard"]', { state: 'visible', timeout: 10000 });

    // Pick a warehouse if needed (replicates stock_out_batch.mjs)
    await page.waitForFunction(() => {
        const el = document.getElementById('warehouseSwitcher');
        return el && el.style.display !== 'none';
    }, { timeout: 10000 }).catch(() => {});

    const nameText = (await page.locator('#currentWarehouseName').textContent().catch(() => '')) || '';
    if (nameText.includes('全部仓库') || nameText.trim() === '') {
        await page.click('[data-action="toggleWarehouseSwitcher"]').catch(() => {});
        const realOption = page.locator('#warehouseDropdown .warehouse-option[data-slug]:not([data-slug=""])').first();
        if (await realOption.count()) {
            await realOption.click();
            await page.waitForTimeout(500);
        } else {
            // Click outside to close
            await page.click('body', { position: { x: 10, y: 10 } }).catch(() => {});
        }
    }

    // Tabs to capture
    const tabs = [
        ['dashboard', '01-dashboard'],
        ['records', '02-records'],
        ['inventory', '03-inventory'],
        ['detail', '04-detail'],
        ['contacts', '05-contacts'],
        ['users', '06-users'],
        ['mcp', '07-mcp'],
        ['tenants', '08-tenants'],
    ];
    for (const [t, name] of tabs) {
        const ok = await tryShootTab(t, name);
        results.tabs.push({ tab: t, name, ok });
    }

    // Settings sub-tabs (under users)
    await clickTab('users').catch(() => {});
    const subtabs = await page.locator('.sub-tab[data-subtab], .sub-tab').all().catch(() => []);
    let subShotIdx = 0;
    for (const tab of subtabs) {
        const tname = (await tab.textContent().catch(() => '')).trim().slice(0, 12);
        try {
            await tab.click({ force: true });
            await page.waitForTimeout(400);
            await shoot(`06-users-sub-${++subShotIdx}-${tname}`);
        } catch (e) {}
    }

    // Header dropdowns
    await clickTab('dashboard');
    await page.click('[data-action="toggleLangDropdown"]').catch(() => {});
    await page.waitForTimeout(200);
    await shoot('09-lang-dropdown');
    await page.click('body', { position: { x: 10, y: 10 } }).catch(() => {});
    await page.waitForTimeout(150);

    await page.click('[data-action="toggleWarehouseSwitcher"]').catch(() => {});
    await page.waitForTimeout(200);
    await shoot('10-warehouse-dropdown');
    await page.click('body', { position: { x: 10, y: 10 } }).catch(() => {});
    await page.waitForTimeout(150);

    // Modals: add-record (records tab)
    await clickTab('records');
    await page.waitForSelector('[data-action="showAddRecordModal"]', { state: 'visible', timeout: 5000 });
    await page.click('[data-action="showAddRecordModal"]');
    await page.waitForSelector('#add-record-modal.show', { timeout: 5000 });
    await page.waitForTimeout(300);
    await shoot('M1-add-record-modal');
    // close
    await page.locator('#add-record-modal .close-btn, #add-record-modal [data-action="hideAddRecordModal"]').first().click({ force: true }).catch(() => {});
    await page.waitForTimeout(200);

    // Import CSV modal (inventory tab)
    await clickTab('inventory');
    const importBtn = page.locator('[data-action="showImportModal"]').first();
    if (await importBtn.count()) {
        await importBtn.click().catch(() => {});
        await page.waitForTimeout(400);
        if (await page.locator('#import-modal.show').count()) {
            await shoot('M2-import-modal');
            await page.locator('#import-modal .close-btn').first().click({ force: true }).catch(() => {});
            await page.waitForTimeout(200);
        }
    }

    // Add contact modal
    await clickTab('contacts');
    const addContactBtn = page.locator('[data-action="showAddContactModal"], [data-action="showContactModal"]').first();
    if (await addContactBtn.count()) {
        await addContactBtn.click().catch(() => {});
        await page.waitForTimeout(400);
        if (await page.locator('#contact-modal.show').count()) {
            await shoot('M3-contact-modal');
            await page.locator('#contact-modal .close-btn').first().click({ force: true }).catch(() => {});
            await page.waitForTimeout(200);
        }
    }

    // Warehouse modal (admin -> users -> warehouses subtab)
    await clickTab('users');
    const whSubtab = page.locator('.sub-tab[data-subtab="warehouses"], [data-action="switchSubtab"][data-subtab="warehouses"]').first();
    if (await whSubtab.count()) {
        await whSubtab.click({ force: true }).catch(() => {});
        await page.waitForTimeout(400);
        const addWhBtn = page.locator('[data-action="showAddWarehouseModal"], [data-action="showWarehouseModal"]').first();
        if (await addWhBtn.count()) {
            await addWhBtn.click({ force: true }).catch(() => {});
            await page.waitForTimeout(400);
            if (await page.locator('#warehouse-modal.show').count()) {
                await shoot('M4-warehouse-modal');
                await page.locator('#warehouse-modal .close-btn').first().click({ force: true }).catch(() => {});
                await page.waitForTimeout(200);
            }
        }
    }

    // Add user modal
    const userSubtab = page.locator('.sub-tab[data-subtab="users"], [data-action="switchSubtab"][data-subtab="users"]').first();
    if (await userSubtab.count()) {
        await userSubtab.click({ force: true }).catch(() => {});
        await page.waitForTimeout(400);
        const addUserBtn = page.locator('[data-action="showAddUserModal"]').first();
        if (await addUserBtn.count()) {
            await addUserBtn.click({ force: true }).catch(() => {});
            await page.waitForTimeout(400);
            if (await page.locator('#add-user-modal.show').count()) {
                await shoot('M5-add-user-modal');
                await page.locator('#add-user-modal .close-btn').first().click({ force: true }).catch(() => {});
            }
        }
    }

} catch (err) {
    console.error('Audit error:', err);
    results.fatal = String(err);
} finally {
    await browser.close();
}

const summary = {
    outdir: OUTDIR,
    consoleErrors: consoleErrors.length,
    pageErrors: pageErrors.length,
    overflowViolations: overflows.length,
    consoleErrorSamples: consoleErrors.slice(0, 5),
    pageErrorSamples: pageErrors.slice(0, 5),
    overflows,
    tabs: results.tabs,
};
fs.writeFileSync(path.join(OUTDIR, 'summary.json'), JSON.stringify(summary, null, 2));
console.log(JSON.stringify(summary, null, 2));
