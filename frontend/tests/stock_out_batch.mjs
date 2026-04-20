// Smoke test: verify the add-record modal shows the correct fields when
// switching between inbound and outbound modes.
//
// Architecture note
// -----------------
// The backend on :2124 serves both /api/* and the built frontend from
// frontend/dist/. This test targets the backend origin directly so
// everything is single-origin (no CORS/cookie wrangling).
//
// If the frontend source (frontend/index.html, frontend/src/**) has been
// edited but the dist bundle is stale, the served HTML will not contain
// the new DOM ids and this test will fail. Rebuild with:
//
//   cd frontend && ./node_modules/.bin/vite build
//
// Serving frontend/ directly from a static server (e.g. python -m http.server)
// is NOT a viable alternative because components.css uses Tailwind @apply
// directives that require a build step; raw browsers cannot interpret them.
//
// Run:
//   TEST_USER=seeed TEST_PASS=seeed node tests/stock_out_batch.mjs
//
// Environment variables:
//   FRONTEND_URL   Origin serving frontend + /api (default http://localhost:2124)
//   TEST_USER      Login username (default admin)
//   TEST_PASS      Login password (default admin)
import { chromium } from 'playwright';

const FRONTEND = process.env.FRONTEND_URL || 'http://localhost:2124';
const USERNAME = process.env.TEST_USER || 'admin';
const PASSWORD = process.env.TEST_PASS || 'admin';

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 375, height: 812 } });
const page = await ctx.newPage();

const errors = [];
const dialogs = [];
page.on('dialog', async (d) => {
    dialogs.push(d.message());
    await d.accept().catch(() => {});
});

async function ensureLoggedIn() {
    // /api/auth/status tells us whether the session cookie is valid. The app
    // does NOT auto-open the login modal on an unauthenticated session; the
    // user has to click #login-btn first.
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
    await page.waitForSelector('#login-modal', { state: 'hidden', timeout: 10000 });
    return true;
}

// The type radios are visually hidden by the custom styled labels, so
// Playwright refuses to click them. Set .checked and dispatch change
// manually — records.js installs onchange handlers that listen for this.
async function setRecordType(value) {
    await page.evaluate((v) => {
        const input = document.querySelector(`input[name="record-type"][value="${v}"]`);
        if (!input) throw new Error(`record-type ${v} not found`);
        input.checked = true;
        input.dispatchEvent(new Event('change', { bubbles: true }));
    }, value);
}

async function ensureWarehouseSelected() {
    // Single-warehouse installs auto-select via renderWarehouseSwitcher.
    // Multi-warehouse: pick the first real option so the write-guard in
    // showAddRecordModal (currentWarehouse check) doesn't alert us out.
    await page.waitForFunction(() => {
        const el = document.getElementById('warehouseSwitcher');
        return el && el.style.display !== 'none';
    }, { timeout: 10000 });

    const nameText = (await page.locator('#currentWarehouseName').textContent().catch(() => '')) || '';
    if (nameText.includes('全部仓库') || nameText.trim() === '') {
        await page.click('[data-action="toggleWarehouseSwitcher"]').catch(() => {});
        const realOption = page.locator('#warehouseDropdown .warehouse-option[data-slug]:not([data-slug=""])').first();
        if (await realOption.count()) {
            await realOption.click();
        }
    }
}

try {
    await page.goto(FRONTEND, { waitUntil: 'networkidle' });

    const didLogin = await ensureLoggedIn();
    if (didLogin) {
        // Reload so loadWarehouses() re-runs authenticated. Without this
        // allWarehouses stays empty and showAddRecordModal() short-circuits
        // with an alert ("写操作需要选择具体仓库").
        await page.reload({ waitUntil: 'networkidle' });
    }

    await page.waitForSelector('[data-tab="records"]', { state: 'visible', timeout: 10000 });
    await ensureWarehouseSelected();

    await page.locator('[data-tab="records"]').first().click();
    await page.waitForSelector('[data-action="showAddRecordModal"]', { state: 'visible', timeout: 10000 });

    await page.click('[data-action="showAddRecordModal"]');
    await page.waitForSelector('#add-record-modal.show', { timeout: 5000 });

    // Outbound: location + variant + batch-select visible, batch-no hidden.
    await setRecordType('out');
    await page.waitForTimeout(300);

    const locationVisible = await page.isVisible('#record-location-group');
    const variantVisible = await page.isVisible('#record-variant-group');
    const batchSelectVisible = await page.isVisible('#record-batch-select-group');
    const batchNoHidden = !(await page.isVisible('#record-batch-group'));

    console.log(JSON.stringify({
        locationVisible, variantVisible, batchSelectVisible, batchNoHidden,
    }, null, 2));

    await page.screenshot({ path: 'stock-out-modal-out-mobile.png', fullPage: true });

    if (!(locationVisible && variantVisible && batchSelectVisible && batchNoHidden)) {
        errors.push('Outbound field visibility mismatch');
    }

    // Inbound: batch-no visible, batch-select hidden.
    await setRecordType('in');
    await page.waitForTimeout(300);

    const batchNoVisibleIn = await page.isVisible('#record-batch-group');
    const batchSelectHiddenIn = !(await page.isVisible('#record-batch-select-group'));
    console.log(JSON.stringify({ batchNoVisibleIn, batchSelectHiddenIn }, null, 2));

    if (!(batchNoVisibleIn && batchSelectHiddenIn)) {
        errors.push('Inbound field visibility mismatch');
    }

    await page.screenshot({ path: 'stock-out-modal-in-mobile.png', fullPage: true });

} catch (err) {
    errors.push(`Exception: ${err.message}`);
} finally {
    await browser.close();
}

if (dialogs.length) {
    console.error('Unexpected dialogs:', dialogs);
    errors.push(`Unexpected dialogs: ${dialogs.join(' | ')}`);
}

if (errors.length) {
    console.error('FAIL:', errors);
    process.exit(1);
}
console.log('OK');
