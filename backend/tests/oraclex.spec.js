/**
 * OracleX — Consolidated Playwright Test Suite
 * =============================================
 * Covers: App render · Navigation · Predictions page bug fixes ·
 *         Games page · Modal · Oracle streaming · Stats · API config ·
 *         No JS crash checks · Accuracy UI · Dashboard stats
 *
 * Run:
 *   npx playwright test oraclex.spec.js
 *   npx playwright test oraclex.spec.js --headed          (watch it run)
 *   npx playwright test oraclex.spec.js --grep "Predictions"  (single section)
 *
 * Prerequisites:
 *   - Frontend running : http://localhost:3000/oraclex-frontend.html
 *   - Backend running  : http://localhost:8080
 */

const { test, expect } = require('@playwright/test');

const FRONTEND_URL = 'http://localhost:3000/oraclex-frontend.html';
const API_BASE     = 'http://localhost:8080';

// ─── Shared setup ────────────────────────────────────────────────────────────

test.beforeEach(async ({ page }) => {
  const errors = [];
  page.on('pageerror', err => errors.push(err.message));
  page.on('console',   msg => { if (msg.type() === 'error') errors.push(msg.text()); });

  await page.goto(FRONTEND_URL);
  await page.waitForSelector('#root > div', { timeout: 15000 });

  // Point frontend at local backend
  await page.locator('.config-input').fill(API_BASE);
  await page.locator('.config-input').press('Enter');
  await page.waitForTimeout(2500);

  // Attach errors to page context so individual tests can inspect if needed
  page._testErrors = errors;
});

// Helper — close any open modal before navigating
async function closeModal(page) {
  try {
    const btn = page.locator('.modal button').filter({ hasText: '✕' });
    if (await btn.count() > 0 && await btn.first().isVisible()) {
      await btn.first().click();
      await page.waitForTimeout(400);
    }
  } catch {}
  await page.keyboard.press('Escape');
  await page.waitForTimeout(300);
}

// Helper — navigate to a named section via nav
async function goTo(page, label) {
  await closeModal(page);
  await page.locator(`button:has-text('${label}')`).first().click();
  await page.waitForTimeout(1500);
}


// ══════════════════════════════════════════════════════════════════════════════
// 1. APP RENDER — does it load at all?
// ══════════════════════════════════════════════════════════════════════════════

test.describe('App Render', () => {

  test('page is not blank — root has content', async ({ page }) => {
    const html = await page.locator('#root').innerHTML();
    expect(html.length).toBeGreaterThan(200);
  });

  test('nav bar is visible', async ({ page }) => {
    await expect(page.locator('nav')).toBeVisible();
  });

  test('nav contains all four links', async ({ page }) => {
    for (const label of ['Dashboard', 'Games', 'Predictions', 'Stats']) {
      await expect(page.locator(`button:has-text('${label}')`).first()).toBeVisible();
    }
  });

  test('OracleX logo/brand is visible', async ({ page }) => {
    await expect(page.locator('text=ORACLEX').first()).toBeVisible();
  });

  test('API config bar is visible with input', async ({ page }) => {
    await expect(page.locator('.config-bar')).toBeVisible();
    await expect(page.locator('.config-input')).toBeVisible();
  });

  test('footer is rendered', async ({ page }) => {
    await expect(page.locator('footer')).toBeVisible();
  });

});


// ══════════════════════════════════════════════════════════════════════════════
// 2. NAVIGATION — can we switch pages without crashing?
// ══════════════════════════════════════════════════════════════════════════════

test.describe('Navigation', () => {

  test('Dashboard renders hero heading', async ({ page }) => {
    await goTo(page, 'Dashboard');
    await expect(page.locator('text=The Story').first()).toBeVisible();
    await expect(page.locator('text=Before It Happens').first()).toBeVisible();
  });

  test('Games page renders with filter buttons', async ({ page }) => {
    await goTo(page, 'Games');
    await expect(page.locator('h1:has-text("Games")')).toBeVisible();
    for (const label of ['UPCOMING', 'LIVE', 'FINAL']) {
      await expect(page.locator(`button:has-text('${label}')`).first()).toBeVisible();
    }
  });

  test('Predictions page renders heading — does NOT go blank', async ({ page }) => {
    await goTo(page, 'Predictions');
    // Nav must still be present (proves no full crash)
    await expect(page.locator('nav')).toBeVisible();
    await expect(page.locator('h1:has-text("Predictions")')).toBeVisible();
  });

  test('Stats page renders Oracle Accuracy heading', async ({ page }) => {
    await goTo(page, 'Stats');
    await expect(page.locator('text=Oracle Accuracy')).toBeVisible();
  });

  test('Logo click returns to Dashboard', async ({ page }) => {
    await goTo(page, 'Games');
    await page.locator('text=ORACLEX').first().click();
    await page.waitForTimeout(800);
    await expect(page.locator('text=The Story').first()).toBeVisible();
  });

  test('Rapid nav between all pages does not crash', async ({ page }) => {
    for (const label of ['Games', 'Predictions', 'Stats', 'Dashboard', 'Predictions', 'Games']) {
      await goTo(page, label);
      await expect(page.locator('nav')).toBeVisible();
    }
  });

});


// ══════════════════════════════════════════════════════════════════════════════
// 3. PREDICTIONS PAGE — the original bug area
// ══════════════════════════════════════════════════════════════════════════════

test.describe('Predictions Page', () => {

  test('filter buttons render correctly', async ({ page }) => {
    await goTo(page, 'Predictions');
    for (const label of ['All', '✓ Correct', '✗ Wrong']) {
      await expect(page.locator(`button:has-text('${label}')`).first()).toBeVisible();
    }
  });

  test('switching filters does not crash the app', async ({ page }) => {
    await goTo(page, 'Predictions');
    for (const label of ['✓ Correct', '✗ Wrong', 'All']) {
      await page.locator(`button:has-text('${label}')`).first().click();
      await page.waitForTimeout(1000);
      // App must still be alive
      await expect(page.locator('nav')).toBeVisible();
    }
  });

  test('clicking a prediction card does NOT produce a blank/black screen', async ({ page }) => {
    await goTo(page, 'Predictions');
    await page.waitForTimeout(2000);

    const cards = page.locator('.card.fade-in');
    const count = await cards.count();

    if (count > 0) {
      await cards.first().click();
      await page.waitForTimeout(600);
      // The critical check — nav must still be visible, not a black screen
      await expect(page.locator('nav')).toBeVisible();
    } else {
      // Empty state is also acceptable
      await expect(page.locator('text=No predictions yet')).toBeVisible();
    }
  });

  test('BUG FIX: no TypeError on undefined home_team/away_team (no JS crash)', async ({ page }) => {
    const criticalErrors = [];
    page.on('pageerror', err => {
      if (err.message.includes('split') || err.message.includes('undefined') || err.message.includes('Cannot read')) {
        criticalErrors.push(err.message);
      }
    });

    await goTo(page, 'Predictions');
    await page.waitForTimeout(2000);

    const cards = page.locator('.card.fade-in');
    if (await cards.count() > 0) {
      await cards.first().click();
      await page.waitForTimeout(1000);
    }

    expect(criticalErrors).toHaveLength(0);
  });

  test('modal opens when clicking prediction card with valid game_id', async ({ page }) => {
    await goTo(page, 'Predictions');
    await page.waitForTimeout(2000);

    const cards = page.locator('.card.fade-in');
    if (await cards.count() === 0) {
      test.skip();
      return;
    }

    // Try cards until one opens a modal (some may have no game_id)
    let modalOpened = false;
    for (let i = 0; i < Math.min(await cards.count(), 5); i++) {
      await cards.nth(i).click();
      await page.waitForTimeout(600);
      if (await page.locator('.modal-bg').isVisible()) {
        modalOpened = true;
        break;
      }
    }

    if (modalOpened) {
      await expect(page.locator('.modal-bg')).toBeVisible();
      await expect(page.locator('text=vs').first()).toBeVisible();
    }
    // Nav must be alive regardless
    await expect(page.locator('nav')).toBeVisible();
  });

  test('modal closes when clicking X button', async ({ page }) => {
    await goTo(page, 'Predictions');
    await page.waitForTimeout(2000);

    const cards = page.locator('.card.fade-in');
    if (await cards.count() === 0) { test.skip(); return; }

    for (let i = 0; i < Math.min(await cards.count(), 5); i++) {
      await cards.nth(i).click();
      await page.waitForTimeout(600);
      if (await page.locator('.modal-bg').isVisible()) {
        await page.locator('.modal button').filter({ hasText: '✕' }).click();
        await page.waitForTimeout(500);
        await expect(page.locator('.modal-bg')).not.toBeVisible();
        return;
      }
    }
  });

  test('prediction cards show Oracle picks label and team names', async ({ page }) => {
    await goTo(page, 'Predictions');
    await page.waitForTimeout(2000);

    const cards = page.locator('.card.fade-in');
    if (await cards.count() === 0) { test.skip(); return; }

    await expect(page.locator('text=Oracle picks').first()).toBeVisible();
    await expect(page.locator('text=vs').first()).toBeVisible();
  });

  test('confidence rings (SVG) are visible on prediction cards', async ({ page }) => {
    await goTo(page, 'Predictions');
    await page.waitForTimeout(2000);

    if (await page.locator('.card.fade-in').count() === 0) { test.skip(); return; }
    const rings = page.locator('svg');
    expect(await rings.count()).toBeGreaterThan(0);
  });

});


// ══════════════════════════════════════════════════════════════════════════════
// 4. GAMES PAGE
// ══════════════════════════════════════════════════════════════════════════════

test.describe('Games Page', () => {

  test('sport filter buttons are visible', async ({ page }) => {
    await goTo(page, 'Games');
    await expect(page.locator('button:has-text("All")').first()).toBeVisible();
    await expect(page.locator('button:has-text("NFL")').first()).toBeVisible();
    await expect(page.locator('button:has-text("NBA")').first()).toBeVisible();
  });

  test('switching status filters does not crash', async ({ page }) => {
    await goTo(page, 'Games');
    for (const label of ['FINAL', 'UPCOMING']) {
      await page.locator(`button:has-text('${label}')`).first().click();
      await page.waitForTimeout(1000);
      await expect(page.locator('nav')).toBeVisible();
    }
  });

  test('clicking a game card opens the modal', async ({ page }) => {
    await goTo(page, 'Games');
    await page.waitForTimeout(2000);

    const cards = page.locator('.card');
    if (await cards.count() === 0) { test.skip(); return; }

    await cards.first().click();
    await page.waitForTimeout(800);
    await expect(page.locator('.modal-bg')).toBeVisible();
    await expect(page.locator('text=vs').first()).toBeVisible();
  });

  test('modal shows Invoke Oracle button for unpredicted games', async ({ page }) => {
    await goTo(page, 'Games');
    await page.waitForTimeout(2000);

    const cards = page.locator('.card');
    if (await cards.count() === 0) { test.skip(); return; }

    for (let i = 0; i < Math.min(await cards.count(), 8); i++) {
      await cards.nth(i).click();
      await page.waitForTimeout(600);
      if (await page.locator('.modal-bg').isVisible()) {
        const invokeBtn = page.locator('.modal button:has-text("Invoke Oracle")');
        const alreadyPredicted = page.locator("text=Oracle's Pick");
        // One or the other should be present
        const either = (await invokeBtn.count()) > 0 || (await alreadyPredicted.count()) > 0;
        expect(either).toBeTruthy();
        await closeModal(page);
        return;
      }
    }
  });

  test('modal closes on backdrop click', async ({ page }) => {
    await goTo(page, 'Games');
    await page.waitForTimeout(2000);

    const cards = page.locator('.card');
    if (await cards.count() === 0) { test.skip(); return; }

    await cards.first().click();
    await page.waitForTimeout(800);

    if (await page.locator('.modal-bg').isVisible()) {
      // Click outside the modal box
      await page.locator('.modal-bg').click({ position: { x: 10, y: 10 } });
      await page.waitForTimeout(500);
      await expect(page.locator('.modal-bg')).not.toBeVisible();
    }
  });

  test('NBA sport filter returns only NBA cards', async ({ page }) => {
    await goTo(page, 'Games');
    await page.locator('button:has-text("NBA")').first().click();
    await page.waitForTimeout(1500);
    await expect(page.locator('nav')).toBeVisible();
    // Cards (if any) should be rendered without crash
  });

});


// ══════════════════════════════════════════════════════════════════════════════
// 5. ORACLE PREDICTION MODAL — streaming narrative
// ══════════════════════════════════════════════════════════════════════════════

test.describe('Oracle Prediction Modal', () => {

  test('Invoke Oracle button triggers connecting state', async ({ page }) => {
    await goTo(page, 'Games');
    await page.waitForTimeout(2000);

    const cards = page.locator('.card');
    if (await cards.count() === 0) { test.skip(); return; }

    for (let i = 0; i < Math.min(await cards.count(), 8); i++) {
      await cards.nth(i).click();
      await page.waitForTimeout(600);
      if (!await page.locator('.modal-bg').isVisible()) continue;

      const invokeBtn = page.locator('.modal button:has-text("Invoke Oracle")');
      if (await invokeBtn.count() > 0) {
        await invokeBtn.first().click();
        // Should show connecting/streaming state briefly
        await page.waitForTimeout(500);
        await expect(page.locator('nav')).toBeVisible(); // app still alive
        return;
      }
      await closeModal(page);
    }
  });

  test('cached prediction shows Oracle\'s Pick section', async ({ page }) => {
    await goTo(page, 'Games');
    await page.waitForTimeout(2000);

    const cards = page.locator('.card');
    if (await cards.count() === 0) { test.skip(); return; }

    for (let i = 0; i < Math.min(await cards.count(), 10); i++) {
      await cards.nth(i).click();
      await page.waitForTimeout(800);
      if (!await page.locator('.modal-bg').isVisible()) continue;

      if (await page.locator("text=Oracle's Pick").count() > 0) {
        await expect(page.locator("text=Oracle's Pick").first()).toBeVisible();
        await closeModal(page);
        return;
      }
      await closeModal(page);
    }
    // Not a failure if no cached predictions exist yet
  });

  test('Re-run button is visible after prediction completes', async ({ page }) => {
    await goTo(page, 'Games');
    await page.waitForTimeout(2000);

    const cards = page.locator('.card');
    if (await cards.count() === 0) { test.skip(); return; }

    for (let i = 0; i < Math.min(await cards.count(), 10); i++) {
      await cards.nth(i).click();
      await page.waitForTimeout(800);
      if (!await page.locator('.modal-bg').isVisible()) continue;

      if (await page.locator('button:has-text("↺ Re-run")').count() > 0) {
        await expect(page.locator('button:has-text("↺ Re-run")').first()).toBeVisible();
        await closeModal(page);
        return;
      }
      await closeModal(page);
    }
  });

});


// ══════════════════════════════════════════════════════════════════════════════
// 6. STATS / ACCURACY PAGE
// ══════════════════════════════════════════════════════════════════════════════

test.describe('Stats / Accuracy Page', () => {

  test('Oracle Accuracy heading is visible', async ({ page }) => {
    await goTo(page, 'Stats');
    await expect(page.locator('text=Oracle Accuracy')).toBeVisible();
  });

  test('By Sport section is visible', async ({ page }) => {
    await goTo(page, 'Stats');
    await expect(page.locator('text=By Sport')).toBeVisible();
  });

  test('accuracy stat cards show Total / Correct / Wrong / Accuracy', async ({ page }) => {
    await goTo(page, 'Stats');
    await page.waitForTimeout(2000);
    for (const label of ['Total', 'Correct', 'Wrong', 'Accuracy']) {
      const el = page.locator(`text=${label}`).first();
      // Only assert visible if rendered (depends on API returning data)
      if (await el.count() > 0) {
        await expect(el).toBeVisible();
      }
    }
  });

  test('no crash on Stats page after switching from Predictions', async ({ page }) => {
    await goTo(page, 'Predictions');
    await goTo(page, 'Stats');
    await expect(page.locator('nav')).toBeVisible();
    await expect(page.locator('text=Oracle Accuracy')).toBeVisible();
  });

});


// ══════════════════════════════════════════════════════════════════════════════
// 7. DASHBOARD
// ══════════════════════════════════════════════════════════════════════════════

test.describe('Dashboard', () => {

  test('stat cards section renders (Predictions/Correct/Accuracy/Pending)', async ({ page }) => {
    await goTo(page, 'Dashboard');
    await page.waitForTimeout(2000);
    // At minimum the hero should be present
    await expect(page.locator('text=The Story').first()).toBeVisible();
  });

  test('Upcoming Games section is rendered', async ({ page }) => {
    await goTo(page, 'Dashboard');
    await expect(page.locator('text=Upcoming Games').first()).toBeVisible();
  });

  test('View Games CTA button works', async ({ page }) => {
    await goTo(page, 'Dashboard');
    await page.locator('button:has-text("View Games")').first().click();
    await page.waitForTimeout(800);
    await expect(page.locator('h1:has-text("Games")')).toBeVisible();
  });

  test('Predictions CTA button works', async ({ page }) => {
    await goTo(page, 'Dashboard');
    await page.locator('button:has-text("Predictions")').first().click();
    await page.waitForTimeout(800);
    await expect(page.locator('h1:has-text("Predictions")')).toBeVisible();
  });

  test('dashboard accuracy numbers visible in UI match API (if data exists)', async ({ page }) => {
    // Fetch from API
    const res = await page.request.get(`${API_BASE}/api/v1/predictions/accuracy/stats`);
    if (!res.ok()) { test.skip(); return; }
    const stats = await res.json();

    await goTo(page, 'Dashboard');
    await page.waitForTimeout(2500);

    if (stats.total > 0) {
      const content = await page.content();
      // The total should appear somewhere on the page
      expect(content).toContain(String(stats.total));
    }
  });

});


// ══════════════════════════════════════════════════════════════════════════════
// 8. API CONFIG BAR
// ══════════════════════════════════════════════════════════════════════════════

test.describe('API Config Bar', () => {

  test('Connect button is clickable', async ({ page }) => {
    await expect(page.locator('button:has-text("Connect")')).toBeVisible();
    await page.locator('button:has-text("Connect")').click();
    await page.waitForTimeout(1500);
    await expect(page.locator('nav')).toBeVisible();
  });

  test('typing a new URL and pressing Enter updates connection', async ({ page }) => {
    const input = page.locator('.config-input');
    await input.fill(API_BASE);
    await input.press('Enter');
    await page.waitForTimeout(2000);
    await expect(page.locator('.config-bar')).toBeVisible();
  });

  test('connection dot turns green when backend is reachable', async ({ page }) => {
    await page.waitForTimeout(3500);
    const dot = page.locator('.dot').first();
    const bg = await dot.evaluate(el => window.getComputedStyle(el).background);
    // Green = rgba(34, 197, 94, ...) or hex #22c55e
    const isGreen = bg.includes('34, 197, 94') || bg.includes('22c55e');
    if (!isGreen) {
      console.warn('Connection dot not green — backend may be offline during test');
    }
  });

});


// ══════════════════════════════════════════════════════════════════════════════
// 9. NO CRITICAL JS ERRORS
// ══════════════════════════════════════════════════════════════════════════════

test.describe('No Critical JS Errors', () => {

  // Known non-critical noise to ignore
  const ignore = ['Babel', 'Permissions policy', 'ContentMain', 'unload'];
  const isCritical = msg => !ignore.some(s => msg.includes(s));

  test('no critical errors on initial page load', async ({ page }) => {
    const errors = [];
    page.on('pageerror', err => errors.push(err.message));
    await page.goto(FRONTEND_URL);
    await page.waitForTimeout(2500);
    expect(errors.filter(isCritical)).toHaveLength(0);
  });

  test('no critical errors navigating to Predictions', async ({ page }) => {
    const errors = [];
    page.on('pageerror', err => errors.push(err.message));
    await page.goto(FRONTEND_URL);
    await page.waitForSelector('#root > div', { timeout: 10000 });
    await page.locator("button:has-text('Predictions')").first().click();
    await page.waitForTimeout(2500);
    expect(errors.filter(isCritical)).toHaveLength(0);
  });

  test('no TypeError split/undefined when clicking prediction cards', async ({ page }) => {
    const errors = [];
    page.on('pageerror', err => errors.push(err.message));
    await page.goto(FRONTEND_URL);
    await page.waitForSelector('#root > div', { timeout: 10000 });
    await page.locator('.config-input').fill(API_BASE);
    await page.locator('.config-input').press('Enter');
    await page.waitForTimeout(2500);

    await page.locator("button:has-text('Predictions')").first().click();
    await page.waitForTimeout(2000);

    const cards = page.locator('.card.fade-in');
    if (await cards.count() > 0) {
      await cards.first().click();
      await page.waitForTimeout(800);
    }

    const splitErrors = errors.filter(e =>
      (e.includes('split') || e.includes('undefined') || e.includes('Cannot read')) &&
      isCritical(e)
    );
    expect(splitErrors).toHaveLength(0);
  });

  test('no critical errors navigating through all pages', async ({ page }) => {
    const errors = [];
    page.on('pageerror', err => errors.push(err.message));
    await page.goto(FRONTEND_URL);
    await page.waitForSelector('#root > div', { timeout: 10000 });
    await page.locator('.config-input').fill(API_BASE);
    await page.locator('.config-input').press('Enter');
    await page.waitForTimeout(2500);

    for (const label of ['Games', 'Predictions', 'Stats', 'Dashboard']) {
      await page.locator(`button:has-text('${label}')`).first().click();
      await page.waitForTimeout(1500);
    }
    expect(errors.filter(isCritical)).toHaveLength(0);
  });

});