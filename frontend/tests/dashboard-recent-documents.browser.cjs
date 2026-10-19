// Real Dashboard/API helper with deterministic intercepted responses; no live services.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const ROOT = process.env.FRONTEND_TEST_URL || 'http://127.0.0.1:5176';
const id = n => n.toString(16).padStart(24, '0');
const RECENT = '/api/dashboard/recent-documents', SUMMARY = '/api/dashboard/summary';
const date = '2026-10-18T12:00:00Z';
const base = { id: id(1), name: 'Research', description: '', created_at: date, updated_at: date };
const summary = { knowledge_base_count: 1, document_count: 8, indexed_document_count: 3, processing_document_count: 1, failed_document_count: 1, ask_history_count: 4, recent_knowledge_bases: [{ ...base, document_count: 8 }] };
const document = n => ({ id: id(n), knowledge_base_id: base.id, knowledge_base_name: base.name, filename: 'Reference '+n+'.pdf', created_at: date, status: 'uploaded', indexed: false, failed: false });
const deferred = () => { let resolve; const promise = new Promise(r => resolve = r); return { promise, resolve }; };

(async () => {
 const browser = await chromium.launch({ channel: 'msedge', headless: true }); let passed = 0;
 async function test(name, run, width = 1440) {
  const context = await browser.newContext({ viewport: { width, height: 1000 } });
  const state = { documents: [document(103), document(102), document(101)], status: 200, summaryStatus: 200, gate: null, summaryGate: null, network: false, started: deferred(), calls: [], errors: [] };
  await context.addInitScript(() => {
   localStorage.setItem('deepdocs_access_token', 'offline.recent.token');
   window.abortedRecent = 0;
   const open = XMLHttpRequest.prototype.open, abort = XMLHttpRequest.prototype.abort;
   XMLHttpRequest.prototype.open = function(method, url, ...args) { this.previewUrl = String(url); return open.call(this, method, url, ...args); };
   XMLHttpRequest.prototype.abort = function(...args) { if (this.previewUrl?.includes('/api/dashboard/recent-documents')) window.abortedRecent++; return abort.apply(this, args); };
  });
  await context.route('**/api/**', async route => {
   const req = route.request(), path = new URL(req.url()).pathname;
   const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': '*' };
   if (req.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
   state.calls.push(path); assert.equal(req.method(), 'GET'); assert.equal(req.headers().authorization, 'Bearer offline.recent.token');
   const reply = (json, status = 200) => route.fulfill({ json, status, headers });
   if (path === '/api/auth/me') return reply({ id: id(9), name: 'Example', email: 'example@example.com', created_at: date });
   if (path === SUMMARY) { if (state.summaryGate) await state.summaryGate.promise; return reply(state.summaryStatus === 200 ? summary : { detail: 'PRIVATE' }, state.summaryStatus); }
   if (path === RECENT) {
    const snapshot = structuredClone(state.documents), status = state.status, network = state.network, gate = state.gate;
    state.started.resolve(); if (gate) await gate.promise;
    if (network) return route.abort();
    return reply(status === 200 ? { documents: snapshot } : { detail: 'PRIVATE' }, status);
   }
   if (path === '/api/knowledge-bases') return reply([base]);
   if (path.endsWith('/documents/browse')) return reply({ items: [], page: 1, limit: 20, total: 0, total_pages: 0 });
   state.errors.push('Unexpected API '+path); return reply({}, 500);
  });
  const page = await context.newPage(); page.on('pageerror', e => state.errors.push(e.message));
  const section = () => page.getByRole('region', { name: 'Recent Documents', exact: true });
  const go = () => page.goto(ROOT+'/');
  const ready = async () => { await go(); await section().locator('li').first().waitFor(); await page.locator('.dashboard-stat dd').first().waitFor(); };
  try { await run({ page, state, go, ready, section }); assert.deepEqual(state.errors, []); passed++; console.log('PASS '+name); }
  finally { state.gate?.resolve(); state.summaryGate?.resolve(); await context.close(); }
 }
 try {
  await test('actual data, server ordering, metadata, placement and navigation only', async ({page,state,ready,section}) => {
   state.documents[0].filename = '<img src=x onerror=alert(1)>.pdf';
   state.documents[1].knowledge_base_name = null;
   await ready();
   assert.deepEqual(await section().locator('h3').allTextContents(), state.documents.map(d=>d.filename));
   assert.equal(await section().locator('li').count(),3); assert.equal(await section().locator('img,button').count(),0);
   assert.ok((await section().locator('li').first().textContent()).includes('Research'));
   assert.ok(!(await section().locator('li').nth(1).textContent()).includes('Research'));
   const formatted=await page.evaluate(value=>new Date(value).toLocaleDateString(undefined,{year:'numeric',month:'short',day:'numeric'}),date);
   assert.equal(await section().locator('time').first().textContent(),formatted);
   assert.equal(await section().locator('time').first().getAttribute('datetime'),date);
   assert.ok((await section().locator('li').first().textContent()).includes('Uploaded '+formatted));
   const y=await section().boundingBox(),note=await page.locator('.dashboard-count-note').boundingBox(),kb=await page.locator('.dashboard-recent').boundingBox();
   assert.ok(y.y>note.y&&y.y<kb.y);
   assert.deepEqual(await page.locator('.dashboard-stat dd').allTextContents(),['1','8','3','4']);
   assert.equal(await page.getByRole('navigation',{name:'Quick actions'}).getByRole('link').count(),3);
  });
  for(const count of [0,1,2,3]) await test('successful document count '+count,async({page,state,go,section})=>{
   state.documents=state.documents.slice(0,count);await go();
   if(count) await section().locator('li').first().waitFor(); else await section().getByText('No documents yet.',{exact:false}).waitFor();
   assert.equal(await section().locator('li').count(),count);
  });
  for(const [status,indexed,failed,label] of [['uploaded',false,false,'Uploaded'],['processing',false,false,'Processing'],['processed',false,false,'Processed'],['processed',true,false,'Indexed'],['failed',false,false,'Failed'],['processed',false,true,'Failed'],['processed',true,true,'Failed']]) await test('status '+status+' indexed='+indexed+' failed='+failed,async({state,ready,section})=>{
   state.documents=[{...document(101),status,indexed,failed}];await ready();assert.equal(await section().locator('.dashboard-document-status').textContent(),label);
  });
  await test('recent loading does not hide metrics',async({page,state,go,section})=>{
   state.gate=deferred();await go();await state.started.promise;await section().getByRole('status').waitFor();await page.locator('.dashboard-stat dd').first().waitFor();assert.equal(await section().locator('li').count(),0);state.gate.resolve();await section().locator('li').first().waitFor();
  });
  await test('summary loading does not hide recent success',async({page,state,go,section})=>{
   state.summaryGate=deferred();await go();await section().locator('li').first().waitFor();await page.getByRole('status',{name:'Loading workspace overview'}).waitFor();state.summaryGate.resolve();await page.locator('.dashboard-stat dd').first().waitFor();
  });
  for(const status of [403,500,503,'network']) await test('sanitized recent error isolated from metrics '+status,async({page,state,go,section})=>{
   if(status==='network')state.network=true;else state.status=status;
   await go();await section().getByRole('alert').waitFor();await page.locator('.dashboard-stat dd').first().waitFor();
   assert.ok(!(await section().textContent()).includes('PRIVATE'));assert.equal(await section().getByText('No documents yet.',{exact:false}).count(),0);
  });
  await test('summary error isolated from recent success',async({page,state,go,section})=>{state.summaryStatus=503;await go();await section().locator('li').first().waitFor();await page.getByRole('button',{name:'Retry overview'}).waitFor();assert.equal(await section().getByRole('alert').count(),0)});
  await test('401 uses existing logout',async({page,state,go})=>{state.status=401;await go();await page.waitForURL('**/login');assert.equal(await page.evaluate(()=>localStorage.getItem('deepdocs_access_token')),null)});
  for(const bad of ['too many','duplicate','id','base','date','name','flag','status']) await test('malformed response '+bad,async({state,go,section})=>{
   if(bad==='too many')state.documents.push(document(100));
   else if(bad==='duplicate')state.documents[1]=state.documents[0];
   else if(bad==='id')state.documents[0].id='bad';
   else if(bad==='base')state.documents[0].knowledge_base_id='bad';
   else if(bad==='date')state.documents[0].created_at='invalid';
   else if(bad==='name')state.documents[0].knowledge_base_name={private:'PRIVATE'};
   else if(bad==='flag')state.documents[0].indexed='true';
   else state.documents[0].status='ready';
   await go();await section().getByRole('alert').waitFor();assert.equal(await section().locator('li').count(),0);assert.ok(!(await section().textContent()).includes('PRIVATE'));
  });
  await test('refresh requests each resource once and updates actual data',async({page,state,ready,section})=>{
   await ready();const before=state.calls.filter(p=>p===RECENT).length,summaryBefore=state.calls.filter(p=>p===SUMMARY).length;
   state.documents=[{...document(110),filename:'Newest.pdf'}];await page.getByRole('button',{name:'Refresh overview'}).click();await section().getByRole('heading',{name:'Newest.pdf'}).waitFor();
   assert.equal(state.calls.filter(p=>p===RECENT).length,before+1);assert.equal(state.calls.filter(p=>p===SUMMARY).length,summaryBefore+1);
  });
  await test('new refresh aborts pending request and rejects late data',async({page,state,go,section})=>{
   const gate=deferred();state.gate=gate;await go();await state.started.promise;await page.locator('.dashboard-stat dd').first().waitFor();const before=await page.evaluate(()=>window.abortedRecent);
   state.gate=null;state.documents=[{...document(110),filename:'New refresh.pdf'}];await page.getByRole('button',{name:'Refresh overview'}).click();await section().getByRole('heading',{name:'New refresh.pdf'}).waitFor();
   assert.ok(await page.evaluate(n=>window.abortedRecent>n,before));gate.resolve();
   assert.deepEqual(await section().locator('h3').allTextContents(),['New refresh.pdf']);
  });
  await test('unmount aborts pending recent request without replay',async({page,state,go})=>{
   state.gate=deferred();await go();await state.started.promise;await page.locator('.dashboard-stat dd').first().waitFor();const before=await page.evaluate(()=>window.abortedRecent),calls=state.calls.filter(p=>p===RECENT).length;
   await page.getByRole('navigation',{name:'Quick actions'}).getByRole('link',{name:'View Documents'}).click();await page.getByRole('heading',{name:'Documents',exact:true,level:1}).waitFor();
   assert.ok(await page.evaluate(n=>window.abortedRecent>n,before));state.gate.resolve();assert.equal(state.calls.filter(p=>p===RECENT).length,calls);assert.equal(await page.locator('.dashboard-documents').count(),0);
  });
  await test('helper propagates AbortSignal and strips nonpublic fields',async({page,state,ready})=>{
   await ready();state.documents[0].private='PRIVATE';
   const data=await page.evaluate(async()=>{const {getRecentDocuments}=await import('/src/services/api.js');return getRecentDocuments(new AbortController().signal)});
   assert.ok(!JSON.stringify(data).includes('PRIVATE'));
   state.gate=deferred();state.started=deferred();const result=page.evaluate(async()=>{const {getRecentDocuments}=await import('/src/services/api.js');window.testAbort=new AbortController();try{await getRecentDocuments(window.testAbort.signal);return 'unexpected'}catch(error){return error.code}});
   await state.started.promise;await page.evaluate(()=>window.testAbort.abort());assert.equal(await result,'ERR_CANCELED');state.gate.resolve();
  });
  await test('keyboard navigation opens the existing Knowledge Base Documents view',async({page,ready,section})=>{
   await ready();const link=section().getByRole('link').first();assert.equal(await link.getAttribute('href'),'/documents?kb='+base.id);await link.focus();assert.ok(await link.evaluate(e=>parseFloat(getComputedStyle(e).outlineWidth)>0));await page.keyboard.press('Enter');await page.getByRole('heading',{name:'Documents',exact:true,level:1}).waitFor();assert.equal(new URL(page.url()).searchParams.get('kb'),base.id);
  });
  for(const width of [1440,1024,768,600,390]) await test('long filenames, contrast and responsive layout '+width,async({page,state,ready,section})=>{
   state.documents[0].filename='LongReference'.repeat(35)+'.pdf';await page.emulateMedia({reducedMotion:'reduce'});await ready();
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   const link=await section().getByRole('link').first().boundingBox();assert.ok(link.height>=44&&link.x>=0&&link.x+link.width<=width);
   assert.ok(await section().locator('h3').evaluateAll(es=>es.every(e=>e.scrollWidth<=e.clientWidth)));
   const ratios=await section().evaluate(e=>{const lum=color=>{const c=color.match(/[\d.]+/g).slice(0,3).map(Number).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4});return c[0]*.2126+c[1]*.7152+c[2]*.0722};return [...e.querySelectorAll('h3,.dashboard-document-info p,.dashboard-document-status')].map(node=>{let parent=node;while(getComputedStyle(parent).backgroundColor==='rgba(0, 0, 0, 0)')parent=parent.parentElement;const a=lum(getComputedStyle(node).color),b=lum(getComputedStyle(parent).backgroundColor);return (Math.max(a,b)+.05)/(Math.min(a,b)+.05)})});assert.ok(ratios.every(r=>r>=4.5));
  },width);
  console.log('Recent Documents: '+passed+' passed, 0 failed, 0 skipped.');
 } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1});
