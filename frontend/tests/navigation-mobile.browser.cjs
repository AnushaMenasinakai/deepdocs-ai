// Offline native-dialog navigation checks. All API requests are intercepted.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const ROOT = process.env.FRONTEND_TEST_URL || 'http://127.0.0.1:5176';
const base = { id: 'a'.repeat(24), name: 'Reference library', description: 'Notes', created_at: '2026-01-01', updated_at: '2026-01-01' };
const destinations = [['Dashboard', '/'], ['Knowledge Bases', '/knowledge-bases'], ['Documents', '/documents'], ['Ask DeepDocs', '/ask']];
async function settle(locator) { await locator.evaluate(async e => { await Promise.allSettled(e.getAnimations().map(a => a.finished)); }); }
async function overflow(page) { assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)); }
(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  let passed = 0;
  async function scenario(name, run, width = 390) {
    const context = await browser.newContext({ viewport: { width, height: 900 } }), calls = [], errors = [], state = { expired: false };
    await context.addInitScript(() => localStorage.setItem('deepdocs_access_token', 'offline.mobile.token'));
    await context.route('**/api/**', async route => {
      const request = route.request(), path = new URL(request.url()).pathname;
      const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': '*' };
      if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
      calls.push(path);
      const reply = json => route.fulfill({ json, headers });
      if (path === '/api/auth/me') return reply({ id: 'user', name: 'Example User', email: 'test@example.com', created_at: '2026-01-01' });
      if (path.startsWith('/api/health')) return reply({ status: 'ok' });
      if (path === '/api/dashboard/recent-documents') return reply({ documents: [] });
      if (path === '/api/dashboard/summary') return reply({ knowledge_base_count: 1, document_count: 0, indexed_document_count: 0, processing_document_count: 0, failed_document_count: 0, ask_history_count: 0, recent_knowledge_bases: [{ ...base, document_count: 0 }] });
      if (path === '/api/knowledge-bases') return reply([base]);
      if (path.endsWith('/browse')) {
        if (state.expired) return route.fulfill({ status: 401, headers, json: {} });
        return reply({ items: path.includes('/documents/') ? [] : [base], page: 1, limit: 20, total: path.includes('/documents/') ? 0 : 1, total_pages: path.includes('/documents/') ? 0 : 1 });
      }
      if (path.endsWith('/ask-history')) return reply([]);
      errors.push('Unexpected API: ' + path); return route.fulfill({ status: 500, headers, json: {} });
    });
    const page = await context.newPage(); page.on('pageerror', e => errors.push(e.message));
    const drawer = page.getByRole('dialog', { name: 'Mobile navigation' });
    const menu = page.getByRole('button', { name: 'Open navigation menu' });
    const open = async () => { await menu.click(); await drawer.waitFor(); await settle(drawer); };
    const closed = async () => { await drawer.waitFor({ state: 'hidden' }); assert.equal(await menu.getAttribute('aria-expanded'), 'false'); assert.equal(await page.evaluate(() => document.body.style.overflow), ''); };
    try {
      await page.goto(ROOT + '/'); await page.locator('.dashboard-stat dd').first().waitFor();
      await run({ page, calls, state, menu, drawer, open, closed }); assert.deepEqual(errors, []); passed++; console.log('PASS ' + name);
    } finally { await context.close(); }
  }
  try {
    for (const width of [390, 600]) {
      await scenario('closed header, open drawer, touch targets and backdrop at ' + width, async ({ page, menu, drawer, open, closed }) => {
        assert.equal(await drawer.count(), 0); assert.equal(await menu.getAttribute('aria-expanded'), 'false');
        assert.equal(await menu.getAttribute('aria-controls'), 'mobile-navigation');
        assert.equal(await page.getByRole('navigation', { name: 'Main navigation' }).count(), 0);
        assert.equal(await page.getByRole('button', { name: 'Log out' }).count(), 1);
        assert.ok(!(await page.locator('.sidebar').isVisible())); await overflow(page);
        await open(); assert.equal(await menu.getAttribute('aria-expanded'), 'true');
        assert.equal(await page.getByRole('navigation', { name: 'Main navigation' }).count(), 1);
        const box = await drawer.boundingBox(); assert.equal(box.x, 0); assert.ok(box.width >= 280 && box.width <= 300); assert.ok(box.width <= width);
        for (const link of await drawer.getByRole('link').all()) assert.ok(await link.isVisible());
        for (const link of await drawer.locator('.nav-link').all()) assert.ok((await link.boundingBox()).height >= 44);
        assert.equal(await drawer.evaluate(e => getComputedStyle(e, '::backdrop').backgroundColor), 'rgba(0, 0, 0, 0.6)');
        await overflow(page); await page.mouse.click(width - 10, 400); await closed();
        assert.ok(await menu.evaluate(e => e === document.activeElement));
      }, width);
      await scenario('all routes, active state and destination functionality at ' + width, async ({ page, drawer, open, closed }) => {
        for (const [name, path] of destinations) {
          await open(); await drawer.getByRole('link', { name, exact: true }).click(); await closed();
          await page.getByRole('heading', { name: name === 'Dashboard' ? 'Welcome back' : name, exact: true, level: 1 }).waitFor(); assert.equal(new URL(page.url()).pathname, path);
          await open(); const active = drawer.getByRole('link', { name, exact: true });
          assert.equal(await active.getAttribute('aria-current'), 'page');
          assert.equal(await drawer.locator('.nav-link.active').count(), 1);
          assert.equal(await active.evaluate(e => getComputedStyle(e).backgroundColor), 'rgb(18, 58, 61)');
          await drawer.getByRole('button', { name: 'Close navigation menu' }).click(); await closed(); await overflow(page);
        }
        await page.getByLabel('Your question', { exact: true }).fill('Independent draft');
        assert.ok(await page.getByRole('button', { name: 'Ask DeepDocs', exact: true }).isEnabled());
      }, width);
    }
    await scenario('keyboard open/close, focus trap, Escape and inert background', async ({ page, menu, drawer, closed }) => {
      await menu.focus(); await page.keyboard.press('Enter'); await drawer.waitFor(); await settle(drawer);
      const close = drawer.getByRole('button', { name: 'Close navigation menu' });
      assert.ok(await close.evaluate(e => e === document.activeElement));
      assert.equal(await close.evaluate(e => getComputedStyle(e).outlineStyle), 'solid');
      const links = drawer.getByRole('link'), first = links.first(), last = links.last();
      await last.focus(); await page.keyboard.press('Tab'); assert.ok(await first.evaluate(e => e === document.activeElement));
      await page.keyboard.press('Shift+Tab'); assert.ok(await last.evaluate(e => e === document.activeElement));
      await page.locator('.session-controls button').evaluate(e => e.focus());
      assert.ok(await drawer.evaluate(e => e.contains(document.activeElement)));
      const logoutBox = await page.locator('.session-controls button').boundingBox();
      assert.ok(await page.evaluate(({ x, y }) => !document.elementFromPoint(x, y)?.closest('.session-controls'), { x: logoutBox.x + 5, y: logoutBox.y + 5 }));
      assert.equal(await page.evaluate(() => document.body.style.overflow), 'hidden');
      await page.keyboard.press('Escape'); await closed(); assert.ok(await menu.evaluate(e => e === document.activeElement));
      await page.keyboard.press('Space'); await drawer.waitFor(); await settle(drawer);
      await close.click(); await closed(); assert.ok(await menu.evaluate(e => e === document.activeElement));
    });
    await scenario('opening and closing preserve URL, query, selected KB, draft and API count', async ({ page, calls, open, drawer, closed }) => {
      await page.goto(ROOT + '/documents?kb=' + base.id + '&search=notes&status=all&sort=filename&order=asc');
      await page.getByRole('heading', { name: 'No matching documents' }).waitFor();
      const url = page.url(), before = calls.length;
      await open(); await drawer.getByRole('button', { name: 'Close navigation menu' }).click(); await closed();
      assert.equal(page.url(), url); assert.equal(await page.getByLabel('Knowledge Base', { exact: true }).inputValue(), base.id); assert.equal(calls.length, before);
      await page.goto(ROOT + '/ask'); await page.getByRole('button', { name: 'History', exact: true }).click();
      await page.getByRole('heading', { name: 'No previous questions yet' }).waitFor();
      await page.getByRole('button', { name: 'Close drawer' }).click(); await page.waitForFunction(() => !document.querySelector('dialog:modal'));
      await page.getByLabel('Your question', { exact: true }).fill('Preserved draft'); const asks = calls.length;
      await open(); await page.keyboard.press('Escape'); await closed();
      assert.equal(await page.getByLabel('Your question', { exact: true }).inputValue(), 'Preserved draft'); assert.equal(calls.length, asks);
    });
    for (const width of [768, 1024, 1440]) await scenario('resize closes modal and preserves desktop collapse at ' + width, async ({ page, open, drawer }) => {
      await page.setViewportSize({ width, height: 900 });
      const collapse = page.getByRole('button', { name: 'Collapse sidebar' }); await collapse.click(); await settle(page.locator('.sidebar'));
      assert.equal((await page.locator('.sidebar').boundingBox()).width, 80);
      await page.setViewportSize({ width: 390, height: 900 }); await open();
      await page.setViewportSize({ width, height: 900 }); await drawer.waitFor({ state: 'hidden' }); await settle(page.locator('.sidebar'));
      assert.equal((await page.locator('.sidebar').boundingBox()).width, 80);
      assert.equal(await page.evaluate(() => document.body.style.overflow), '');
      assert.ok(await page.locator('.sidebar .nav-link.active').evaluate(e => e === document.activeElement));
      await page.getByRole('button', { name: 'Expand sidebar' }).click(); await settle(page.locator('.sidebar'));
      assert.ok((await page.locator('.sidebar').boundingBox()).width > 180); await overflow(page);
      await page.setViewportSize({ width: 600, height: 900 }); assert.equal(await drawer.count(), 0);
    });
    await scenario('slide/fade animations reverse and reduced motion disables both', async ({ page, open, drawer, closed }) => {
      await open();
      assert.equal(await drawer.evaluate(e => getComputedStyle(e).animationDuration), '0.24s');
      assert.equal(await drawer.evaluate(e => getComputedStyle(e, '::backdrop').animationDuration), '0.24s');
      await drawer.getByRole('button', { name: 'Close navigation menu' }).evaluate(e => e.click());
      await page.waitForFunction(() => document.querySelector('#mobile-navigation').classList.contains('drawer-closing'));
      assert.equal(await page.locator('#mobile-navigation').evaluate(e => getComputedStyle(e).animationName), 'drawer-exit');
      await closed(); await page.emulateMedia({ reducedMotion: 'reduce' }); await open();
      assert.equal(await drawer.evaluate(e => getComputedStyle(e).animationName), 'none');
      assert.equal(await drawer.evaluate(e => getComputedStyle(e, '::backdrop').animationName), 'none');
      await page.keyboard.press('Escape'); await closed();
    });
    await scenario('rapid close/reopen, route history change and unmount clean up modal state', async ({ page, state, open, drawer, closed }) => {
      for (let n = 0; n < 3; n++) { await open(); await page.keyboard.press('Escape'); await closed(); }
      await open(); await drawer.getByRole('link', { name: 'Documents', exact: true }).click(); await closed();
      await open(); await page.goBack(); await closed();
      await open(); state.expired = true;
      // A real client-side shell unmount, rather than a full browser reload.
      await drawer.getByRole('link', { name: 'Knowledge Bases', exact: true }).click();
      // Dashboard has the same heading; wait for the actual authentication redirect.
      await page.waitForURL('**/login**');
      await page.getByRole('heading', { name: 'Welcome back', exact: true }).waitFor();
      assert.equal(await page.locator('#mobile-navigation').count(), 0);
      assert.equal(await page.evaluate(() => document.body.style.overflow), '');
    });
    console.log(`Mobile navigation: ${passed} passed, 0 failed, 0 skipped.`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
