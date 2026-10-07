const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const BASE = (process.env.FRONTEND_TEST_URL || 'http://127.0.0.1:5176');
const TOKEN = 'mockheader.mockpayload.mocksignature';
const user = {id:'507f1f77bcf86cd799439011',name:'Example User',email:'user@example.com',created_at:'2026-01-01T00:00:00Z'};
const sample = {id:'507f1f77bcf86cd799439012',name:'Research papers',description:'A focused collection',created_at:'2026-01-01T00:00:00Z',updated_at:'2026-01-01T00:00:00Z'};
(async()=>{
 const browser = await chromium.launch({channel:'msedge',headless:true});
 let passed=0;
 async function scenario(run,{width=1440,token=TOKEN}={}) {
  const context=await browser.newContext({viewport:{width,height:950}});
  const state={items:[],status:200,delay:0,network:false,requests:[]};
  await context.addInitScript(token=>{if(token)localStorage.setItem('deepdocs_access_token',token)},token);
  await context.route('**/api/**',async route=>{
   const req=route.request(), path=new URL(req.url()).pathname, method=req.method();
   const headers={'access-control-allow-origin':'*'};
   if(method==='OPTIONS') return route.fulfill({status:204,headers:{...headers,'access-control-allow-headers':'*','access-control-allow-methods':'GET, POST, PATCH, DELETE, OPTIONS'}});
   if(path==='/api/auth/me') return route.fulfill({json:user,headers});
   if(path==='/api/dashboard/summary') return route.fulfill({json:{knowledge_base_count:0,document_count:0,indexed_document_count:0,processing_document_count:0,failed_document_count:0,ask_history_count:0,recent_knowledge_bases:[]},headers});
      if(path.startsWith('/api/health')) return route.fulfill({json:{status:'ok'},headers});
   if(path.startsWith('/api/knowledge-bases')) {
    assert.equal(req.headers().authorization,'Bearer '+TOKEN);
    const payload=req.postDataJSON();
    state.requests.push({method,payload,path});
    const status=state.status, network=state.network;
    if(state.delay) await new Promise(r=>setTimeout(r,state.delay));
    if(network)return route.abort();
    if(status!==200)return route.fulfill({status,json:{detail:'PRIVATE_BACKEND_DETAIL'},headers});
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
   await run({page,state,go,button});
   assert.deepEqual(errors,[]);
   assert(!logs.some(x=>x.includes(TOKEN)||x.includes('PRIVATE_BACKEND_DETAIL')));
   const text=await page.locator('body').innerText();
   assert(!text.includes(TOKEN)&&!text.includes('PRIVATE_BACKEND_DETAIL'));
   passed++;
  }catch(error){console.log('Failed scenario '+(passed+1));console.log(await page.locator('body').innerText());console.log(errors);throw error;}finally{await context.close();}
 }
 try {
  await scenario(async({page,go})=>{await go();await page.getByRole('heading',{name:'Welcome back'}).waitFor();assert.equal(new URL(page.url()).pathname,'/login')},{token:null});
  await scenario(async({page,state,go,button})=>{
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
   await button('Edit Research papers').click();
   assert.equal(await page.getByLabel('Name',{exact:true}).inputValue(),'Research papers');
   await page.getByLabel('Name',{exact:true}).fill('Updated notes');
   await page.getByLabel('Description',{exact:false}).fill('   ');
   await button('Save changes').click();await page.getByText('Knowledge Base updated.',{exact:true}).waitFor();
   assert.deepEqual(state.requests.find(r=>r.method==='PATCH').payload,{name:'Updated notes',description:null});
   await button('Delete Updated notes').click();
   await page.getByRole('heading',{name:'Delete Knowledge Base?'}).waitFor();
   assert((await page.locator('.kb-delete').innerText()).includes('Updated notes'));
   assert.equal(state.requests.filter(r=>r.method==='DELETE').length,0);
   await button('Cancel').click();assert.equal(state.requests.filter(r=>r.method==='DELETE').length,0);
   await button('Delete Updated notes').click();await button('Confirm deletion').click();
   await page.getByText('Knowledge Base deleted.',{exact:true}).waitFor();
   await page.getByRole('heading',{name:'Your knowledge starts with a collection'}).waitFor();
   assert(state.requests.filter(r=>r.method==='GET').length<=5); // Initial load plus server refresh after create, edit and delete.
  });
  for(const network of [false,true]) await scenario(async({page,state,go,button})=>{
   state.status=503;state.network=network;await go();await page.getByRole('alert').waitFor();
   assert.equal(await page.evaluate(()=>localStorage.getItem('deepdocs_access_token')),TOKEN);
   state.status=200;state.network=false;await button('Try again').click();
   await page.getByRole('heading',{name:'Your knowledge starts with a collection'}).waitFor();
  });
  for(const method of ['POST','PATCH','DELETE']) for(const status of [422,503]) await scenario(async({page,state,go,button})=>{
   if(method!=='POST')state.items=[sample];
   await go();await button('New Knowledge Base').waitFor();await page.waitForFunction(()=>!document.querySelector('.kb-toolbar button').disabled);
   await button(method==='POST'?'New Knowledge Base':method==='PATCH'?'Edit Research papers':'Delete Research papers').click();
   if(method==='POST')await page.getByLabel('Name',{exact:true}).fill('New notes');
   state.status=status;
   await button(method==='POST'?'Create Knowledge Base':method==='PATCH'?'Save changes':'Confirm deletion').click();
   await page.getByRole('alert').waitFor();
   state.status=200;
   await button(method==='POST'?'Create Knowledge Base':method==='PATCH'?'Save changes':'Confirm deletion').click();
   await page.getByText(method==='POST'?'Knowledge Base created.':method==='PATCH'?'Knowledge Base updated.':'Knowledge Base deleted.',{exact:true}).waitFor();
  });
  for(const method of ['PATCH','DELETE'])await scenario(async({page,state,go,button})=>{
   state.items=[sample];await go();await button('Edit Research papers').waitFor();
   await button(method==='PATCH'?'Edit Research papers':'Delete Research papers').click();
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
  for(const width of [1440,768,390])await scenario(async({page,state,go,button})=>{
   state.items=[{...sample,name:'Research '.repeat(12),description:'Long-description-'.repeat(25)}];await go();
   await page.locator('.kb-card').waitFor();
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await page.screenshot({path:'E:/deepdocs-ai/.verification/kb-'+width+'.png',fullPage:true});
   await page.locator('.kb-card button').first().click();
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await page.screenshot({path:'E:/deepdocs-ai/.verification/kb-form-'+width+'.png',fullPage:true});
   await button('Cancel').click();
   await page.locator('.kb-card button').last().click();
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await button('Cancel').click();
   await button('Log out').click();await page.getByRole('heading',{name:'Welcome back'}).waitFor();
  },{width});
  console.log('PASS: '+passed+' mocked Knowledge Base browser scenarios; CRUD, validation, pending state, errors/retry, 401 logout, 404 reconciliation, responsive 1440/768/390; no runtime errors or secret output.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error.message);process.exitCode=1});


