// Offline browser coverage: every API request is intercepted.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const ROOT = process.env.FRONTEND_TEST_URL || 'http://127.0.0.1:5176';
const id = n => n.toString(16).padStart(24, '0'), date = '2026-01-01T00:00:00Z';
const deferred = () => { let resolve; const promise = new Promise(r => resolve = r); return { promise, resolve }; };
(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  let passed = 0;
  async function scenario(name, run, width = 1440) {
    const context = await browser.newContext({ viewport: { width, height: 1000 } });
    const state = { calls: [], status: 200, gate: null, started: deferred(), finished: deferred(), snapshot: { operation_state: 'claimed_unknown', document_state: 'uploaded', attention: 'outcome_uncertain', recommended_action: 'refresh' }, docs: ['uploaded', 'processing', 'processed', 'processed', 'failed'].map((status, i) => ({
      id: id(101 + i), filename: `Reference ${i}.pdf`, status, indexed: i === 3, failed: i === 4,
      file_size: 100, created_at: date, updated_at: date,
    })) };
    await context.addInitScript(() => { if (location.protocol === 'http:') localStorage.setItem('deepdocs_access_token', 'offline.processing.token'); });
    await context.route('**/api/**', async route => {
      const req = route.request(), url = new URL(req.url()), path = url.pathname;
      const headers = { 'access-control-allow-origin': '*' };
      if (req.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: { ...headers, 'access-control-allow-headers': '*', 'access-control-allow-methods': '*' } });
      state.calls.push({ path, method: req.method(), query: url.search });
      if (path === '/api/auth/me') return route.fulfill({ json: { id: 'user', name: 'Example', email: 'test@example.com', created_at: date }, headers });
      if (path === '/api/knowledge-bases') return route.fulfill({ json: [1, 2].map(n => ({ id: id(n), name: 'Collection ' + n, created_at: date, updated_at: date })), headers });
      if (path.endsWith('/browse')) return route.fulfill({ json: { items: path.includes(id(2)) ? [{ ...state.docs[0], id: id(201), filename: 'Other collection.pdf' }] : state.docs, page: Number(url.searchParams.get('page') || 1), limit: 20, total: 25, total_pages: 2 }, headers });
      if (path.endsWith('/operation-status')) return route.fulfill({ json: { document_id: path.split('/')[3], ...state.snapshot }, headers });
      if (path.endsWith('/process')) {
        assert.equal(req.method(), 'POST'); assert.equal(req.postData(), null);
        assert.equal(req.headers().authorization, 'Bearer offline.processing.token');
        state.started.resolve();
        if (state.gate) await state.gate.promise;
        if (state.status === 'network') return route.abort('timedout');
        if (state.status === 200) state.docs[0] = { ...state.docs[0], status: 'processed' };
        try { return await route.fulfill({ status: state.status, json: state.status === 200 ? state.docs[0] : { detail: 'PRIVATE raw exception' }, headers }); }
        finally { state.finished.resolve(); }
      }
      throw new Error('Unexpected API ' + req.method() + ' ' + path);
    });
    const page = await context.newPage(), errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const row = filename => page.locator('.document-card').filter({ has: page.getByRole('heading', { name: filename, exact: true }) });
    // Hidden menu items are checked for eligibility; activation always opens the real menu first.
    const processButton = () => row('Reference 0.pdf').getByRole('menuitem', { name: 'Process PDF', exact: true, includeHidden: true });
    const openActions = async () => { const trigger = page.getByRole('button', { name: 'Actions for Reference 0.pdf', exact: true }); if (await trigger.getAttribute('aria-expanded') !== 'true') { await trigger.scrollIntoViewIfNeeded(); await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))); await trigger.click(); } };
    const inspect = async () => { await openActions(); await page.getByRole('menuitem', { name: 'Inspect status', exact: true }).click(); };
    const calls = () => state.calls.filter(c => c.path.endsWith('/process'));
    try {
      await page.goto(ROOT + '/documents?kb=' + id(1) + '&search=Reference&status=all&sort=filename&order=asc&page=2');
      await openActions();await processButton().waitFor();
      await run({ page, state, processButton, calls, openActions, inspect, row });
      assert.deepEqual(errors, []); assert.ok(!(await page.locator('body').innerText()).includes('PRIVATE'));
      assert.equal(state.calls.filter(c => /embeddings|bulk-reindex/.test(c.path)).length, 0);
      console.log('PASS ' + name); passed++;
    } finally { state.gate?.resolve(); await context.close(); }
  }
  try {
    await scenario('Uploaded only; one authenticated bodyless POST, pending locks, HTTP 200 refresh preserves query', async ({ page, state, processButton, calls, openActions, inspect, row }) => {
      assert.equal(await page.getByRole('menuitem', { name: 'Process PDF', exact: true, includeHidden: true }).count(), 1);
      const url = page.url(), before = state.calls.filter(c => c.path.endsWith('/browse')).length;
      await page.getByRole('checkbox', { name: 'Select Reference 0.pdf', exact: true }).check();
      for (const name of ['Delete selected', 'Re-index selected']) assert.equal(await page.getByRole('button', { name, exact: true }).isEnabled(), true);
      state.gate = deferred();
      await openActions(); await processButton().evaluate(button => { button.click(); button.click(); });
      await state.started.promise;
      await page.getByText('Processing…', { exact: true }).waitFor();
      assert.equal(calls().length, 1);
      for (const name of ['Actions for Reference 0.pdf', 'Refresh documents']) assert.equal(await page.getByRole('button', { name, exact: true }).isDisabled(), true);
      for (const name of ['Process PDF', 'Inspect status', 'Delete']) assert.equal(await row('Reference 0.pdf').getByRole('menuitem', { name, exact: true, includeHidden: true }).isDisabled(), true);
      assert.equal(await page.getByLabel('Knowledge Base', { exact: true }).isDisabled(), true);
      assert.equal(await page.getByRole('checkbox', { name: 'Select current page', exact: true }).isDisabled(), true);
      for (const name of ['Delete selected', 'Re-index selected']) {
        const button = page.getByRole('button', { name, exact: true });
        assert.equal(await button.isDisabled(), true); await button.evaluate(element => element.click());
      }
      assert.equal(state.calls.filter(c => /bulk-/.test(c.path)).length, 0);
      state.gate.resolve();
      await page.getByText('PDF processed. It has not been automatically indexed.', { exact: true }).waitFor();
      await page.waitForFunction(() => !document.querySelector('.document-list').getAttribute('aria-busy') || document.querySelector('.document-list').getAttribute('aria-busy') === 'false');
      assert.equal(page.url(), url); assert.equal(await processButton().count(), 0);
      const browses = state.calls.filter(c => c.path.endsWith('/browse'));
      assert.equal(browses.length, before + 1); assert.equal(browses.at(-1).query, browses[0].query);
    });
    for (const [status, text] of [[403, 'do not have access'], [404, 'no longer available'], [409, 'operation in progress'], [422, 'request is invalid'], [500, 'could not be confirmed'], [503, 'could not be confirmed'], ['network', 'could not be confirmed']]) {
      await scenario('sanitized error without replay: ' + status, async ({ page, state, processButton, calls, openActions, inspect, row }) => {
        state.status = status; await processButton().click();
        await page.getByRole('alert').filter({ hasText: text }).waitFor();
        assert.equal(await processButton().count(), 0); assert.equal(calls().length, 1);
        await page.getByRole('button', { name: 'Refresh documents', exact: true }).click();
        await page.getByRole('button', { name: 'Actions for Reference 0.pdf', exact: true }).waitFor();
        assert.equal(calls().length, 1);
      });
    }
    await scenario('401 follows existing logout', async ({ page, state, processButton, calls, openActions, inspect, row }) => {
      state.status = 401; await processButton().click(); await page.waitForURL('**/login**'); assert.equal(calls().length, 1);
    });
    await scenario('known uncertain inspection suppresses processing without granting recovery', async ({ page, processButton, calls, inspect }) => {
      await inspect();
      await page.getByText('Do not retry or delete this document until its state has been verified.', { exact: true }).waitFor();
      assert.equal(await processButton().count(), 0); assert.equal(calls().length, 0);
    });
    for (const document_state of ['processed', 'indexed', 'failed', 'processing', 'unknown']) await scenario('newer ' + document_state + ' inspection survives idle snapshot and stale list', async ({ page, state, processButton, calls, openActions, inspect, row }) => {
      state.docs.push({ ...state.docs[0], id: id(106), filename: 'Unrelated.pdf' });
      await page.getByRole('button', { name: 'Refresh documents', exact: true }).click();
      const unrelated = row('Unrelated.pdf').getByRole('menuitem', { name: 'Process PDF', exact: true, includeHidden: true });
      await unrelated.waitFor({ state: 'attached' });
      state.snapshot = { operation_state: 'idle', document_state, attention: document_state === 'indexed' ? 'none' : 'needs_reindex', recommended_action: document_state === 'indexed' ? 'none' : 'reindex' };
      await inspect();
      await page.getByText(/Inspection received at/).waitFor();
      assert.equal(await processButton().count(), 0); assert.equal(await unrelated.isEnabled(), true);
      state.snapshot = { operation_state: 'idle', document_state: 'uploaded', attention: 'needs_processing', recommended_action: 'process' };
      await page.getByRole('button', { name: 'Inspect again', exact: true }).click();
      await page.getByText('This document has not completed processing.', { exact: true }).waitFor();
      assert.equal(await processButton().count(), 0);
      await page.getByRole('button', { name: 'Close drawer', exact: true }).click();
      await page.waitForFunction(() => !document.querySelector('dialog:modal'));
      await page.getByRole('button', { name: 'Refresh documents', exact: true }).click();
      await page.waitForFunction(() => document.querySelector('.document-list')?.getAttribute('aria-busy') === 'false');
      assert.equal(await processButton().count(), 0); assert.equal(await unrelated.isEnabled(), true); assert.equal(calls().length, 0);
    });
    await scenario('normal Uploaded inspection preserves eligibility', async ({ page, state, processButton, inspect }) => {
      state.snapshot = { operation_state: 'idle', document_state: 'uploaded', attention: 'needs_processing', recommended_action: 'process' };
      await inspect();
      await page.getByText(/Inspection received at/).waitFor(); assert.equal(await processButton().isEnabled(), true);
    });
    for (const change of ['query', 'kb']) await scenario('pending navigation ' + change + ' aborts without replay or stale success', async ({ page, state, processButton, calls, openActions, inspect, row }) => {
      await page.evaluate(() => {
        const original = AbortController.prototype.abort;
        window.observedAborts = 0;
        AbortController.prototype.abort = function (...args) { window.observedAborts++; return original.apply(this, args); };
      });
      state.gate = deferred(); await processButton().click(); await state.started.promise;
      const failed = page.waitForEvent('requestfailed', request => request.url().endsWith('/process'));
      const target = change === 'kb' ? '/documents?kb=' + id(2) : '/documents?kb=' + id(1) + '&search=Changed&page=1';
      await page.evaluate(url => { history.pushState({}, '', url); dispatchEvent(new PopStateEvent('popstate')); }, target);
      await failed;
      await page.waitForFunction(() => document.querySelector('.document-list')?.getAttribute('aria-busy') === 'false');
      if (change === 'kb') await page.getByRole('heading', { name: 'Other collection.pdf', exact: true }).waitFor();
      const before = state.calls.filter(c => c.path.endsWith('/browse')).length;
      state.gate.resolve(); await state.finished.promise;
      assert.ok(await page.evaluate(() => window.observedAborts > 0));
      assert.equal(calls().length, 1); assert.equal(state.calls.filter(c => c.path.endsWith('/browse')).length, before);
      assert.equal(await page.getByText('PDF processed. It has not been automatically indexed.', { exact: true }).count(), 0);
      assert.equal(new URL(page.url()).search, new URL(target, ROOT).search);
      if (change === 'kb') assert.equal(await row('Other collection.pdf').getByRole('menuitem', { name: 'Process PDF', exact: true, includeHidden: true }).isEnabled(), true);
    });
    await scenario('unmount cancellation never replays', async ({ page, state, processButton, calls, openActions, inspect, row }) => {
      state.gate = deferred(); await processButton().click(); await state.started.promise;
      await page.goto('about:blank'); state.gate.resolve(); assert.equal(calls().length, 1);
    });
    await scenario('helper AbortSignal propagates cancellation without replay', async ({ page, state, calls }) => {
      state.gate = deferred();
      const result = page.evaluate(async documentId => {
        const { processDocument } = await import('/src/services/api.js');
        window.processingAbort = new AbortController();
        try { await processDocument(documentId, window.processingAbort.signal); return 'unexpected'; }
        catch (error) { return error.code; }
      }, id(101));
      await state.started.promise; await page.evaluate(() => window.processingAbort.abort());
      assert.equal(await result, 'ERR_CANCELED'); assert.equal(calls().length, 1);
    });
    await scenario('real client timeout gives uncertain feedback without replay', async ({ page, state, processButton, calls, openActions, inspect, row }) => {
      // Shorten only the browser transport's timeout in this test; leave the request pending.
      await page.evaluate(() => {
        const descriptor = Object.getOwnPropertyDescriptor(XMLHttpRequest.prototype, 'timeout');
        Object.defineProperty(XMLHttpRequest.prototype, 'timeout', { ...descriptor, set(value) { descriptor.set.call(this, value === 300000 ? 500 : value); } });
      });
      state.gate = deferred(); await processButton().click(); await state.started.promise;
      await page.getByRole('alert').filter({ hasText: 'could not be confirmed' }).waitFor(); assert.equal(calls().length, 1);
    });
    for (const width of [1440, 1024, 768, 600, 390]) await scenario('responsive controls ' + width, async ({ page }) => {
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    }, width);
    console.log(`${passed} processing scenarios passed`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
