// Dashboard checks use only intercepted synthetic API data, never live accounts.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const baseURL = process.env.FRONTEND_TEST_URL || 'http://127.0.0.1:5176';
const fixture = () => ({knowledge_base_count:7,document_count:12,indexed_document_count:9,processing_document_count:1,failed_document_count:2,ask_history_count:23,recent_knowledge_bases:Array.from({length:5},(_,i)=>({id:'base-'+i,name:i===0?'<img src=x onerror=alert(1)> Reference notes':'Research '+i,document_count:i+1,updated_at:'2026-10-03T10:00:00Z'}))});
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true}); let passed=0;
 async function scenario(name,run,width=1440){
  const context=await browser.newContext({viewport:{width,height:1000}});
  const state={data:fixture(),status:200,delay:0,network:false,healthStatus:{},healthDelay:0,calls:[]};
  await context.addInitScript(()=>localStorage.setItem('deepdocs_access_token','offline.test.token'));
  await context.route('**/api/**',async route=>{
   const req=route.request(),path=new URL(req.url()).pathname,headers={'access-control-allow-origin':'*'};
   if(req.method()==='OPTIONS')return route.fulfill({status:204,headers:{...headers,'access-control-allow-headers':'*','access-control-allow-methods':'GET,OPTIONS'}});
   state.calls.push(path);
   if(path==='/api/auth/me')return route.fulfill({json:{id:'user',name:'Example User',email:'example@example.com',created_at:'2026-01-01'},headers});
   if(path.startsWith('/api/health')){
    const status=state.healthStatus[path]||200;
    if(state.healthDelay)await new Promise(r=>setTimeout(r,state.healthDelay));
    return route.fulfill({status,json:status===200?{status:'ok'}:{detail:'PRIVATE'},headers});
   }
   assert.equal(req.headers().authorization,'Bearer offline.test.token');
   if(path==='/api/dashboard/summary'){
    const data=structuredClone(state.data),status=state.status;
    if(state.delay)await new Promise(r=>setTimeout(r,state.delay));
    if(state.network)return route.abort();
    return route.fulfill({status,json:status===200?data:{detail:'PRIVATE'},headers});
   }
   if(path==='/api/knowledge-bases/browse')return route.fulfill({json:{items:[],page:1,limit:20,total:0,total_pages:0},headers});
   if(path==='/api/knowledge-bases')return route.fulfill({json:[],headers});
   throw Error('Unexpected route: '+path);
  });
  const page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('console',m=>{if(m.type()==='error'&&!m.text().startsWith('Failed to load resource:'))errors.push(m.text());if(/PRIVATE|offline\.test\.token/.test(m.text()))errors.push('Sensitive output')});
  const go=()=>page.goto(baseURL+'/');
  const ready=async()=>{await go();await page.locator('.dashboard-stat dd').first().waitFor()};
  try{await run({page,state,context,go,ready});assert.deepEqual(errors,[]);assert.ok(!state.calls.some(p=>/ask$|embeddings|search/.test(p)));passed++;console.log('PASS '+name)}finally{await context.close()}
 }
 try{
  await scenario('loading skeletons do not show fake counts',async({page,state,go})=>{state.delay=700;await go();await page.getByRole('status',{name:'Loading workspace overview'}).waitFor();assert.equal(await page.locator('.dashboard-stat dd').count(),0);assert.equal(await page.locator('.dashboard-skeleton').count(),4);await page.locator('.dashboard-stat dd').first().waitFor()});
  await scenario('counts, bounded recent list and safe text',async({page,ready})=>{await ready();assert.deepEqual(await page.locator('.dashboard-stat dd').allTextContents(),['7','12','9','23']);assert.equal(await page.locator('.dashboard-recent li').count(),5);await page.getByText('1 document · Edited',{exact:false}).waitFor();await page.getByRole('heading',{name:fixture().recent_knowledge_bases[0].name,exact:true}).waitFor();assert.equal(await page.locator('.dashboard-recent img').count(),0)});
  await scenario('new account onboarding',async({page,state,ready})=>{state.data={...fixture(),knowledge_base_count:0,document_count:0,indexed_document_count:0,processing_document_count:0,failed_document_count:0,ask_history_count:0,recent_knowledge_bases:[]};await ready();await page.getByRole('heading',{name:'Create your first Knowledge Base'}).waitFor();await page.getByRole('link',{name:'Open Knowledge Bases'}).click();await page.getByRole('heading',{name:'Knowledge Bases',exact:true,level:1}).waitFor()});
  await scenario('safe failure and retry',async({page,state,go})=>{state.status=503;await go();await page.getByRole('alert').waitFor();assert.ok(!(await page.getByRole('alert').textContent()).includes('PRIVATE'));state.status=200;await page.getByRole('button',{name:'Retry overview'}).click();await page.locator('.dashboard-stat dd').first().waitFor()});
  await scenario('network failure',async({page,state,go})=>{state.network=true;await go();await page.getByRole('button',{name:'Retry overview'}).waitFor()});
  for(const invalid of ['negative','missing','unbounded','date'])await scenario('malformed summary '+invalid,async({page,state,go})=>{if(invalid==='negative')state.data.document_count=-1;if(invalid==='missing')delete state.data.ask_history_count;if(invalid==='unbounded')state.data.recent_knowledge_bases.push(state.data.recent_knowledge_bases[0]);if(invalid==='date')state.data.recent_knowledge_bases[0].updated_at='not a date';await go();await page.getByText('We could not read your workspace overview. Please try again.').waitFor()});
  await scenario('expired auth uses existing logout',async({page,state,go})=>{state.status=401;await go();await page.getByRole('heading',{name:'Welcome back',exact:true}).waitFor();assert.equal(await page.evaluate(()=>localStorage.getItem('deepdocs_access_token')),null)});
  await scenario('independent health checking and operational',async({page,state,ready})=>{state.healthDelay=600;await ready();assert.equal(await page.getByText('Checking',{exact:true}).count(),3);await page.getByRole('status',{name:'Database: operational'}).waitFor();assert.equal(await page.getByText('Operational',{exact:true}).count(),3)});
  await scenario('health outage preserves summary and recovers',async({page,state,ready})=>{state.healthStatus['/api/health/qdrant']=503;await ready();await page.getByRole('status',{name:'Vector store: unavailable'}).waitFor();assert.equal(await page.locator('.dashboard-stat dd').count(),4);state.healthStatus={};await page.getByRole('button',{name:'Refresh status'}).click();await page.getByRole('status',{name:'Vector store: operational'}).waitFor()});
  await scenario('recent navigation uses existing KB page',async({page,ready})=>{await ready();await page.getByRole('link',{name:'Manage Knowledge Bases for Research 1',exact:true}).click();assert.equal(new URL(page.url()).pathname,'/knowledge-bases')});
  for(const [label,path,title]of [['Manage Knowledge Bases','/knowledge-bases','Knowledge Bases'],['View Documents','/documents','Documents'],['Ask DeepDocs','/ask','Ask DeepDocs']])await scenario('quick action '+label,async({page,ready})=>{await ready();await page.getByRole('navigation',{name:'Quick actions'}).getByRole('link',{name:label,exact:true}).click();await page.getByRole('heading',{name:title,exact:true,level:1}).waitFor();assert.equal(new URL(page.url()).pathname,path)});
  await scenario('reduced motion skeleton',async({page,state,go})=>{state.delay=800;await page.emulateMedia({reducedMotion:'reduce'});await go();await page.locator('.dashboard-skeleton span').first().waitFor();assert.equal(await page.locator('.dashboard-skeleton span').first().evaluate(e=>getComputedStyle(e).animationName),'none')});
  await scenario('unmount during summary load',async({page,state,go})=>{state.delay=650;await go();await page.getByRole('status',{name:'Loading workspace overview'}).waitFor();await page.getByRole('navigation',{name:'Quick actions'}).getByRole('link',{name:'View Documents'}).click();await page.waitForTimeout(750);assert.equal(await page.locator('.dashboard-stat dd').count(),0);assert.equal(new URL(page.url()).pathname,'/documents')});
  for(const width of [1440,768,390])await scenario('responsive '+width,async({page,ready})=>{await ready();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.getByRole('status',{name:'Vector store: operational'}).waitFor();await page.screenshot({path:'.verification/dashboard-'+width+'.png',fullPage:true});const button=page.getByRole('navigation',{name:'Quick actions'}).getByRole('link',{name:'Ask DeepDocs',exact:true});assert.ok((await button.boundingBox()).width>100)},width);
  console.log('Dashboard: '+passed+' scenarios passed; no unexpected console/runtime errors.');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
