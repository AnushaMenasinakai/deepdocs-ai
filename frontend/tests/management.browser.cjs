const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const ROOT=process.env.FRONTEND_TEST_URL||'http://127.0.0.1:5176',date='2026-01-01T00:00:00Z';
const kb=i=>({id:'kb-'+i,name:'Collection '+String(i).padStart(2,'0'),description:null,created_at:date,updated_at:date});
const doc=i=>({id:'doc-'+i,knowledge_base_id:'kb-1',filename:'Reference '+String(i).padStart(2,'0')+'.pdf',status:i%3===0?'processing':i%3===1?'processed':'failed',indexed:i%3===1,failed:i%3===2,file_size:1024,content_type:'application/pdf',created_at:date,updated_at:date});
function paginate(rows,p,docs){
 rows=rows.filter(r=>(docs?r.filename:r.name).toLowerCase().includes((p.get('search')||'').toLowerCase()));
 const status=p.get('status')||'all';if(docs&&status!=='all')rows=rows.filter(r=>status==='indexed'?r.indexed:status==='failed'?r.failed:r.status==='processing');
 const sort=p.get('sort')||(docs?'created_at':'updated_at'),dir=p.get('order')==='asc'?1:-1;
 rows.sort((a,b)=>dir*((a[sort]<b[sort]?-1:a[sort]>b[sort]?1:0)||a.id.localeCompare(b.id)));
 const page=Number(p.get('page')||1),limit=Number(p.get('limit')||20);
 return {items:rows.slice((page-1)*limit,page*limit),page,limit,total:rows.length,total_pages:Math.ceil(rows.length/limit)};
}
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});let passed=0;
 async function scenario(name,run,width=1440){
  const context=await browser.newContext({viewport:{width,height:1000}});
  const state={bases:Array.from({length:25},(_,i)=>kb(i+1)),docs:Array.from({length:23},(_,i)=>doc(i+1)),calls:[],delay:()=>0,status:200,malformed:false};
  await context.addInitScript(()=>localStorage.setItem('deepdocs_access_token','offline.management.token'));
  await context.route('**/api/**',async route=>{
   const req=route.request(),url=new URL(req.url()),path=url.pathname,method=req.method(),headers={'access-control-allow-origin':'*'};
   if(method==='OPTIONS')return route.fulfill({status:204,headers:{...headers,'access-control-allow-headers':'*','access-control-allow-methods':'*'}});
   if(path==='/api/auth/me')return route.fulfill({json:{id:'user',name:'Example',email:'test@example.com',created_at:date},headers});
   state.calls.push({path,method,query:url.search});
   if(path==='/api/knowledge-bases')return route.fulfill({json:state.bases,headers});
   if(path.endsWith('/ask-history'))return route.fulfill({json:[],headers});
   if(path.endsWith('/browse')){
    const docs=path.includes('/documents/'),rows=docs?(path.includes('/kb-1/')?state.docs:[{...doc(90),filename:'Other collection.pdf'}]):state.bases;
    const data=paginate(structuredClone(rows),url.searchParams,docs),status=state.status,malformed=state.malformed;
    await new Promise(r=>setTimeout(r,state.delay(url)));
    return route.fulfill({status,json:status!==200?{detail:'PRIVATE'}:malformed?{items:'invalid'}:data,headers});
   }
   if(method==='DELETE'){state.docs=state.docs.filter(d=>!path.endsWith('/'+d.id));state.bases=state.bases.filter(b=>!path.endsWith('/'+b.id));return route.fulfill({status:204,headers})}
   if(method==='POST'&&path.endsWith('/documents')){const item={...doc(100),filename:'uploaded.pdf',status:'uploaded',indexed:false,failed:false};state.docs.push(item);return route.fulfill({status:201,json:item,headers})}
   throw Error('Unexpected mocked endpoint '+path);
  });
  const page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error'&&!m.text().startsWith('Failed to load resource:'))errors.push(m.text())});
  const go=path=>page.goto(ROOT+path,{waitUntil:'domcontentloaded'}),ready=async()=>{await page.waitForTimeout(80);await page.waitForFunction(()=>document.querySelector('.management-pagination p')?.textContent.includes('results · Page '+(new URL(location.href).searchParams.get('page')||'1')+' of'))};
  const button=name=>page.getByRole('button',{name,exact:true}),settle=async()=>{await page.waitForTimeout(350);await ready()};
  try{await run({page,state,go,ready,settle,button});assert.deepEqual(errors,[]);assert.ok(!(await page.locator('body').innerText()).includes('PRIVATE'));passed++;console.log('PASS '+name)}catch(e){console.log('FAIL '+name);throw e}finally{await context.close()}
 }
 try{
  await scenario('KB pagination, refresh URL and initial loading',async({page,state,go,ready,button})=>{
   state.delay=()=>500;await go('/knowledge-bases');await page.getByText('Loading your Knowledge Bases…',{exact:true}).waitFor();await ready();assert.equal(await page.locator('.kb-card').count(),20);
   await button('Next').click();await ready();assert.equal(await page.locator('.kb-card').count(),5);assert.equal(new URL(page.url()).searchParams.get('page'),'2');await page.reload();await ready();assert.equal(await page.locator('.kb-card').count(),5);await button('Previous').click();await ready();assert.equal(await page.locator('.kb-card').count(),20);
  });
  await scenario('debounced literal search, no results, clear',async({page,state,go,ready,settle,button})=>{
   state.bases[0].name='Auth.Reference';await go('/knowledge-bases');await ready();const count=state.calls.length;await page.getByLabel('Search Knowledge Bases').fill('a');await page.getByLabel('Search Knowledge Bases').fill('AUTH.');await page.waitForTimeout(120);assert.equal(state.calls.length,count);await settle();assert.equal(await page.locator('.kb-card').count(),1);
   await page.getByLabel('Search Knowledge Bases').fill('missing');await settle();await page.getByRole('heading',{name:'No matching Knowledge Bases'}).waitFor();await button('Clear filters').click();await ready();assert.equal(await page.locator('.kb-card').count(),20);
  });
  for(const path of ['/knowledge-bases','/documents'])await scenario('stale search '+path,async({page,state,go,ready,settle})=>{
   await go(path);await ready();state.delay=url=>url.searchParams.get('search')==='01'?850:0;const field=page.getByLabel(path==='/documents'?'Search filenames':'Search Knowledge Bases');await field.fill('01');await page.waitForTimeout(370);await field.fill('02');await settle();await page.waitForTimeout(950);const cards=page.locator(path==='/documents'?'.document-card':'.kb-card');assert.equal(await cards.count(),1);assert.match(await cards.innerText(),/02/);
  });
  await scenario('sort resets page and browser back restores state',async({page,go,ready})=>{
   await go('/knowledge-bases?page=2&limit=5');await ready();await page.getByLabel('Sort by').selectOption('name');await ready();assert.equal(new URL(page.url()).searchParams.get('page'),'1');await page.getByLabel('Order',{exact:true}).selectOption('asc');await ready();assert.equal(await page.locator('.kb-card h2').first().textContent(),'Collection 01');await page.goBack();await ready();assert.equal(await page.getByLabel('Order',{exact:true}).inputValue(),'desc');
  });
  for(const path of ['/knowledge-bases','/documents'])await scenario('failure retry malformed '+path,async({page,state,go,ready,button})=>{
   state.status=503;await go(path);await page.getByRole('alert').waitFor();state.status=200;await button('Try again').click();await ready();state.malformed=true;await page.getByLabel('Order',{exact:true}).selectOption('asc');await page.getByText('We could not read the results. Please try again.').waitFor();state.malformed=false;await button('Try again').click();await ready();
  });
  await scenario('document filters search sorting pagination clear',async({page,go,ready,settle,button})=>{
   await go('/documents?limit=5');await ready();await button('Next').click();await ready();assert.equal(new URL(page.url()).searchParams.get('page'),'2');
   for(const status of ['indexed','processing','failed']){await page.getByLabel('Status',{exact:true}).selectOption(status);await ready();assert.equal(new URL(page.url()).searchParams.get('page'),'1');assert.ok((await page.locator('.document-status').allTextContents()).every(t=>t.toLowerCase()===status))}
   await button('Clear filters').click();await ready();await page.getByLabel('Sort by').selectOption('filename');await page.getByLabel('Order',{exact:true}).selectOption('asc');await ready();assert.equal(await page.locator('.document-card h3').first().textContent(),'Reference 01.pdf');await page.getByLabel('Search filenames').fill('REFERENCE 01.');await settle();assert.equal(await page.locator('.document-card').count(),1);await page.getByLabel('Search filenames').fill('not present');await settle();await page.getByRole('heading',{name:'No matching documents'}).waitFor();
  });
  await scenario('KB switching invalidates old documents',async({page,state,go,ready})=>{
   state.delay=url=>url.pathname.includes('/kb-1/')?800:0;await go('/documents');await page.getByLabel('Knowledge Base',{exact:true}).selectOption('kb-2');await ready();await page.getByRole('heading',{name:'Other collection.pdf'}).waitFor();await page.waitForTimeout(900);assert.equal(await page.locator('.document-card').count(),1);assert.equal(new URL(page.url()).searchParams.get('kb'),'kb-2');
  });
  for(const path of ['/knowledge-bases','/documents'])await scenario('delete last item '+path,async({page,state,go,ready,button})=>{
   state.bases=state.bases.slice(0,2);state.docs=state.docs.slice(0,2);await go(path+'?limit=1&page=2');await ready();await page.locator(path==='/documents'?'.document-card button':'.kb-card button').last().click();await button('Confirm deletion').click();await page.waitForFunction(()=>new URL(location.href).searchParams.get('page')==='1');await ready();assert.equal(await page.locator(path==='/documents'?'.document-card':'.kb-card').count(),1);
  });
  await scenario('upload refresh respects indexed filter',async({page,state,go,ready,button})=>{
   await go('/documents?status=indexed');await ready();const before=state.calls.filter(c=>c.path.endsWith('/browse')).length;await page.getByLabel('PDF file',{exact:true}).setInputFiles({name:'uploaded.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-test')});await button('Upload PDF').click();await page.getByText('PDF uploaded.',{exact:true}).waitFor();await ready();assert.ok(state.calls.filter(c=>c.path.endsWith('/browse')).length>before);assert.equal(await page.getByRole('heading',{name:'uploaded.pdf',exact:true}).count(),0);
  });
  await scenario('Ask selector retains all 25 KBs',async({page,go})=>{await go('/ask');const selector=page.getByLabel('Knowledge Base',{exact:true});await selector.waitFor();assert.equal(await selector.locator('option').count(),25);await selector.selectOption('kb-25')});
  await scenario('expired session',async({page,state,go})=>{state.status=401;await go('/knowledge-bases');await page.getByRole('heading',{name:'Welcome back',exact:true}).waitFor();assert.equal(await page.evaluate(()=>localStorage.getItem('deepdocs_access_token')),null)});
  for(const width of [1440,768,390])for(const path of ['/knowledge-bases','/documents'])await scenario('responsive '+width+' '+path,async({page,state,go,ready})=>{
   const text='<img src=x onerror=alert(1)> '+'LongName'.repeat(14);state.bases[0].name=text;state.docs[0].filename=text+'.pdf';await go(path+'?sort='+(path==='/documents'?'filename':'name')+'&order=asc');await ready();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.equal(await page.locator('.kb-card img,.document-card img').count(),0);await page.screenshot({path:'.verification/management-'+width+path.replace('/','-')+'.png',fullPage:true});
  },width);
  console.log('Management: '+passed+' scenarios passed; no unexpected console/runtime errors.');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
