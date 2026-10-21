const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const BASE = (process.env.FRONTEND_TEST_URL || 'http://127.0.0.1:5176');
const TOKEN = 'mockheader.mockpayload.mocksignature';
const user = {id:'507f1f77bcf86cd799439011',name:'Example User',email:'user@example.com',created_at:'2026-01-01T00:00:00Z'};
const sample = {id:'507f1f77bcf86cd799439012',name:'Research papers',description:'A focused collection',created_at:'2026-01-01T00:00:00Z',updated_at:'2026-01-01T00:00:00Z'};
(async()=>{
 const browser = await chromium.launch({channel:'msedge',headless:true});
 let passed=0;
 async function scenario(run,{width=1440,token=TOKEN,reducedMotion='no-preference'}={}) {
  const context=await browser.newContext({viewport:{width,height:950},reducedMotion});
  const state={items:[],status:200,delay:0,network:false,requests:[]};
  await context.addInitScript(token=>{if(token)localStorage.setItem('deepdocs_access_token',token)},token);
  await context.route('**/api/**',async route=>{
   const req=route.request(), path=new URL(req.url()).pathname, method=req.method();
   const headers={'access-control-allow-origin':'*'};
   if(method==='OPTIONS') return route.fulfill({status:204,headers:{...headers,'access-control-allow-headers':'*','access-control-allow-methods':'GET, POST, PATCH, DELETE, OPTIONS'}});
   if(path==='/api/auth/me') return route.fulfill({json:user,headers});
   if (path === '/api/dashboard/recent-documents') return route.fulfill({json:{documents:[]},headers});
   if(path==='/api/dashboard/summary') return route.fulfill({json:{knowledge_base_count:0,document_count:0,indexed_document_count:0,processing_document_count:0,failed_document_count:0,ask_history_count:0,recent_knowledge_bases:[]},headers});
      if(path.startsWith('/api/health')) return route.fulfill({json:{status:'ok'},headers});
   if(path.startsWith('/api/knowledge-bases')) {
    assert.equal(req.headers().authorization,'Bearer '+TOKEN);
    const payload=req.postDataJSON();
    state.requests.push({method,payload,path});
    const status=state.status, network=state.network, settled=state.settled;
    if(state.hold)await state.hold(method);
    if(state.delay) await new Promise(r=>setTimeout(r,state.delay));
    if(network)return route.abort();
    if(status!==200){await route.fulfill({status,json:{detail:'PRIVATE_BACKEND_DETAIL'},headers});settled?.();return;}
    if(method==='GET') return route.fulfill({json:path.endsWith('/browse')?{items:state.items,page:1,limit:20,total:state.items.length,total_pages:state.items.length?1:0}:state.items,headers});
    if(method==='POST') {
     const item={...sample,...payload};state.items.push(item);
     return route.fulfill({status:201,json:item,headers});
    }
    if(method==='PATCH') {
     const item={...state.items[0],...payload,updated_at:'2026-09-25T12:00:00Z'};state.items=[item];
     return route.fulfill({json:item,headers});
    }
    if(method==='DELETE'){state.items=[];return route.fulfill({status:204,headers});}
   }
   throw new Error('Unexpected mocked endpoint');
  });
  const page=await context.newPage(),errors=[],logs=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('console',m=>logs.push(m.text()));
  const go=()=>page.goto(BASE+'/knowledge-bases',{waitUntil:'domcontentloaded'});
  const button=name=>page.getByRole('button',{name,exact:true});
  try {
   const action=async (name,label)=>{await button('Actions for '+name).click();await page.getByRole('menuitem',{name:label,exact:true}).click()};
   await run({page,state,go,button,action});
   assert.deepEqual(errors,[]);
   assert(!logs.some(x=>x.includes(TOKEN)||x.includes('PRIVATE_BACKEND_DETAIL')));
   const text=await page.locator('body').innerText();
   assert(!text.includes(TOKEN)&&!text.includes('PRIVATE_BACKEND_DETAIL'));
   passed++;
  }catch(error){console.log('Failed scenario '+(passed+1));console.log(await page.locator('body').innerText());console.log(errors);throw error;}finally{await context.close();}
 }
 try {
  await scenario(async({page,go})=>{await go();await page.getByRole('heading',{name:'Welcome back'}).waitFor();assert.equal(new URL(page.url()).pathname,'/login')},{token:null});
  await scenario(async({page,state,go,button,action})=>{
   state.delay=500;await go();await page.getByText('Loading your Knowledge Bases…',{exact:true}).waitFor();
   await page.getByRole('heading',{name:'Your knowledge starts with a collection'}).waitFor();
   await button('New Knowledge Base').click();
   assert.equal(await page.locator('#kb-name').evaluate(el=>el===document.activeElement),true);
   await page.getByLabel('Name',{exact:true}).fill('   ');
   await button('Create Knowledge Base').click();
   await page.getByRole('alert').waitFor();assert.equal(state.requests.filter(r=>r.method==='POST').length,0);
   assert.equal(await page.locator('#kb-name').getAttribute('maxlength'),'100');
   assert.equal(await page.locator('#kb-description').getAttribute('maxlength'),'500');
   await page.getByLabel('Name',{exact:true}).fill('  Research papers  ');
   await page.getByLabel('Description',{exact:false}).fill('  A focused collection  ');
   await button('Create Knowledge Base').click();
   await button('Saving…').waitFor();assert(await button('Saving…').isDisabled());
   await page.getByText('Knowledge Base created.',{exact:true}).waitFor();
   assert.deepEqual(state.requests.find(r=>r.method==='POST').payload,{name:'Research papers',description:'A focused collection'});
   assert.equal(state.requests.filter(r=>r.method==='POST').length,1);
   await action('Research papers','Edit');
   assert.equal(await page.getByLabel('Name',{exact:true}).inputValue(),'Research papers');
   await page.getByLabel('Name',{exact:true}).fill('Updated notes');
   await page.getByLabel('Description',{exact:false}).fill('   ');
   await button('Save changes').click();await page.getByText('Knowledge Base updated.',{exact:true}).waitFor();
   assert.deepEqual(state.requests.find(r=>r.method==='PATCH').payload,{name:'Updated notes',description:null});
   await action('Updated notes','Delete');
   await page.getByRole('heading',{name:'Delete Knowledge Base?'}).waitFor();
   assert((await page.getByRole('dialog',{name:'Delete Knowledge Base?'}).innerText()).includes('Updated notes'));
   assert.equal(state.requests.filter(r=>r.method==='DELETE').length,0);
   await button('Cancel').click();assert.equal(state.requests.filter(r=>r.method==='DELETE').length,0);
   await action('Updated notes','Delete');await button('Confirm deletion').click();
   await page.getByText('Knowledge Base deleted.',{exact:true}).waitFor();
   await page.getByRole('heading',{name:'Your knowledge starts with a collection'}).waitFor();
   assert(state.requests.filter(r=>r.method==='GET').length<=5); // Initial load plus server refresh after create, edit and delete.
  });
  for(const network of [false,true]) await scenario(async({page,state,go,button,action})=>{
   state.status=503;state.network=network;await go();await page.getByRole('alert').waitFor();
   assert.equal(await page.evaluate(()=>localStorage.getItem('deepdocs_access_token')),TOKEN);
   state.status=200;state.network=false;await button('Try again').click();
   await page.getByRole('heading',{name:'Your knowledge starts with a collection'}).waitFor();
  });
  for(const method of ['POST','PATCH','DELETE']) for(const status of [422,503]) await scenario(async({page,state,go,button,action})=>{
   if(method!=='POST')state.items=[sample];
   await go();await button('New Knowledge Base').waitFor();await page.waitForFunction(()=>!document.querySelector('.kb-toolbar button').disabled);
   if(method==='POST')await button('New Knowledge Base').click();else await action('Research papers',method==='PATCH'?'Edit':'Delete');
   if(method==='POST')await page.getByLabel('Name',{exact:true}).fill('New notes');
   state.status=status;
   await button(method==='POST'?'Create Knowledge Base':method==='PATCH'?'Save changes':'Confirm deletion').click();
   await page.getByRole('alert').waitFor();
   state.status=200;
   await button(method==='POST'?'Create Knowledge Base':method==='PATCH'?'Save changes':'Confirm deletion').click();
   await page.getByText(method==='POST'?'Knowledge Base created.':method==='PATCH'?'Knowledge Base updated.':'Knowledge Base deleted.',{exact:true}).waitFor();
  });
  for(const method of ['PATCH','DELETE'])await scenario(async({page,state,go,button,action})=>{
   state.items=[sample];await go();await button('Actions for Research papers').waitFor();
   await action('Research papers',method==='PATCH'?'Edit':'Delete');
   state.status=404;
   await button(method==='PATCH'?'Save changes':'Confirm deletion').click();
   await page.getByRole('alert').waitFor();
   state.items=[];state.status=200;
   await button('Try again').click();await page.getByRole('heading',{name:'Your knowledge starts with a collection'}).waitFor();
   await button('New Knowledge Base').click();await button('Cancel').click();
  });
  await scenario(async({page,state,go})=>{
   state.status=401;await go();await page.getByRole('heading',{name:'Welcome back'}).waitFor();
   assert.equal(await page.evaluate(()=>localStorage.getItem('deepdocs_access_token')),null);
  });
  for(const width of [1440,1024,768,600,390])await scenario(async({page,state,go,button,action})=>{
   state.items=[{...sample,name:'Research '.repeat(12),description:'Long-description-'.repeat(25)},{...sample,id:'second',name:'Second collection'},{...sample,id:'third',name:'Third collection'}];await go();
   await page.locator('.kb-card').first().waitFor();
   assert.equal(await page.locator('.kb-grid').evaluate(e=>getComputedStyle(e).gridTemplateColumns.split(' ').length),width>1100?3:width>600?2:1);
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await action(state.items[0].name,'Edit');
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await button('Cancel').click();await page.getByRole('dialog').waitFor({state:'hidden'});
   await action(state.items[0].name,'Delete');
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await button('Cancel').click();
   await button('Log out').click();await page.getByRole('heading',{name:'Welcome back'}).waitFor();
  },{width});
  await scenario(async({page,state,go,button})=>{
   state.items=[sample];await go();const card=page.locator('.kb-card');await card.waitFor();
   const link=card.getByRole('link',{name:sample.name,exact:true});assert.equal(await link.getAttribute('href'),'/documents?kb='+sample.id);
   assert.equal(await link.locator('button,a,input').count(),0);await link.focus();assert.ok(await link.evaluate(e=>getComputedStyle(e,'::after').outlineStyle!=='none'));
   await page.keyboard.press('Tab');assert.ok(await button('Actions for '+sample.name).evaluate(e=>e===document.activeElement));
   await page.keyboard.press('Enter');await page.getByRole('menuitem',{name:'Edit',exact:true}).waitFor();assert.equal(new URL(page.url()).pathname,'/knowledge-bases');
   await page.keyboard.press('Enter');await page.getByRole('dialog',{name:'Edit Knowledge Base'}).waitFor();assert.ok(await page.getByLabel('Name',{exact:true}).evaluate(e=>e===document.activeElement));
   await page.keyboard.press('Escape');await page.getByRole('dialog').waitFor({state:'hidden'});assert.ok(await button('Actions for '+sample.name).evaluate(e=>e===document.activeElement));
   await link.focus();await page.keyboard.press('Enter');await page.waitForURL('**/documents?kb='+sample.id);
  });
  await scenario(async({page,state,go})=>{
   state.items=[sample];await go();const card=page.locator('.kb-card');await card.waitFor();
   const box=await card.boundingBox();await page.mouse.click(box.x+12,box.y+box.height-12);await page.waitForURL('**/documents?kb='+sample.id);
  });
  await scenario(async({page,state,go,button,action})=>{
   state.items=[sample];await go();await action(sample.name,'Delete');
   let release,entered;const started=new Promise(r=>entered=r),gate=new Promise(r=>release=r);state.status=409;state.hold=async method=>{if(method==='DELETE'){entered();await gate}};
   await button('Confirm deletion').evaluate(e=>{e.click();e.click()});await started;
   assert.equal(state.requests.filter(r=>r.method==='DELETE').length,1);
   for(const control of await page.getByRole('dialog').getByRole('button').all())assert.ok(await control.isDisabled());
   assert.ok(await button('Actions for '+sample.name).isDisabled());assert.equal(await page.locator('.kb-card-link').getAttribute('aria-disabled'),'true');
   assert.ok(await page.getByLabel('Search Knowledge Bases').isDisabled());await page.keyboard.press('Escape');assert.ok(await page.getByRole('dialog').isVisible());
   release();await page.getByRole('alert').filter({hasText:'Delete its documents before deleting the Knowledge Base'}).waitFor();
   assert.equal(state.requests.filter(r=>r.method==='DELETE').length,1);state.status=200;state.hold=null;
   await button('Confirm deletion').focus();await page.keyboard.press('Enter');await page.getByText('Knowledge Base deleted.',{exact:true}).waitFor();await page.getByRole('dialog').waitFor({state:'hidden'});
   assert.equal(state.requests.filter(r=>r.method==='DELETE').length,2);await page.waitForFunction(()=>!document.querySelector('.kb-toolbar button').disabled);
   assert.ok(await button('New Knowledge Base').evaluate(e=>e===document.activeElement));
  });
  await scenario(async({page,state,go,button,action})=>{
   state.items=[sample];await go();await action(sample.name,'Delete');
   let release,entered,finished;const started=new Promise(r=>entered=r),gate=new Promise(r=>release=r),done=new Promise(r=>finished=r);
   state.status=503;state.settled=finished;state.hold=async method=>{if(method==='DELETE'){entered();await gate}};
   await button('Confirm deletion').click();await started;
   // Real route unmount cancels the client request; its delayed failure cannot
   // change a newly mounted page's confirmation or authorize a retry there.
   const aborted=page.waitForEvent('requestfailed',{predicate:r=>r.method()==='DELETE'});
   await page.evaluate(()=>{history.pushState(null,'','/');dispatchEvent(new PopStateEvent('popstate'))});await page.getByRole('heading',{name:'Welcome back'}).waitFor();await aborted;
   state.status=200;state.hold=null;state.settled=null;await page.evaluate(()=>{history.pushState(null,'','/knowledge-bases');dispatchEvent(new PopStateEvent('popstate'))});await action(sample.name,'Delete');
   release();await done;await page.getByRole('dialog',{name:'Delete Knowledge Base?'}).waitFor();assert.equal(await page.getByRole('alert').count(),0);
   assert.equal(state.requests.filter(r=>r.method==='DELETE').length,1);await button('Confirm deletion').click();await page.getByText('Knowledge Base deleted.',{exact:true}).waitFor();assert.equal(state.requests.filter(r=>r.method==='DELETE').length,2);
  });
  await scenario(async({page,state,go,button,action})=>{
   state.items=[sample];await go();await action(sample.name,'Delete');state.status=503;await button('Confirm deletion').click();await page.getByRole('alert').waitFor();
   await button('Cancel').click();await page.getByRole('dialog').waitFor({state:'hidden'});state.status=200;await action(sample.name,'Delete');assert.equal(await page.getByRole('alert').count(),0);await button('Confirm deletion').click();await page.getByText('Knowledge Base deleted.',{exact:true}).waitFor();assert.equal(state.requests.filter(r=>r.method==='DELETE').length,2);
  });
  await scenario(async({page,state,go,button})=>{
   state.items=[sample];await go();await button('New Knowledge Base').click();assert.equal(await page.getByRole('dialog').evaluate(e=>getComputedStyle(e).animationName),'none');await page.keyboard.press('Escape');await page.getByRole('dialog').waitFor({state:'hidden'});assert.ok(await button('New Knowledge Base').evaluate(e=>e===document.activeElement));
  },{width:390,reducedMotion:'reduce'});
  await scenario(async({page,state,button,action})=>{
   state.items=[sample];await page.goto(BASE+'/knowledge-bases?search=Research&sort=name&order=asc&page=1&limit=5');await page.locator('.kb-card').waitFor();const url=page.url();
   await action(sample.name,'Edit');await page.getByLabel('Description',{exact:false}).fill('Revised description');await button('Save changes').click();await page.getByText('Knowledge Base updated.',{exact:true}).waitFor();await page.locator('.kb-grid[aria-busy="false"]').waitFor();assert.equal(page.url(),url);
   assert.equal(await page.getByLabel('Search Knowledge Bases').inputValue(),'Research');assert.equal(await page.getByLabel('Sort by').inputValue(),'name');assert.equal(await page.getByLabel('Order',{exact:true}).inputValue(),'asc');
   assert.equal(state.requests.filter(r=>r.method==='PATCH').length,1);assert.match(await page.locator('.kb-updated').innerText(),/^Last edited/);assert.equal(await page.locator('.kb-updated time').getAttribute('datetime'),'2026-09-25T12:00:00Z');
  });
  console.log('PASS: '+passed+' mocked Knowledge Base browser scenarios; CRUD, validation, pending state, errors/retry, 401 logout, 404 reconciliation, responsive 1440/1024/768/600/390; no runtime errors or secret output.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error.message);process.exitCode=1});


