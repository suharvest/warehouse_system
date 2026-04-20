// Smoke test: verify the add-record modal shows the correct fields when switching
// to outbound mode.
//
// Prerequisites:
//   - Backend on http://localhost:2124
//   - Frontend static server on http://localhost:8080 (serves frontend/)
//   - Admin user "admin" with password "admin"
//
// Run: node stock_out_batch.mjs
//
// This test does NOT require a pre-populated database.
import { chromium } from 'playwright';

const FRONTEND = process.env.FRONTEND_URL || 'http://localhost:8080';
const USERNAME = process.env.TEST_USER || 'admin';
const PASSWORD = process.env.TEST_PASS || 'admin';

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 375, height: 812 } });
const page = await ctx.newPage();

const errors = [];

try {
    await page.goto(FRONTEND, { waitUntil: 'domcontentloaded' });

    // Login if not already
    const loginVisible = await page.isVisible('#login-modal').catch(() => false);
    if (loginVisible) {
        await page.fill('#login-username', USERNAME);
        await page.fill('#login-password', PASSWORD);
        await page.click('[data-action="handleLogin"]');
        await page.waitForSelector('#login-modal', { state: 'hidden', timeout: 5000 }).catch(() => {});
    }

    // Navigate to records tab
    const recordsTab = page.locator('[data-tab="records"]').first();
    if (await recordsTab.count()) {
        await recordsTab.click();
    }

    // Open add-record modal
    await page.click('[data-action="showAddRecordModal"]');
    await page.waitForSelector('#add-record-modal.show', { timeout: 3000 });

    // Switch to outbound
    await page.click('input[name="record-type"][value="out"]');
    await page.waitForTimeout(200);

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

    // Switch back to inbound, verify reversed
    await page.click('input[name="record-type"][value="in"]');
    await page.waitForTimeout(200);

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

if (errors.length) {
    console.error('FAIL:', errors);
    process.exit(1);
}
console.log('OK');
