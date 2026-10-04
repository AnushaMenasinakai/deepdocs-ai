// Offline citation rendering/contracts. No live backend or provider calls.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const baseURL = process.env.FRONTEND_TEST_URL || 'http://127.0.0.1:5176';
const source = (id, start=id, end=start) => ({citation_id:id,document_id:'a'.repeat(24),source_filename:'guide.pdf',page_start:start,page_end:end});
const result = () => ({status:'answered',citation_version:1,answer:'JWT verifies tokens.\n\nStorage uses documents.',
  claims:[{text:'JWT verifies tokens.',citation_ids:[1]},{text:'Storage uses documents.',citation_ids:[1,2]}],
  retrieved_chunk_count:3,sources:[source(1),source(2,3,4)]});
const saved = (value, id='history-new') => ({...structuredClone(value),id,question:'Saved question '+id+'?',created_at:'2026-10-04T10:30:00Z'});
const legacy = () => ({status:'answered',answer:'Legacy answer [1] stays plain text.',retrieved_chunk_count:1,
  sources:[{document_id:'b'.repeat(24),source_filename:'old.pdf',page_start:2,page_end:2}]});
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true}); let passed=0;
 async function scenario(name,run,width=1440){
  const context=await browser.newContext({viewport:{width,height:1000}});
  const state={result:result(),history:[],delay:0,calls:0,status:200};
  await context.addInitScript(()=>localStorage.setItem('deepdocs_access_token','fake.test.token'));
  await context.route('**/api/**',async route=>{
   const req=route.request(),path=new URL(req.url()).pathname,headers={'access-control-allow-origin':'*'};
   if(req.method()==='OPTIONS')return route.fulfill({status:204,headers:{...headers,'access-control-allow-headers':'*','access-control-allow-methods':'GET, POST, DELETE, OPTIONS'}});
   if(path==='/api/auth/me')return route.fulfill({json:{id:'user',name:'Example',email:'user@example.com',created_at:'2026-01-01'},headers});
   assert.equal(req.headers().authorization,'Bearer fake.test.token');
   if(path==='/api/knowledge-bases')return route.fulfill({json:[{id:'base-a',name:'Research'},{id:'base-b',name:'Projects'}],headers});
   if(path.endsWith('/ask-history'))return route.fulfill({json:state.history,headers});
   if(path.includes('/ask-history/')&&req.method()==='DELETE'){state.history=state.history.filter(v=>v.id!==path.split('/').at(-1));return route.fulfill({status:204,headers})}
   if(path.endsWith('/ask')){
    assert.deepEqual(Object.keys(req.postDataJSON()),['question']); state.calls++;
    const data=structuredClone(state.result),status=state.status;
    if(state.delay)await new Promise(resolve=>setTimeout(resolve,state.delay));
    if(status===200)state.history=[saved(data),...state.history.filter(v=>v.id!=='history-new')];
    return route.fulfill({status,json:status===200?data:{detail:'PRIVATE'},headers});
   }
   throw Error('Unexpected test route '+path);
  });
  const page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('console',m=>{if(m.type()==='error'&&!m.text().startsWith('Failed to load resource:'))errors.push(m.text())});
  const ready=async()=>{await page.goto(baseURL+'/ask');await page.getByLabel('Your question',{exact:true}).waitFor()};
  const ask=async()=>{await page.getByLabel('Your question',{exact:true}).fill('Explain the documents');await page.getByRole('button',{name:'Ask DeepDocs',exact:true}).click()};
  try{await run({page,state,ready,ask});assert.deepEqual(errors,[]);passed++;console.log('PASS '+name)}finally{await context.close()}
 }
 try{
  await scenario('multiple claims, multi-page citations, repeated reference and keyboard focus',async({page,ready,ask})=>{
   await ready();await ask();await page.locator('.ask-answer .citation-marker').first().waitFor();
   const area=page.locator('.ask-answer');
   assert.equal(await area.locator('.citation-marker').count(),3);
   assert.deepEqual(await area.locator('.source-number').allTextContents(),['[1]','[2]']);
   await area.getByText('Page 1',{exact:true}).waitFor();await area.getByText('Pages 3–4',{exact:true}).waitFor();
   const marker=area.getByRole('button',{name:'Source 2: guide.pdf, Pages 3–4'});
   await marker.focus();await marker.press('Enter');
   const target=await marker.getAttribute('aria-controls');
   assert.equal(await page.evaluate(()=>document.activeElement.id),target);
   assert.equal(await area.locator('a').count(),0);
   assert.equal(await area.locator('.source-card').last().evaluate(el=>getComputedStyle(el).outlineStyle),'solid');
  });
  await scenario('one cited source keeps its original nonconsecutive number',async({page,state,ready,ask})=>{
   state.result.claims=[{text:'Only page three.',citation_ids:[2]}];state.result.answer='Only page three.';state.result.sources=[source(2,3)];
   await ready();await ask();await page.locator('.ask-answer .citation-marker').waitFor();
   assert.equal(await page.locator('.ask-answer .source-number').textContent(),'[2]');
  });
  await scenario('claim and filename HTML are inert text',async({page,state,ready,ask})=>{
   state.result.claims=[{text:'<img src=x onerror=alert(1)>',citation_ids:[1]}];state.result.answer=state.result.claims[0].text;
   state.result.sources=[{...source(1),source_filename:'<img onerror=alert(1)>.pdf'}];
   await ready();await ask();await page.locator('.ask-answer .citation-marker').waitFor();
   assert.equal(await page.locator('.ask-answer img').count(),0);
   assert.match(await page.locator('.ask-answer .answer-text').textContent(),/<img/);
   assert.match(await page.locator('.ask-answer .source-filename').textContent(),/<img/);
  });
  await scenario('duplicate IDs normalize but do not duplicate cards',async({page,state,ready,ask})=>{
   state.result.claims[0].citation_ids=[1,1];await ready();await ask();await page.locator('.ask-answer .citation-marker').first().waitFor();
   assert.equal(await page.locator('.ask-answer .citation-marker').count(),3);
   assert.equal(await page.locator('.ask-answer .source-card').count(),2);
  });
  const mutations=[v=>{v.claims[0].citation_ids=[99]},v=>{v.claims[0].citation_ids=[true]},v=>{v.claims[0].citation_ids=['1']},
   v=>{v.claims[0].citation_ids=[]},v=>{v.claims=[]},v=>{delete v.claims},v=>{v.citation_version='1'},v=>{v.citation_version=true},
   v=>{v.answer='contradictory'},v=>{v.sources[1].citation_id=1},v=>{v.sources[0].page_start=0},v=>{v.sources[0].source_filename='../private.pdf'},
   v=>{v.claims[1].citation_ids=[1]},v=>{v.sources[1]={...v.sources[0],citation_id:2}}];
  for(let i=0;i<mutations.length;i++)await scenario('malformed version 1 rejected '+i,async({page,state,ready,ask})=>{
   mutations[i](state.result);await ready();await ask();await page.getByText('We could not read the answer. Please try again.',{exact:true}).waitFor();
   assert.equal(await page.locator('.ask-answer').count(),0);assert.equal(await page.locator('.ask-answer .citation-marker').count(),0);
  });
  await scenario('insufficient context has no markers or source cards',async({page,state,ready,ask})=>{
   state.result={status:'insufficient_context',answer:'Not enough relevant information.',retrieved_chunk_count:0,citation_version:1,claims:[],sources:[]};
   await ready();await ask();await page.getByRole('heading',{name:'Not enough relevant information'}).waitFor();
   assert.equal(await page.locator('.citation-marker').count(),0);assert.equal(await page.locator('.source-card').count(),0);
  });
  await scenario('mixed old/new history and unique current/history targets',async({page,state,ready,ask})=>{
   state.history=[saved(legacy(),'old')];await ready();await ask();await page.locator('.ask-answer .citation-marker').first().waitFor();
   await page.locator('.history-item').first().waitFor();await page.locator('.history-item summary').first().click();await page.locator('.history-item summary').last().click();
   const old=page.locator('.history-item').filter({hasText:'Saved question old?'});
   assert.equal(await old.locator('.citation-marker').count(),0);
   await old.getByText('These pages were supplied as context for the answer, not matched to individual sentences.').waitFor();
   assert.equal(await old.locator('.answer-text').textContent(),legacy().answer);
   const ids=await page.locator('.source-card[id]').evaluateAll(nodes=>nodes.map(n=>n.id));assert.equal(new Set(ids).size,ids.length);
   const savedMarker=page.locator('.history-answer .citation-marker').last();await savedMarker.click();
   assert.equal(await page.evaluate(()=>document.activeElement.id),await savedMarker.getAttribute('aria-controls'));
   await page.getByRole('button',{name:'Delete previous question: Saved question history-new?'}).click();
   await page.getByRole('button',{name:'Confirm delete',exact:true}).click();
   await page.waitForFunction(()=>document.querySelectorAll('.history-item').length===1);
  });
  await scenario('malformed version 1 history cannot fall back to legacy',async({page,state,ready})=>{
   const bad=result();bad.claims=[];state.history=[saved(bad)];await ready();await page.getByText('We could not read Ask history. Please try again.').waitFor();
   assert.equal(await page.locator('.history-item').count(),0);
  });
  await scenario('pending citations cannot survive a Knowledge Base switch',async({page,state,ready,ask})=>{
   state.delay=600;await ready();await ask();await page.locator('.ask-loading').waitFor();
   assert.equal(await page.getByRole('button',{name:'Finding an answer…'}).isDisabled(),true);
   await page.getByLabel('Knowledge Base',{exact:true}).selectOption('base-b');await page.waitForTimeout(750);
   assert.equal(await page.locator('.ask-answer').count(),0);assert.equal(await page.locator('.citation-marker').count(),0);
  });
  for(const width of [1440,768,390])await scenario('responsive citations '+width,async({page,state,ready,ask})=>{
   state.result.claims[0].text='longword'.repeat(100);state.result.answer=state.result.claims.map(c=>c.text).join('\n\n');
   state.result.sources[0].source_filename='long-filename-'.repeat(40)+'.pdf';
   await ready();await ask();await page.locator('.ask-answer .citation-marker').first().waitFor();
   await page.locator('.history-item summary').first().click();
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
   await page.emulateMedia({reducedMotion:'reduce'});
   await page.locator('.ask-answer .citation-marker').first().click();
   assert.equal(await page.evaluate(()=>document.activeElement.classList.contains('source-card')),true);
   await page.screenshot({path:'.verification/phase14-citations-'+width+'.png',fullPage:true});
  },width);
  console.log('TOTAL: '+passed+' citation browser scenarios passed; no application console/runtime errors.');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
