// Offline intercepted APIs only. No real accounts or document operations.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const ROOT=process.env.FRONTEND_TEST_URL||'http://127.0.0.1:5176';
const id=n=>n.toString(16).padStart(24,'0'),date='2026-01-01T00:00:00Z';
const doc=n=>({id:id(n),filename:`Reference ${n}.pdf`,status:'uploaded',indexed:false,failed:false,file_size:100,created_at:date,updated_at:date});
const snapshot=(n,changes={})=>({document_id:id(n),operation_state:'idle',document_state:'uploaded',attention:'needs_processing',recommended_action:'process',...changes});
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});let passed=0;
 async function scenario(name,run,width=1440){
  const context=await browser.newContext({viewport:{width,height:1000}}),state={docs:[doc(101),doc(102)],calls:[],status:200,delay:0,network:false,result:n=>snapshot(n)};
  await context.addInitScript(()=>{if(location.protocol==='http:')localStorage.setItem('deepdocs_access_token','offline.inspection.token')});
  await context.route('**/api/**',async route=>{
   const req=route.request(),url=new URL(req.url()),path=url.pathname,method=req.method(),headers={'access-control-allow-origin':'*'};
   if(method==='OPTIONS')return route.fulfill({status:204,headers:{...headers,'access-control-allow-headers':'*','access-control-allow-methods':'*'}});
   if(path==='/api/auth/me')return route.fulfill({json:{id:'user',name:'Example',email:'test@example.com',created_at:date},headers});
   state.calls.push({path,method});
   if(path==='/api/knowledge-bases')return route.fulfill({json:[1,2].map(n=>({id:id(n),name:'Collection '+n,created_at:date,updated_at:date,description:null})),headers});
   if(path.endsWith('/browse'))return route.fulfill({json:{items:state.docs,page:1,limit:20,total:state.docs.length,total_pages:1},headers});
   if(path.endsWith('/operation-status')){
    assert.equal(method,'GET');assert.match(req.headers().authorization,/Bearer /);
    const n=parseInt(path.split('/')[3],16),result=state.result(n),status=state.status,network=state.network;
    await new Promise(r=>setTimeout(r,state.delay));
    if(network)return route.abort();
    return route.fulfill({status,json:status===200?result:{detail:'PRIVATE provider /path secret'},headers});
   }
   if(path.endsWith('/bulk-delete')||path.endsWith('/bulk-reindex')){
    const operation=path.endsWith('/bulk-delete')?'delete':'reindex',ids=req.postDataJSON().document_ids;
    if(operation==='delete')state.docs=state.docs.filter(d=>!ids.includes(d.id));
    return route.fulfill({json:{operation,requested:ids.length,succeeded:ids.length,failed:0,not_attempted:0,
     results:ids.map(document_id=>({document_id,outcome:'succeeded'}))},headers});
   }
   if(method==='DELETE') {state.docs=state.docs.filter(d=>!path.endsWith(d.id));return route.fulfill({status:204,headers});}
   throw new Error('Unexpected API '+method+' '+path);
  });
  const page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error'&&!/Failed to load resource|net::ERR_FAILED/.test(m.text()))errors.push(m.text())});
  const panel=()=>page.getByRole('region',{name:'Document status inspection'}),button=name=>page.getByRole('button',{name,exact:true});
  const inspect=n=>page.getByRole('button',{name:'Inspect status for '+state.docs.find(d=>d.id===id(n)).filename,exact:true}).click();
  const ready=()=>page.getByText(/Inspection received at/).waitFor();
  try{
   await page.goto(ROOT+'/documents?search=Reference&sort=filename&order=asc');await page.locator('.document-card').first().waitFor();
   await run({page,state,panel,button,inspect,ready});assert.deepEqual(errors,[]);assert.ok(!(await page.locator('body').innerText()).includes('PRIVATE'));passed++;console.log('PASS '+name);
  }finally{await context.close()}
 }
 try{
  await scenario('action, safe uploaded snapshot, selection and URL unchanged, keyboard focus',async({page,state,panel,inspect,ready,button})=>{
   assert.equal(await page.getByRole('button',{name:/Inspect status for/}).count(),2);
   await page.getByRole('checkbox',{name:'Select Reference 101.pdf',exact:true}).check();const url=page.url();
   await inspect(101);await ready();assert.equal(page.url(),url);assert.equal(await page.getByRole('checkbox',{name:'Select Reference 101.pdf',exact:true}).isChecked(),true);
   await panel().getByText('This document has not completed processing.').waitFor();await panel().getByText('No operation claim was recorded at inspection time.').waitFor();
   assert.equal(await page.locator('#inspection-title').evaluate(e=>e===document.activeElement),true);
   assert.ok(state.calls.every(c=>c.method==='GET'));await button('Close inspection').click();
   await page.waitForFunction(()=>document.activeElement?.getAttribute('aria-label')==='Inspect status for Reference 101.pdf');
  });
  for(const [name,changes,text] of [
   ['indexed',{document_state:'indexed',attention:'none',recommended_action:'none'},'Indexed (last confirmed)'],
   ['claimed',{operation_state:'claimed_unknown',document_state:'processing',attention:'outcome_uncertain',recommended_action:'refresh'},'Do not retry or delete this document until its state has been verified.'],
   ['failed',{document_state:'failed',attention:'requires_review',recommended_action:'contact_support'},'This document requires further review.'],
   ['processed',{document_state:'processed',attention:'needs_reindex',recommended_action:'reindex'},'This document may need indexing before it can be searched.'],
  ])await scenario(name+' messaging',async({state,inspect,ready,panel})=>{
   state.result=n=>snapshot(n,changes);await inspect(101);await ready();await panel().getByText(text,{exact:true}).waitFor();
   if(name==='claimed')await panel().getByText('An operation may still be running, or its outcome may require verification.').waitFor();
   assert.equal(await panel().getByRole('button').count(),2);
  });
  await scenario('loading, explicit reinspect clears stale snapshot, no polling',async({state,inspect,ready,page,button})=>{
   state.delay=250;await inspect(101);await page.getByRole('status').filter({hasText:'Inspecting document status…'}).waitFor();await ready();
   const calls=()=>state.calls.filter(c=>c.path.endsWith('/operation-status')).length;
   await page.waitForTimeout(400);assert.equal(calls(),1);state.delay=300;state.status=503;await button('Inspect again').click();
   assert.equal(await page.getByText(/Inspection received at/).count(),0);await page.getByRole('alert').filter({hasText:/temporarily unavailable/}).waitFor();assert.equal(calls(),2);
  });
  for(const [status,text] of [[404,'This document is no longer available.'],[422,'This inspection request is invalid.'],[503,'Status inspection is temporarily unavailable.'],[500,'Unable to inspect this document.']])await scenario('safe error '+status,async({state,inspect,page,button,ready})=>{
   state.status=status;await inspect(101);await page.getByRole('alert').filter({hasText:text}).waitFor();state.status=200;await button('Inspect again').click();await ready();
  });
  await scenario('network failure sanitized',async({state,inspect,page})=>{state.network=true;await inspect(101);await page.getByRole('alert').filter({hasText:/Unable to inspect/}).waitFor()});
  for(const kind of ['id','enum','contradiction'])await scenario('malformed response '+kind,async({state,inspect,page})=>{
   state.result=n=>kind==='id'?snapshot(999):kind==='enum'?snapshot(n,{attention:'PRIVATE'}):snapshot(n,{operation_state:'claimed_unknown'});
   await inspect(101);await page.getByRole('alert').filter({hasText:/could not read/}).waitFor();
  });
  await scenario('new document ignores old delayed response',async({state,inspect,ready,panel,page})=>{
   state.delay=500;await inspect(101);state.delay=0;state.result=n=>snapshot(n,{document_state:'indexed',attention:'none',recommended_action:'none'});
   await inspect(102);await ready();await page.waitForTimeout(550);await panel().getByText('Reference 102.pdf',{exact:true}).waitFor();await panel().getByText('Indexed (last confirmed)').waitFor();
  });
  for(const change of ['kb','search','status','sort','order','page','refresh','unmount'])await scenario('inspection invalidated on '+change,async({state,inspect,page,panel,button})=>{
   state.delay=350;await inspect(101);
   if(change==='kb')await page.getByLabel('Knowledge Base',{exact:true}).selectOption(id(2));
   if(change==='search')await page.getByLabel('Search filenames').fill('Changed');
   if(change==='status')await page.getByLabel('Status',{exact:true}).selectOption('failed');
   if(change==='sort')await page.getByLabel('Sort by').selectOption('created_at');
   if(change==='order')await page.getByLabel('Order',{exact:true}).selectOption('desc');
   if(change==='page')await page.evaluate(()=>{const url=new URL(location.href);url.searchParams.set('page','2');history.pushState({},'',url);dispatchEvent(new PopStateEvent('popstate'))});
   if(change==='refresh')await button('Refresh documents').click();
   if(change==='unmount')await page.goto('about:blank');
   await page.waitForTimeout(450);assert.equal(await panel().count(),0);
  });
  await scenario('single deletion closes inspection',async({inspect,ready,button,page,panel})=>{
   await inspect(101);await ready();await button('Delete Reference 101.pdf').click();await button('Confirm deletion').click();await page.getByText('Document deleted.',{exact:true}).waitFor();assert.equal(await panel().count(),0);
  });
  for(const action of ['delete','reindex'])await scenario('inspection preserves bulk '+action,async({inspect,ready,button,page,panel,state})=>{
   await page.getByRole('checkbox',{name:'Select current page',exact:true}).check();await inspect(101);await ready();
   await button(action==='delete'?'Delete selected':'Re-index selected').click();await button('Cancel bulk action').click();
   assert.equal(await page.getByRole('checkbox',{name:'Select current page',exact:true}).isChecked(),true);
   await button(action==='delete'?'Delete selected':'Re-index selected').click();await button(action==='delete'?'Confirm bulk deletion':'Confirm bulk re-index').click();
   await page.getByText('2 document(s) '+(action==='delete'?'deleted':'re-indexed')+' successfully.',{exact:true}).waitFor();
   assert.equal(await panel().count(),0);assert.equal(state.calls.filter(c=>c.method==='POST').length,1);
  });
  await scenario('401 centralized sign out',async({state,inspect,page})=>{state.status=401;await inspect(101);await page.waitForURL('**/login');assert.equal(await page.evaluate(()=>localStorage.getItem('deepdocs_access_token')),null)});
  for(const width of [1440,768,390])await scenario('responsive safe text '+width,async({state,inspect,ready,panel,page})=>{
   state.docs[0].filename='<img src=x onerror=alert(1)>-'+('long-name-'.repeat(20))+'.pdf';await page.getByRole('button',{name:'Refresh documents',exact:true}).click();await page.getByRole('button',{name:'Inspect status for '+state.docs[0].filename,exact:true}).waitFor();
   await inspect(101);await ready();assert.equal(await panel().locator('img').count(),0);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  },width);
  console.log(`Document inspection: ${passed} scenarios passed; no unexpected console/runtime errors.`);
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
