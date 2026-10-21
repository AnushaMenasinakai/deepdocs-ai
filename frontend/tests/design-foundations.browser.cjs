// Standalone, offline Edge checks. No screenshots or live backend requests.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const ROOT = process.env.FRONTEND_TEST_URL || 'http://127.0.0.1:5176';
const id = n => n.toString(16).padStart(24, '0'), date = '2026-01-01T00:00:00Z';
const base = { id: id(1), name: 'Research reference collection', description: 'Reference documents', created_at: date, updated_at: date };
const doc = { id: id(101), filename: 'Long-reference-document-'.repeat(5) + '.pdf', status: 'uploaded', indexed: false, failed: false, file_size: 100, created_at: date, updated_at: date };
const deferred = () => { let resolve; const promise = new Promise(r => resolve = r); return { promise, resolve }; };
const rgb = hex => 'rgb(' + hex.match(/\w\w/g).map(v => parseInt(v, 16)).join(', ') + ')';
function luminance(color) {
  const values = color.match(/[\d.]+/g).slice(0, 3).map(Number).map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4);
  return values.reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
}
async function styles(locator) {
  return locator.evaluate(async e => {
    // Measure the settled state, not a frame of the enabled/disabled transition.
    await Promise.all(e.getAnimations().filter(a => a.effect.getTiming().iterations !== Infinity).map(a => a.finished));
    const s = getComputedStyle(e);
    let background, current = e;
    while (current) {
      const value = getComputedStyle(current).backgroundColor;
      if (value !== 'rgba(0, 0, 0, 0)' && value !== 'transparent') { background = value; break; }
      current = current.parentElement;
    }
    return { color: s.color, background, ownBackground: s.backgroundColor, border: s.borderColor,
      radius: s.borderRadius, font: s.fontFamily, outline: s.outlineStyle, outlineWidth: s.outlineWidth,
      outlineColor: s.outlineColor, opacity: s.opacity, transition: s.transitionDuration };
  });
}
async function readable(locator, label) {
  const s = await styles(locator);
  assert.ok(s.background, label + ': opaque ancestor background');
  const [dark, light] = [luminance(s.color), luminance(s.background)].sort((a, b) => a - b);
  const ratio = (light + .05) / (dark + .05);
  assert.ok(ratio >= 4.5, label + ': actual contrast ' + ratio.toFixed(2));
  assert.equal(s.opacity, '1', label + ': text is not faded');
  return s;
}
async function noOverflow(page) {
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'No horizontal page overflow');
  for (const locator of [page.locator('h1'), page.locator('main input:visible').first(), page.locator('main textarea:visible').first()]) {
    if (await locator.count()) {
      const box = await locator.boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= page.viewportSize().width + 1, 'Content fits viewport');
    }
  }
}
(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  let passed = 0, failed = 0;
  async function scenario(name, run, { width = 1440, authenticated = true } = {}) {
    const context = await browser.newContext({ viewport: { width, height: 1000 } });
    const state = { askGate: null, summaryGate: null, calls: [], unexpected: [], errors: [] };
    await context.addInitScript(auth => { if (auth) localStorage.setItem('deepdocs_access_token', 'offline.design.token'); }, authenticated);
    await context.route('**/api/**', async route => {
      const req = route.request(), path = new URL(req.url()).pathname;
      const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': '*' };
      if (req.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
      state.calls.push(path);
      const reply = (json, status = 200) => route.fulfill({ json, status, headers });
      if (path === '/api/auth/me') return reply({ id: id(9), name: 'Example User', email: 'test@example.com', created_at: date });
      if (path === '/api/auth/register') return reply({}, 201);
      if (path === '/api/auth/login') return reply({ detail: 'Synthetic unavailable service' }, 503);
      if (path.startsWith('/api/health')) return reply({ status: 'ok' }, path.endsWith('/qdrant') ? 503 : 200);
      if (path === '/api/dashboard/recent-documents') return reply({ documents: [] });
      if (path === '/api/dashboard/summary') {
        if (state.summaryGate) await state.summaryGate.promise;
        return reply({ knowledge_base_count: 1, document_count: 1, indexed_document_count: 0, processing_document_count: 0, failed_document_count: 0, ask_history_count: 0, recent_knowledge_bases: [{ ...base, document_count: 1 }] });
      }
      if (path === '/api/knowledge-bases') return reply([base]);
      if (path.endsWith('/browse')) return reply({ items: path.includes('/documents/') ? [doc] : [base], page: 1, limit: 20, total: 1, total_pages: 1 });
      if (path.endsWith('/ask-history')) return reply([]);
      if (path.endsWith('/ask')) {
        if (state.askGate) await state.askGate.promise;
        return reply({ status: 'answered', answer: 'A grounded independent answer. ' + 'Reference '.repeat(35), retrieved_chunk_count: 1, sources: [{ document_id: doc.id, source_filename: doc.filename, page_start: 1, page_end: 2 }] });
      }
      if (path.endsWith('/operation-status')) return reply({ document_id: doc.id, operation_state: 'claimed_unknown', document_state: 'uploaded', attention: 'outcome_uncertain', recommended_action: 'refresh' });
      state.unexpected.push(path); return reply({}, 500);
    });
    const page = await context.newPage();
    page.on('pageerror', e => state.errors.push(e.message));
    const go = async path => { await page.goto(ROOT + path); await page.locator('h1').waitFor(); await page.evaluate(() => document.fonts.ready); };
    try {
      await run({ page, state, go });
      assert.deepEqual(state.unexpected, []); assert.deepEqual(state.errors, []);
      passed++; console.log('PASS ' + name);
    } catch (error) { failed++; console.error('FAIL ' + name + ': ' + error.stack); }
    finally { state.askGate?.resolve(); state.summaryGate?.resolve(); await context.close(); }
  }
  try {
    await scenario('approved tokens, applied surfaces, locally loaded Inter', async ({ page, go }) => {
      const fontResponse = page.waitForResponse(r => r.url().endsWith('/fonts/Inter-Variable.woff2'));
      await go('/'); await page.locator('.dashboard-stat dd').first().waitFor();
      assert.equal((await fontResponse).status(), 200);
      const font = await page.evaluate(() => ({ loaded: [...document.fonts].some(f => f.family === 'Inter' && f.status === 'loaded'), check: document.fonts.check('400 16px Inter'), rules: [...document.styleSheets].flatMap(s => [...s.cssRules]).filter(r => r.type === CSSRule.FONT_FACE_RULE).map(r => r.cssText) }));
      assert.ok(font.loaded && font.check); assert.ok(font.rules.some(r => r.includes('font-display: swap') && r.includes('/fonts/Inter-Variable.woff2')));
      for (const [token, hex] of Object.entries({ '--background': '#0B1220', '--surface': '#172235', '--accent': '#14B8A6', '--text-primary': '#E2E8F0' })) {
        assert.equal(await page.evaluate(t => getComputedStyle(document.documentElement).getPropertyValue(t).trim().toUpperCase(), token), hex);
      }
      assert.equal((await styles(page.locator('html'))).background, rgb('0B1220'));
      assert.equal((await readable(page.locator('.panel').first(), 'card')).background, rgb('172235'));
      assert.match((await styles(page.locator('body'))).font, /^Inter/);
      assert.equal((await styles(page.locator('.panel').first())).radius, '12px');
      await readable(page.locator('.kb-muted').first(), 'muted text');
    });
    await scenario('four button hierarchies, hover, disabled and keyboard focus', async ({ page, go }) => {
      await go('/knowledge-bases'); await page.locator('.kb-card').waitFor();
      const originalUrl = page.url();
      const primary = page.getByRole('button', { name: 'New Knowledge Base', exact: true });
      const neutral = page.locator('.session-controls .session-button');
      const primaryColor = (await readable(primary, 'primary')).background;
      const neutralColor = (await readable(neutral, 'neutral')).background;
      assert.equal(primaryColor, rgb('14B8A6'));
      await primary.hover();
      await page.waitForFunction(() => getComputedStyle(document.querySelector('.kb-toolbar .kb-primary')).backgroundColor === 'rgb(45, 212, 191)');
      await readable(primary, 'primary hover');

      const trigger = page.getByRole('button', { name: 'Actions for ' + base.name, exact: true });
      await trigger.focus(); await page.keyboard.press('Enter');
      await page.getByRole('menu').waitFor(); assert.equal(await trigger.getAttribute('aria-expanded'), 'true');
      const edit = page.getByRole('menuitem', { name: 'Edit', exact: true });
      const remove = page.getByRole('menuitem', { name: 'Delete', exact: true });
      assert.ok(await edit.isVisible()); assert.ok(await remove.isVisible());
      assert.ok(await edit.evaluate(e => e === document.activeElement));
      await readable(edit, 'focused Edit menu action');
      assert.equal(page.url(), originalUrl, 'Opening menu does not navigate the card');
      await page.keyboard.press('Enter');
      await page.getByRole('dialog', { name: 'Edit Knowledge Base', exact: true }).waitFor();
      assert.equal(await page.getByLabel('Name', { exact: true }).inputValue(), base.name);
      assert.equal(page.url(), originalUrl, 'Edit does not navigate the card');
      // Cancel retains the secondary hierarchy formerly sampled on inline Edit.
      const secondary = page.getByRole('button', { name: 'Cancel', exact: true });
      const secondaryColor = (await readable(secondary, 'secondary')).background;
      assert.equal(new Set([primaryColor, secondaryColor, neutralColor]).size, 3);
      await secondary.click(); await page.getByRole('dialog').waitFor({ state: 'hidden' });

      await trigger.focus(); await page.keyboard.press('Enter');
      await edit.waitFor(); await page.keyboard.press('ArrowDown');
      assert.ok(await remove.evaluate(e => e === document.activeElement));
      const focus = await styles(remove); assert.equal(focus.outline, 'solid'); assert.ok(parseFloat(focus.outlineWidth) >= 2);
      await readable(remove, 'focused destructive menu action');
      await page.keyboard.press('Enter');
      await page.getByRole('dialog', { name: 'Delete Knowledge Base?', exact: true }).waitFor();
      assert.equal(page.url(), originalUrl, 'Delete does not navigate the card');
      const danger = page.getByRole('button', { name: 'Confirm deletion', exact: true });
      assert.equal((await readable(danger, 'destructive')).background, rgb('B91C1C'));

      const gate = deferred(), entered = deferred(); let deletions = 0;
      await page.route('**/api/knowledge-bases/' + base.id, async route => {
        if (route.request().method() !== 'DELETE') return route.fallback();
        deletions++; entered.resolve(); await gate.promise;
        return route.fulfill({ status: 409, json: { detail: 'Synthetic conflict' }, headers: { 'access-control-allow-origin': '*' } });
      });
      try {
        await danger.click(); await entered.promise;
        const working = page.getByRole('button', { name: /^Working/ });
        for (const control of await page.getByRole('dialog').getByRole('button').all()) assert.ok(await control.isDisabled());
        assert.ok(await trigger.isDisabled());
        assert.equal((await readable(working, 'pending confirmation')).background, rgb('25364D'));
        await working.evaluate(e => e.click()); assert.equal(deletions, 1);
      } finally { gate.resolve(); }
      await page.getByRole('alert').filter({ hasText: 'Delete its documents before deleting the Knowledge Base' }).waitFor();
      await page.getByRole('button', { name: 'Cancel', exact: true }).click();
      await page.getByRole('dialog').waitFor({ state: 'hidden' });

      await go('/ask'); await page.locator('#ask-question').waitFor();
      const submit = page.getByRole('button', { name: 'Ask DeepDocs', exact: true });
      assert.ok(await submit.isDisabled());
      const disabled = await readable(submit, 'disabled button'); assert.equal(disabled.background, rgb('25364D'));
      await page.locator('#ask-question').fill('A question');
      await page.waitForFunction(() => getComputedStyle(document.querySelector('.ask-composer .kb-primary')).backgroundColor === 'rgb(20, 184, 166)');
      assert.notEqual((await styles(submit)).background, disabled.background);
    });
    await scenario('dark management inputs, select, textarea, checkbox and file control', async ({ page, go }) => {
      await go('/documents?kb=' + base.id); await page.locator('.document-card').waitFor();
      for (const selector of ['.management-search input', '.document-selector select', '.management-controls select', '.document-upload input', '.document-selector label']) {
        const s = await readable(page.locator(selector).first(), selector); assert.ok(luminance(s.background) < .1);
      }
      const checkbox = page.getByRole('checkbox', { name: 'Select current page', exact: true });
      await checkbox.check(); assert.ok(await checkbox.isChecked());
      assert.equal(await checkbox.evaluate(e => getComputedStyle(e).accentColor), rgb('14B8A6'));
      await checkbox.focus(); await page.keyboard.press('Space'); assert.ok(!(await checkbox.isChecked()));
      await go('/ask'); await page.locator('#ask-question').waitFor();
      await readable(page.locator('#ask-question'), 'question input');
      const placeholder = await page.locator('#ask-question').evaluate(e => getComputedStyle(e, '::placeholder').color);
      assert.equal(placeholder, rgb('A6B5C9'));
    });
    await scenario('validation, error and success messages on actual authentication forms', async ({ page, go }) => {
      await go('/register'); await page.getByRole('button', { name: 'Create account', exact: true }).click();
      await readable(page.locator('.auth-field-error').first(), 'field error');
      await readable(page.locator('.auth-validation-summary'), 'validation summary');
      assert.equal((await styles(page.locator('#name'))).border, rgb('B56573'));
      await page.getByLabel('Name', { exact: true }).fill('Example');
      await page.getByLabel('Email', { exact: true }).fill('test@example.com');
      await page.getByLabel('Password', { exact: true }).fill('test-password');
      await page.getByLabel('Confirm password', { exact: true }).fill('test-password');
      await page.getByRole('button', { name: 'Create account', exact: true }).click();
      await page.locator('.auth-success').waitFor(); await readable(page.locator('.auth-success'), 'success notice');
      await page.getByLabel('Email', { exact: true }).fill('test@example.com');
      await page.getByLabel('Password', { exact: true }).fill('test-password');
      await page.getByRole('button', { name: 'Sign in', exact: true }).click();
      await page.locator('.auth-error').waitFor(); await readable(page.locator('.auth-error'), 'service error');
    }, { authenticated: false });
    await scenario('uncertainty and loading remain readable; reduced motion', async ({ page, state, go }) => {
      await page.emulateMedia({ reducedMotion: 'reduce' });
      state.summaryGate = deferred(); await go('/');
      await page.locator('.dashboard-skeleton').first().waitFor();
      assert.equal(await page.locator('.dashboard-skeleton span').first().evaluate(e => getComputedStyle(e).animationName), 'none');
      state.summaryGate.resolve(); await page.locator('.dashboard-stat dd').first().waitFor();
      assert.equal(await page.locator('.dashboard-health').count(), 0);
      assert.ok(!state.calls.some(path => path.startsWith('/api/health')));
      await go('/documents?kb=' + base.id); await page.locator('.document-card').waitFor();
      await page.getByRole('button', { name: 'Inspect status for ' + doc.filename, exact: true }).click();
      const uncertain = page.getByText('Do not retry or delete this document until its state has been verified.', { exact: true });
      await uncertain.waitFor(); await readable(uncertain, 'uncertain operation notice');
      await go('/ask'); await page.locator('#ask-question').waitFor(); state.askGate = deferred();
      await page.locator('#ask-question').fill('A question'); await page.getByRole('button', { name: 'Ask DeepDocs', exact: true }).click();
      await page.locator('.ask-loading').waitFor(); await readable(page.locator('.ask-loading p'), 'loading message');
      assert.equal(await page.locator('.ask-loading-dots span').first().evaluate(e => getComputedStyle(e).animationName), 'none');
      assert.ok((await styles(page.locator('.ask-composer button'))).transition.split(', ').every(v => parseFloat(v) === 0));
      state.askGate.resolve(); await page.locator('.answer-text').waitFor();
    });
    for (const width of [1440, 768, 390]) {
      for (const path of ['/', '/knowledge-bases', '/documents?kb=' + base.id, '/ask', '/login', '/register']) {
        const authenticated = !['/login', '/register'].includes(path);
        await scenario('responsive theme ' + width + ' ' + path.split('?')[0], async ({ page, go }) => {
          await go(path);
          const ready = path === '/' ? '.dashboard-stat dd' : path === '/knowledge-bases' ? '.kb-card' : path.startsWith('/documents') ? '.document-card' : path === '/ask' ? '#ask-question' : '.auth-field input';
          await page.locator(ready).first().waitFor();
          await noOverflow(page); await readable(page.locator('h1'), 'page heading');
          if (authenticated) {
            if (width <= 600) await page.getByRole('button', { name: 'Open navigation menu' }).click();
            await readable(page.locator('.nav-link.active:visible'), 'active navigation');
            if (width <= 600) {
              await page.getByRole('button', { name: 'Close navigation menu' }).click();
              await page.getByRole('dialog', { name: 'Mobile navigation' }).waitFor({ state: 'hidden' });
            }
            await readable(page.locator('.panel').first(), 'page card');
            const name = await page.locator('.session-name').evaluate(e => ({ client: e.clientWidth, scroll: e.scrollWidth, overflow: getComputedStyle(e).textOverflow }));
            if (name.scroll > name.client) assert.equal(name.overflow, 'ellipsis');
          } else await readable(page.locator('.auth-field input').first(), 'auth input');
          if (path === '/ask') {
            await page.locator('#ask-question').fill('A question'); await page.getByRole('button', { name: 'Ask DeepDocs', exact: true }).click();
            await page.locator('.source-card').waitFor(); await readable(page.locator('.answer-text'), 'answer'); await readable(page.locator('.source-filename'), 'source'); await noOverflow(page);
          }
          if (path.startsWith('/documents')) {
            await page.getByRole('checkbox', { name: 'Select current page', exact: true }).check();
            await page.getByRole('button', { name: 'Delete selected', exact: true }).click();
            await page.getByRole('button', { name: 'Confirm bulk deletion', exact: true }).waitFor(); await noOverflow(page);
          }
        }, { width, authenticated });
      }
    }
  } finally { await browser.close(); }
  console.log(`Design foundations: ${passed} passed, ${failed} failed, 0 skipped.`);
  if (failed) process.exitCode = 1;
})().catch(error => { console.error(error); process.exitCode = 1; });
