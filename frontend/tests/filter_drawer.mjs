// Verify mobile filter drawer behaviour:
//  - On 375x812 viewport, inline .filter-bar is hidden, .filter-trigger-bar visible
//  - Clicking the trigger opens drawer; layout is 2-column grid
//  - Changing fields updates badge count
//  - Apply/reset buttons close the drawer
//  - On 1024x768 desktop, filter-bar still inline and trigger hidden
//
// Run:  node tests/filter_drawer.mjs
import { chromium } from 'playwright';
import fs from 'fs';

const FRONTEND = process.env.FRONTEND_URL || 'http://127.0.0.1:2124';
const USERNAME = process.env.TEST_USER || 'admin';
const PASSWORD = process.env.TEST_PASS || 'admin123';
const OUTDIR = process.env.OUTDIR || '/tmp/filter-drawer';
fs.mkdirSync(OUTDIR, { recursive: true });

const browser = await chromium.launch({ headless: true });
const errors = [];

async function login(page) {
    await page.goto(FRONTEND, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1000);
    // Click login button if visible (i.e. not already logged in)
    const loginBtn = page.locator('#login-btn');
    if (await loginBtn.isVisible().catch(() => false)) {
        await loginBtn.click();
        await page.waitForTimeout(400);
        await page.locator('#login-username').fill(USERNAME);
        await page.locator('#login-password').fill(PASSWORD);
        await page.locator('#login-modal [data-action="handleLogin"]').click();
        await page.waitForTimeout(1800);
    }
}

async function gotoTab(page, tab) {
    // Click sidebar/nav item with data-tab attribute, fallback to hash
    const navBtn = page.locator(`[data-tab="${tab}"]`).first();
    if (await navBtn.count() > 0) {
        await navBtn.click().catch(() => {});
    } else {
        await page.evaluate((t) => { location.hash = '#tab=' + t; }, tab);
    }
    await page.waitForTimeout(900);
}

// ---- Mobile viewport ----
const mobileCtx = await browser.newContext({
    viewport: { width: 375, height: 812 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
});
const mPage = await mobileCtx.newPage();
mPage.on('pageerror', (e) => errors.push('mobile: ' + e));
mPage.on('console', (m) => { if (m.type() === 'error') errors.push('mobile-console: ' + m.text()); });

await login(mPage);
await gotoTab(mPage, 'records');

// Screenshot 1: trigger button visible, filter-bar hidden
await mPage.screenshot({ path: `${OUTDIR}/01-records-trigger.png`, fullPage: false });

const triggerVisible = await mPage.locator('#tab-records .filter-trigger-bar').isVisible();
const filterBarHidden = await mPage.locator('#tab-records > .tab-pane > .filter-bar, #tab-records .filter-bar').first().isHidden();
console.log('records: trigger visible =', triggerVisible, '; filter-bar hidden =', filterBarHidden);

// Click trigger to open drawer
await mPage.locator('#tab-records .filter-trigger-btn').click();
await mPage.waitForTimeout(400);
await mPage.screenshot({ path: `${OUTDIR}/02-records-drawer-open.png`, fullPage: false });

const drawerOpen = await mPage.locator('#tab-records .filter-bar.is-drawer-open').isVisible();
console.log('records: drawer open =', drawerOpen);

// Verify 2-column grid layout
const layout = await mPage.evaluate(() => {
    const fb = document.querySelector('#tab-records .filter-bar.is-drawer-open');
    if (!fb) return null;
    const cs = getComputedStyle(fb);
    return {
        display: cs.display,
        gridTemplateColumns: cs.gridTemplateColumns,
        position: cs.position,
        bottom: cs.bottom,
    };
});
console.log('records drawer layout:', layout);

// Change a field — verify badge updates
await mPage.locator('#filter-records-product').fill('test');
await mPage.waitForTimeout(200);
const badgeText = await mPage.locator('#tab-records .filter-trigger-badge').textContent();
console.log('records: badge after typing =', badgeText);
await mPage.screenshot({ path: `${OUTDIR}/03-records-drawer-with-input.png`, fullPage: false });

// Click apply (筛选) — drawer should close
await mPage.locator('#tab-records .filter-bar.is-drawer-open .filter-actions [data-action="applyRecordsFilter"]').click();
await mPage.waitForTimeout(500);
const drawerStillOpen = await mPage.locator('#tab-records .filter-bar.is-drawer-open').isVisible().catch(() => false);
console.log('records: drawer still open after apply =', drawerStillOpen);
await mPage.screenshot({ path: `${OUTDIR}/04-records-after-apply.png`, fullPage: false });

// Inventory tab
await gotoTab(mPage, 'inventory');
await mPage.locator('#tab-inventory .filter-trigger-btn').click();
await mPage.waitForTimeout(400);
await mPage.screenshot({ path: `${OUTDIR}/05-inventory-drawer.png`, fullPage: false });
const invDrawer = await mPage.locator('#tab-inventory .filter-bar.is-drawer-open').isVisible();
console.log('inventory: drawer open =', invDrawer);

// Close via backdrop
await mPage.locator('#filter-drawer-backdrop').click({ position: { x: 100, y: 50 } }).catch(() => {});
await mPage.waitForTimeout(400);

// Contacts tab
await gotoTab(mPage, 'contacts');
await mPage.locator('#tab-contacts .filter-trigger-btn').click();
await mPage.waitForTimeout(400);
await mPage.screenshot({ path: `${OUTDIR}/06-contacts-drawer.png`, fullPage: false });

await mobileCtx.close();

// ---- Desktop viewport: verify untouched ----
const dCtx = await browser.newContext({ viewport: { width: 1024, height: 768 } });
const dPage = await dCtx.newPage();
dPage.on('pageerror', (e) => errors.push('desktop: ' + e));
await login(dPage);
await gotoTab(dPage, 'records');
await dPage.screenshot({ path: `${OUTDIR}/07-desktop-records.png`, fullPage: false });

const desktopState = await dPage.evaluate(() => {
    const fb = document.querySelector('#tab-records .filter-bar');
    const trig = document.querySelector('#tab-records .filter-trigger-bar');
    return {
        filterBarDisplay: fb ? getComputedStyle(fb).display : null,
        triggerDisplay: trig ? getComputedStyle(trig).display : null,
    };
});
console.log('desktop records:', desktopState);

await gotoTab(dPage, 'inventory');
await dPage.screenshot({ path: `${OUTDIR}/08-desktop-inventory.png`, fullPage: false });

await dCtx.close();
await browser.close();

console.log('\n=== ERRORS ===');
console.log(errors.length ? errors.join('\n') : '(none)');
console.log('\nScreenshots in:', OUTDIR);
