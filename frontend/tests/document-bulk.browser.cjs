// All API requests are intercepted; no real accounts, PDFs, or external services.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const ROOT=process.env.FRONTEND_TEST_URL||'http://127.0.0.1:5176';
const id=n=>n.toString(16).padStart(24,'0'),date='2026-01-01T00:00:00Z';
const base=n=>({id:id(n),name:'Collection '+n,description:null,created_at:date,updated_at:date});
const doc=n=>({id:id(n+100),knowledge_base_id:id(1),filename:'Reference '+n+'.pdf',status:'processed',indexed:false,failed:false,file_size:1024,content_type:'application/pdf',created_at:date,updated_at:date});
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});let passed=0;
 async function scenario(name,run,width=1440){
  const context=await browser.newContext({viewport:{width,height:1000}}),state={docs:Array.from({length:7},(_,i)=>doc(i+1)),calls:[],delay:0,listDelay:()=>0,codes:null,status:200,network:false,alter:x=>x};
  await context.addInitScript(()=>localStorage.setItem('deepdocs_access_token','offline.bulk.token'));
  await context.route('**/api/**',async route=>{
   const req=route.request(),url=new URL(req.url()),path=url.pathname,method=req.method(),headers={'access-control-allow-origin':'*'};
   if(method==='OPTIONS')return route.fulfill({status:204,headers:{...headers,'access-control-allow-headers':'*','access-control-allow-methods':'*'}});
   if(path==='/api/auth/me')return route.fulfill({json:{id:'user',name:'Example',email:'test@example.com',created_at:date},headers});
   const payload=method==='POST'&&!path.endsWith('/documents')?req.postDataJSON():null;
   state.calls.push({path,method,payload,query:url.search});
   if(path==='/api/knowledge-bases')return route.fulfill({json:[base(1),base(2)],headers});
   if(path.endsWith('/browse')){
    const documents=path.includes('/documents/');let rows=documents?(path.includes('/'+id(1)+'/')?state.docs:[{...doc(90),filename:'Other collection.pdf'}]):[base(1),base(2)];
    const p=url.searchParams,search=(p.get('search')||'').toLowerCase(),filter=p.get('status')||'all',field=p.get('sort')||(documents?'created_at':'updated_at'),direction=p.get('order')==='asc'?1:-1;
    rows=rows.filter(r=>(r.filename||r.name).toLowerCase().includes(search));
    if(filter!=='all')rows=rows.filter(r=>filter==='indexed'?r.indexed:filter==='failed'?r.failed:r.status==='processing');
    rows=[...rows].sort((a,b)=>direction*((a[field]<b[field]?-1:a[field]>b[field]?1:0)||a.id.localeCompare(b.id)));
    const page=Number(p.get('page')||1),limit=Number(p.get('limit')||20),data=structuredClone({items:rows.slice((page-1)*limit,page*limit),page,limit,total:rows.length,total_pages:Math.ceil(rows.length/limit)});
    await new Promise(r=>setTimeout(r,state.listDelay(url)));
    return route.fulfill({json:data,headers});
   }
   if(path.endsWith('/bulk-delete')||path.endsWith('/bulk-reindex')){
    const operation=path.endsWith('/bulk-delete')?'delete':'reindex';
    assert.deepEqual(Object.keys(payload),['document_ids']);assert.ok(payload.document_ids.length<=(operation==='delete'?100:5));
    const results=payload.document_ids.map((document_id,index)=>{const code=state.codes?.[index];return code==='not_attempted'?{document_id,outcome:'not_attempted'}:code?{document_id,outcome:'failed',code}:{document_id,outcome:'succeeded'}});
    let report={operation,requested:results.length,results,...Object.fromEntries(['succeeded','failed','not_attempted'].map(key=>[key,results.filter(r=>r.outcome===key).length]))};
    const status=state.status,network=state.network;report=state.alter(report);
    await new Promise(r=>setTimeout(r,state.delay));
    if(network)return route.abort();
    if(status===200)for(const r of results.filter(r=>r.outcome==='succeeded')){
     if(operation==='delete')state.docs=state.docs.filter(d=>d.id!==r.document_id);
     else state.docs=state.docs.map(d=>d.id===r.document_id?{...d,indexed:true}:d);
    }
    return route.fulfill({status,json:status===200?report:{detail:'PRIVATE'},headers});
   }
   if(path.endsWith('/documents')&&method==='POST'){const item={...doc(99),status:'uploaded',filename:'uploaded.pdf'};state.docs.push(item);return route.fulfill({status:201,json:item,headers})}
   throw Error('Unexpected mock endpoint '+path);
  });
  const page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error'&&!m.text().startsWith('Failed to load resource:'))errors.push(m.text())});
  const button=name=>page.getByRole('button',{name,exact:true});
  const pageCheck=()=>page.getByRole('checkbox',{name:'Select current page',exact:true});
  const rows=()=>page.locator('.document-row-check');
  const ready=async()=>{await page.waitForTimeout(90);await page.waitForFunction(()=>document.querySelector('.management-pagination p')?.textContent.includes('results ·')&&!document.querySelector('.document-bulk-controls input')?.disabled)};
  const go=async(query='')=>{await page.goto(ROOT+'/documents'+query);await ready()};
  const calls=()=>state.calls.filter(c=>c.path.includes('/bulk-'));
  const select=async(n=1)=>{for(let i=0;i<n;i++)await rows().nth(i).check()};
  const open=async operation=>{await button(operation==='delete'?'Delete selected':'Re-index selected').click();await page.getByRole('heading',{name:operation==='delete'?'Delete selected documents?':'Re-index selected documents?'}).waitFor()};
  const confirm=operation=>button(operation==='delete'?'Confirm bulk deletion':'Confirm bulk re-index').click();
  try{await run({page,state,go,ready,button,pageCheck,rows,calls,select,open,confirm});assert.deepEqual(errors,[]);assert.ok(!(await page.locator('body').innerText()).includes('PRIVATE'));passed++;console.log('PASS '+name)}catch(e){console.log('FAIL '+name);throw e}finally{await context.close()}
 }
 try{
  await scenario('individual, multiple, indeterminate, checked, clear and keyboard labels',async({page,go,rows,pageCheck,select,button})=>{
   await go('?limit=3');assert.equal(await pageCheck().isChecked(),false);await select();assert.equal(await pageCheck().evaluate(e=>e.indeterminate),true);await page.getByText('1 document selected',{exact:true}).waitFor();
   await rows().nth(1).check();await page.getByText('2 documents selected',{exact:true}).waitFor();await rows().nth(2).check();assert.equal(await pageCheck().isChecked(),true);assert.equal(await pageCheck().evaluate(e=>e.indeterminate),false);
   await button('Clear selection').click();assert.equal(await rows().evaluateAll(es=>es.filter(e=>e.checked).length),0);assert.equal(await pageCheck().isChecked(),false);
   await rows().first().focus();await page.keyboard.press('Space');assert.equal(await rows().first().isChecked(),true);assert.match(await rows().first().getAttribute('aria-label'),/^Select Reference/);
  });
  await scenario('select page submits only visible IDs, never every matching document',async({go,pageCheck,rows,open,confirm,calls,ready})=>{
   await go('?limit=3');await pageCheck().check();assert.equal(await rows().count(),3);await open('delete');await confirm('delete');await ready();assert.deepEqual(calls()[0].payload.document_ids,[id(107),id(106),id(105)]);assert.equal(calls().length,1);
  });
  for(const change of ['search','status','sort','order','page','kb','refresh'])await scenario('selection cleared on '+change,async({page,go,ready,select,button,pageCheck,rows})=>{
   await go('?limit=3');await select();
   if(change==='search')await page.getByLabel('Search filenames').fill('Reference');
   if(change==='status')await page.getByLabel('Status',{exact:true}).selectOption('indexed');
   if(change==='sort')await page.getByLabel('Sort by').selectOption('filename');
   if(change==='order')await page.getByLabel('Order',{exact:true}).selectOption('asc');
   if(change==='page')await button('Next').click();
   if(change==='kb')await page.getByLabel('Knowledge Base',{exact:true}).selectOption(id(2));
   if(change==='refresh')await button('Refresh documents').click();
   await page.waitForTimeout(50);assert.equal(await button('Delete selected').count(),0);assert.equal(await rows().evaluateAll(es=>es.some(e=>e.checked)),false);
   await page.waitForTimeout(350);if(change!=='status')await ready();assert.equal(await pageCheck().isChecked(),false);
   assert.ok(!new URL(page.url()).search.includes(id(107)));
  });
  await scenario('upload refresh clears selection',async({page,go,select,button,ready})=>{
   await go();await select();await page.getByLabel('PDF file',{exact:true}).setInputFiles({name:'uploaded.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-test')});await button('Upload PDF').click();await page.getByText('PDF uploaded.',{exact:true}).waitFor();await ready();assert.equal(await button('Delete selected').count(),0);
  });
  for(const operation of ['delete','reindex'])await scenario(operation+' confirmation, cancellation, success and duplicate prevention',async({page,state,go,select,open,confirm,button,calls,rows,ready})=>{
   await go();await select(2);await open(operation);assert.equal(calls().length,0);await button('Cancel bulk action').click();assert.equal(calls().length,0);await open(operation);state.delay=500;
   const confirmButton=button(operation==='delete'?'Confirm bulk deletion':'Confirm bulk re-index');await confirmButton.evaluate(el=>{el.click();el.click()});
   await button('Working…').waitFor();assert.equal(await rows().first().isDisabled(),true);assert.equal(await page.getByLabel('Knowledge Base',{exact:true}).isDisabled(),true);assert.equal(await page.getByLabel('Search filenames').isDisabled(),true);
   await page.getByText('2 document(s) '+(operation==='delete'?'deleted':'re-indexed')+' successfully.',{exact:true}).waitFor();await ready();assert.equal(calls().length,1);assert.equal(await button('Delete selected').count(),0);
   await page.waitForFunction(()=>document.activeElement===document.querySelector('.document-list-heading h2'));
   if(operation==='reindex')assert.equal(await page.locator('.document-status').filter({hasText:'Indexed'}).count(),2);
  });
  for(const operation of ['delete','reindex'])await scenario(operation+' partial outcomes and safe filename feedback',async({page,state,go,select,open,confirm,ready})=>{
   state.codes=operation==='delete'?[null,'busy','not_found','service_unavailable','not_attempted']:[null,'requires_processing','busy','service_unavailable','not_attempted'];
   state.docs[6].filename='<img src=x onerror=alert(1)>.pdf';await go();await select(5);await open(operation);await confirm(operation);await page.getByText(/1 succeeded, 3 failed, 1 not attempted/).waitFor();await ready();
   await page.getByText('Another operation is using this document. It was not changed by this request.',{exact:false}).waitFor();await page.getByText('Not attempted because the batch stopped after a service failure.',{exact:false}).waitFor();
   if(operation==='reindex')await page.getByText('This document must be processed before it can be re-indexed.',{exact:false}).waitFor();
   assert.equal(await page.locator('.document-bulk-result img').count(),0);assert.ok(!(await page.locator('.document-bulk-result').innerText()).includes(id(106)));
  });
  await scenario('delete last page recovers and retains result report',async({page,go,pageCheck,open,confirm,ready})=>{
   await go('?limit=3&page=3');await pageCheck().check();await open('delete');await confirm('delete');await page.waitForFunction(()=>new URL(location.href).searchParams.get('page')==='2');await ready();assert.equal(await page.locator('.document-card').count(),3);await page.getByText('1 document(s) deleted successfully.',{exact:true}).waitFor();
  });
  await scenario('more than five blocks re-index with no splitting',async({page,go,pageCheck,button,calls})=>{
   await go();await pageCheck().check();assert.equal(await button('Re-index selected').isDisabled(),true);await page.getByText(/Re-index at most 5 documents at once/).waitFor();await page.waitForTimeout(350);assert.equal(calls().length,0);
  });
  await scenario('search invalidates retained cards before delayed response',async({page,state,go,select,rows,button,ready})=>{
   await go();await select();state.listDelay=()=>700;await page.getByLabel('Search filenames').fill('Reference 1');assert.equal(await button('Delete selected').count(),0);assert.equal(await rows().first().isDisabled(),true);await page.waitForTimeout(1050);await ready();assert.equal(await rows().count(),1);assert.equal(await rows().first().isChecked(),false);
  });
  await scenario('unmount aborts pending operation without replay',async({page,state,go,select,open,confirm,calls})=>{
   await go();await select();await open('delete');state.delay=650;await confirm('delete');await page.getByRole('link',{name:'Knowledge Bases',exact:true}).click();await page.waitForTimeout(850);assert.equal(new URL(page.url()).pathname,'/knowledge-bases');assert.equal(await page.locator('.document-bulk-result').count(),0);assert.equal(calls().length,1);
  });
  await scenario('URL KB switch while pending ignores stale report and selection',async({page,state,go,select,open,confirm,calls,ready})=>{
   await go();await select();await open('reindex');state.delay=650;await confirm('reindex');await page.evaluate(kb=>{history.pushState({},'', '/documents?kb='+kb);dispatchEvent(new PopStateEvent('popstate'))},id(2));await ready();await page.waitForTimeout(850);await page.getByRole('heading',{name:'Other collection.pdf'}).waitFor();assert.equal(await page.locator('.document-bulk-result').count(),0);assert.equal(await page.locator('.document-row-check:checked').count(),0);assert.equal(calls().length,1);
  });
  for(const failure of ['network','503','404','422','malformed-count','malformed-id','malformed-code','malformed-order'])await scenario('uncertain/safe '+failure,async({page,state,go,select,open,confirm,rows,button,calls,ready})=>{
   if(failure==='network')state.network=true;else if(/^\d/.test(failure))state.status=Number(failure);
   else state.alter=data=>{if(failure==='malformed-count')data.succeeded=20;if(failure==='malformed-id')data.results[0].document_id=id(999);if(failure==='malformed-code'){data.results[0].outcome='failed';data.results[0].code='PRIVATE'}if(failure==='malformed-order')data.results.reverse();return data};
   await go();await select(2);await open('delete');await confirm('delete');await page.getByRole('alert').waitFor();assert.equal(calls().length,1);assert.equal(await button('Delete selected').count(),0);if(await rows().count())assert.equal(await rows().first().isDisabled(),true);
   state.status=200;state.network=false;state.alter=x=>x;await button('Refresh documents').click();await ready();assert.equal(calls().length,1);
  });
  await scenario('401 preserves centralized logout behavior',async({page,state,go,select,open,confirm})=>{await go();await select();await open('delete');state.status=401;await confirm('delete');await page.getByRole('heading',{name:'Welcome back',exact:true}).waitFor();assert.equal(await page.evaluate(()=>localStorage.getItem('deepdocs_access_token')),null)});
  for(const width of [1440,768,390])await scenario('responsive selection confirmation and partial results '+width,async({page,state,go,select,open,confirm})=>{
   state.docs[6].filename='LongFilename'.repeat(16)+'.pdf';state.codes=['busy',null];await go();await select(2);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await open('reindex');assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await confirm('reindex');await page.getByText(/1 succeeded, 1 failed/).waitFor();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:'.verification/document-bulk-'+width+'.png',fullPage:true});
  },width);
  console.log('Document bulk: '+passed+' scenarios passed; no unexpected console/runtime errors.');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
