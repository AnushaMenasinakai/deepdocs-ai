// Offline navigation-shell checks: all API traffic is synthetic; no screenshots.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const ROOT = process.env.FRONTEND_TEST_URL || 'http://127.0.0.1:5176';
const base = { id: 'a'.repeat(24), name: 'Reference library', description: 'Notes', created_at: '2026-01-01', updated_at: '2026-01-01' };
const destinations = [['Dashboard', '/'], ['Knowledge Bases', '/knowledge-bases'], ['Documents', '/documents'], ['Ask DeepDocs', '/ask']];
async function settled(page) {
  await page.locator('.sidebar').evaluate(async e => { await Promise.all(e.getAnimations().map(a => a.finished)); });
}
async function geometry(page) {
  await settled(page);
  const sidebar = await page.locator('.sidebar').boundingBox();
  const shell = await page.locator('.main-shell').boundingBox();
  const header = await page.locator('.topbar').boundingBox();
  assert.ok(Math.abs(shell.x - (sidebar.x + sidebar.width)) < 1);
  assert.equal(header.x, shell.x); assert.equal(header.width, shell.width);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  return sidebar.width;
}
(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  let passed = 0;
  async function scenario(name, run, width = 1440) {
    const context = await browser.newContext({ viewport: { width, height: 1000 } });
    const errors = [], calls = [];
    await context.addInitScript(() => localStorage.setItem('deepdocs_access_token', 'offline.navigation.token'));
    await context.route('**/api/**', async route => {
      const request = route.request(), path = new URL(request.url()).pathname;
      const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': '*' };
      if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
      calls.push(path);
      const reply = json => route.fulfill({ json, headers });
      if (path === '/api/auth/me') return reply({ id: 'user', name: 'Example User', email: 'test@example.com', created_at: '2026-01-01' });
      if (path.startsWith('/api/health')) return reply({ status: 'ok' });
      if (path === '/api/dashboard/summary') return reply({ knowledge_base_count: 1, document_count: 0, indexed_document_count: 0, processing_document_count: 0, failed_document_count: 0, ask_history_count: 0, recent_knowledge_bases: [{ ...base, document_count: 0 }] });
      if (path === '/api/knowledge-bases') return reply([base]);
      if (path.endsWith('/browse')) return reply({ items: path.includes('/documents/') ? [] : [base], page: 1, limit: 20, total: path.includes('/documents/') ? 0 : 1, total_pages: path.includes('/documents/') ? 0 : 1 });
      if (path.endsWith('/ask-history')) return reply([]);
      errors.push('Unexpected API: ' + path); return route.fulfill({ status: 500, headers, json: {} });
    });
    const page = await context.newPage(); page.on('pageerror', e => errors.push(e.message));
    try {
      await page.goto(ROOT + '/'); await page.locator('.dashboard-stat dd').first().waitFor();
      await run({ page, calls }); assert.deepEqual(errors, []); passed++; console.log('PASS ' + name);
    } finally { await context.close(); }
  }
  try {
    for (const width of [1440, 1024, 768]) await scenario('both states, all routes and alignment at ' + width, async ({ page }) => {
      const expanded = await geometry(page); assert.ok(expanded > 180);
      assert.equal(await page.getByRole('button', { name: 'Collapse sidebar' }).getAttribute('aria-expanded'), 'true');
      for (const collapsed of [false, true]) {
        if (collapsed) { await page.getByRole('button', { name: 'Collapse sidebar' }).click(); assert.equal(await geometry(page), 80); }
        const nav = page.getByRole('navigation', { name: 'Main navigation' });
        for (const [name, path] of destinations) {
          const link = nav.getByRole('link', { name, exact: true });
          assert.ok(await link.locator('svg').isVisible()); await link.click();
          await page.getByRole('heading', { name, exact: true, level: 1 }).waitFor();
          assert.equal(new URL(page.url()).pathname, path);
          assert.equal(await link.getAttribute('aria-current'), 'page');
          assert.ok((await link.getAttribute('class')).includes('active'));
          assert.equal(await nav.locator('[aria-current="page"]').count(), 1);
          await link.evaluate(async e => { await Promise.all(e.getAnimations().map(a => a.finished)); });
          assert.equal(await link.evaluate(e => getComputedStyle(e).backgroundColor), 'rgb(18, 58, 61)');
          await page.mouse.move(width - 5, 5); await page.locator('h1').click();
          assert.equal(await link.locator('.nav-text').isVisible(), !collapsed);
          assert.equal(await geometry(page), collapsed ? 80 : expanded);
        }
      }
      const url = page.url(); await page.getByRole('button', { name: 'Expand sidebar' }).click();
      assert.equal(page.url(), url); assert.equal(await geometry(page), expanded);
    }, width);
    await scenario('toggle preserves draft, URL, mounted page and request count', async ({ page, calls }) => {
      await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Ask DeepDocs', exact: true }).click();
      const input = page.getByLabel('Your question', { exact: true }); await input.fill('Keep this independent question draft');
      await page.getByRole('heading', { name: 'No previous questions yet', exact: true }).waitFor();
      const before = calls.length, url = page.url();
      await input.evaluate(e => { window.navigationDraftElement = e; });
      for (const name of ['Collapse sidebar', 'Expand sidebar']) {
        await page.getByRole('button', { name }).click(); await settled(page);
        assert.equal(await input.inputValue(), 'Keep this independent question draft'); assert.equal(page.url(), url);
        assert.ok(await input.evaluate(e => e === window.navigationDraftElement));
      }
      assert.equal(calls.length, before);
      await page.goto(ROOT + '/knowledge-bases?search=notes&sort=name&order=asc&page=1');
      await page.locator('.kb-card').waitFor(); const query = page.url();
      await page.getByRole('button', { name: 'Collapse sidebar' }).click(); await settled(page);
      assert.equal(page.url(), query); assert.equal(await page.getByLabel('Search Knowledge Bases').inputValue(), 'notes');
    });
    await scenario('keyboard toggle, focused tooltips and Escape dismissal', async ({ page }) => {
      const toggle = page.locator('.sidebar-toggle'); await toggle.focus(); await page.keyboard.press('Space'); await settled(page);
      assert.equal(await toggle.getAttribute('aria-expanded'), 'false');
      await page.keyboard.press('Tab'); const link = page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Dashboard', exact: true });
      assert.ok(await link.evaluate(e => e === document.activeElement)); assert.ok(await link.locator('.nav-text').isVisible());
      assert.equal(await link.evaluate(e => getComputedStyle(e).outlineStyle), 'solid');
      await page.keyboard.press('Escape'); assert.ok(!(await link.locator('.nav-text').isVisible()));
      await page.keyboard.press('Tab'); const next = page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Knowledge Bases', exact: true });
      assert.ok(await next.locator('.nav-text').isVisible()); await page.keyboard.press('Enter'); await page.locator('.kb-card').waitFor();
      await page.locator('h1').click(); await link.hover(); assert.ok(await link.locator('.nav-text').isVisible());
      const tip = await link.locator('.nav-text').boundingBox(); await page.mouse.move(tip.x + tip.width / 2, tip.y + tip.height / 2);
      assert.ok(await link.locator('.nav-text').isVisible());
    });
    await scenario('reduced motion removes width animation', async ({ page }) => {
      assert.ok((await page.locator('.sidebar').evaluate(e => getComputedStyle(e).transitionDuration)).includes('0.2s'));
      await page.emulateMedia({ reducedMotion: 'reduce' });
      assert.ok((await page.locator('.sidebar').evaluate(e => getComputedStyle(e).transitionDuration)).split(', ').every(v => parseFloat(v) === 0));
      await page.getByRole('button', { name: 'Collapse sidebar' }).click(); assert.equal(await geometry(page), 80);
    });
    await scenario('mobile ignores retained desktop collapse and restores it on resize', async ({ page }) => {
      await page.getByRole('button', { name: 'Collapse sidebar' }).click(); await settled(page);
      await page.setViewportSize({ width: 390, height: 1000 });
      assert.ok(!(await page.locator('.sidebar-toggle').isVisible()));
      assert.ok(!(await page.locator('.sidebar').isVisible()));
      await page.getByRole('button', { name: 'Open navigation menu' }).click();
      for (const [name] of destinations) assert.ok(await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name, exact: true }).isVisible());
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.setViewportSize({ width: 1024, height: 1000 }); assert.equal(await geometry(page), 80);
    });
    await scenario('mobile default uses closed drawer and preserves all routes', async ({ page }) => {
      assert.ok(!(await page.locator('.sidebar-toggle').isVisible()));
      assert.equal(await page.getByRole('navigation', { name: 'Main navigation' }).count(), 0);
      for (const [name] of destinations) {
        await page.getByRole('button', { name: 'Open navigation menu' }).click();
        const nav = page.getByRole('navigation', { name: 'Main navigation' });
        assert.equal(await nav.evaluate(e => getComputedStyle(e).gridTemplateColumns.split(' ').length), 1);
        await nav.getByRole('link', { name, exact: true }).click(); await page.getByRole('heading', { name, exact: true, level: 1 }).waitFor();
        await page.getByRole('dialog', { name: 'Mobile navigation' }).waitFor({ state: 'hidden' });
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      }
    }, 390);
    console.log(`Desktop navigation: ${passed} passed, 0 failed, 0 skipped.`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
