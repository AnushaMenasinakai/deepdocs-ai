const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const BASE=(process.env.FRONTEND_TEST_URL || 'http://127.0.0.1:5176');
const TOKEN='mockheader.mockpayload.mocksignature';
const USER={id:'507f1f77bcf86cd799439011',name:'Example User',email:'user@example.com',created_at:'2026-09-22T12:00:00Z'};
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  let passed=0;
  async function scenario({token=null,meStatus=200,network=false,delay=0,width=1440}={},run) {
    const context=await browser.newContext({viewport:{width,height:900}});
    const state={meStatus,network,delay,meBody:USER,loginStatus:200};
    const requests=[]; const errors=[]; const logs=[];
    await context.addInitScript(({token})=>{
      if(!sessionStorage.getItem('test_initialized')) {
        localStorage.clear();
        if(token) localStorage.setItem('deepdocs_access_token',token);
        sessionStorage.setItem('test_initialized','yes');
      }
    },{token});
    await context.route('**/api/**',async route=>{
      const req=route.request(); const path=new URL(req.url()).pathname;
      if(req.method()==='OPTIONS') return route.fulfill({status:204,headers:{'access-control-allow-origin':'*','access-control-allow-headers':'*','access-control-allow-methods':'POST, GET, OPTIONS'}});
      requests.push({path,authorization:req.headers().authorization,body:req.postData()});
      const headers={'access-control-allow-origin':'*'};
      if(path==='/api/knowledge-bases/browse')return route.fulfill({json:{items:[],page:1,limit:20,total:0,total_pages:0},headers});
      if(path==='/api/knowledge-bases') return route.fulfill({json:[],headers});
      if(path==='/api/dashboard/summary') return route.fulfill({json:{knowledge_base_count:0,document_count:0,indexed_document_count:0,processing_document_count:0,failed_document_count:0,ask_history_count:0,recent_knowledge_bases:[]},headers});
      if(path.startsWith('/api/health')) return route.fulfill({json:{status:'ok'},headers});
      if(path==='/api/auth/me') {
        assert.equal(req.headers().authorization,'Bearer '+TOKEN);
        const current={...state};
        if(current.delay) await new Promise(r=>setTimeout(r,current.delay));
        if(current.network) return route.abort();
        return route.fulfill({status:current.meStatus,json:current.meStatus===200?current.meBody:{detail:'PRIVATE_DRIVER_DETAIL'},headers});
      }
      if(path==='/api/auth/login') {
        assert.equal(req.headers().authorization,undefined);
        return route.fulfill({status:state.loginStatus,json:state.loginStatus===200?{access_token:TOKEN,token_type:'bearer'}:{detail:'PRIVATE_DETAIL'},headers});
      }
      if(path==='/api/auth/register') return route.fulfill({status:201,json:USER,headers});
      throw new Error('Unexpected request: '+path);
    });
    const page=await context.newPage();
    page.on('pageerror',e=>errors.push(e.message));
    page.on('console',m=>logs.push(m.text()));
    const go=path=>page.goto(BASE+path);
    const heading=text=>page.getByRole('heading',{name:text,exact:true,level:1}).waitFor();
    const login=async()=>{
      await page.getByLabel('Email',{exact:true}).fill('USER@Example.com');
      await page.getByLabel('Password',{exact:true}).fill('offline-password');
      await page.getByRole('button',{name:'Sign in',exact:true}).click();
    };
    try {
      await run({page,state,requests,go,heading,login});
      assert.equal(errors.length,0);
      assert(!logs.some(x=>x.includes(TOKEN)||x.includes('offline-password')));
      const text=await page.locator('body').innerText();
      assert(!text.includes(TOKEN) && !text.includes('PRIVATE_DRIVER_DETAIL'));
      passed++;
    } finally {await context.close();}
  }
  try {
    for(const path of ['/','/documents','/knowledge-bases','/ask']) await scenario({},async({page,go,heading,requests})=>{
      await go(path); await heading('Welcome back');
      assert.equal(new URL(page.url()).pathname,'/login');
      assert.equal(await page.evaluate(()=>history.state.usr.from),path);
      assert(!requests.some(r=>r.path==='/api/auth/me'));
    });
    for(const path of ['/login','/register']) await scenario({},async({go,heading,requests})=>{
      await go(path); await heading(path==='/login'?'Welcome back':'Create your account');
      assert(!requests.some(r=>r.path==='/api/auth/me'));
    });
    console.log('PASS no-token protection/public auth pages; no unnecessary /me requests');

    for(const [path,title] of [['/','Dashboard'],['/documents','Documents'],['/knowledge-bases','Knowledge Bases'],['/ask','Ask DeepDocs']]) await scenario({token:TOKEN,delay:200},async({page,go,heading})=>{
      await go(path); await heading('Checking your account');
      assert.equal(await page.locator('.sidebar').count(),0);
      assert.equal(await page.locator('form').count(),0);
      await heading(title);
      assert.equal(await page.locator('.session-name').innerText(),USER.name);
    });
    for(const path of ['/login','/register']) await scenario({token:TOKEN},async({page,go,heading})=>{
      await go(path); await heading('Dashboard');
      assert.equal(new URL(page.url()).pathname,'/');
      assert.equal(await page.locator('form').count(),0);
    });
    console.log('PASS stored-token bootstrap, loading without app/form flash, public-only redirects');

    await scenario({token:TOKEN,meStatus:401},async({page,go,heading})=>{
      await go('/documents'); await heading('Welcome back');
      assert.equal(await page.evaluate(()=>localStorage.getItem('deepdocs_access_token')),null);
      assert.equal(new URL(page.url()).pathname,'/login');
    });
    for(const network of [false,true]) await scenario({token:TOKEN,meStatus:503,network},async({page,state,go,heading})=>{
      await go('/documents'); await heading('Unable to verify your account');
      assert.equal(await page.evaluate(()=>localStorage.getItem('deepdocs_access_token')),TOKEN);
      assert.equal(new URL(page.url()).pathname,'/documents');
      state.meStatus=200;state.network=false;
      await page.getByRole('button',{name:'Try again',exact:true}).click();
      await heading('Documents');
    });
    await scenario({token:TOKEN,meStatus:503},async({page,state,go,heading})=>{
      await go('/'); await heading('Unable to verify your account');
      state.meStatus=401;
      await page.getByRole('button',{name:'Try again',exact:true}).click();
      await heading('Welcome back');
      assert.equal(await page.evaluate(()=>localStorage.length),0);
    });
    console.log('PASS invalid-token clearing; 503/network retention, successful retry and retry-401 handling');

    for(const destination of ['/documents','/']) await scenario({},async({page,go,heading,login,requests})=>{
      await go(destination); await heading('Welcome back'); await login();
      await heading(destination==='/documents'?'Documents':'Dashboard');
      assert.equal(new URL(page.url()).pathname,destination);
      assert.deepEqual(await page.evaluate(()=>Object.entries(localStorage)),[['deepdocs_access_token',TOKEN]]);
      assert(requests.some(r=>r.path==='/api/auth/login'));
      assert(requests.some(r=>r.path==='/api/auth/me'));
      await page.getByRole('button',{name:'Log out',exact:true}).click();
      await heading('Welcome back');
      assert.equal(new URL(page.url()).pathname,'/login');
      assert.equal(await page.evaluate(()=>localStorage.length),0);
      assert.equal(await page.locator('.session-name').count(),0);
      await go('/documents'); await heading('Welcome back');
    });
    await scenario({meStatus:503},async({page,state,go,heading,login})=>{
      await go('/documents'); await heading('Welcome back'); await login();
      await heading('Unable to verify your account');
      assert.equal(await page.evaluate(()=>localStorage.getItem('deepdocs_access_token')),TOKEN);
      state.meStatus=200;
      await page.getByRole('button',{name:'Try again',exact:true}).click();
      await heading('Documents');
    });
    await scenario({meStatus:401},async({page,go,heading,login})=>{
      await go('/login'); await heading('Welcome back'); await login();
      await page.getByRole('alert').filter({hasText:'Your sign-in could not be verified.'}).waitFor();
      assert.equal(await page.evaluate(()=>localStorage.length),0);
      assert.equal(await page.locator('.sidebar').count(),0);
    });
    await scenario({},async({page,go,heading,login,state})=>{
      state.loginStatus=401;
      await go('/login'); await heading('Welcome back'); await login();
      await page.getByRole('alert').filter({hasText:'Invalid email or password.'}).waitFor();
      assert.equal(await page.evaluate(()=>localStorage.length),0);
    });
    console.log('PASS login -> /me -> context -> destination, post-login failures, and logout');

    await scenario({},async({page,go,heading,login})=>{
      await go('/documents'); await heading('Welcome back');
      await page.getByRole('link',{name:'Create an account',exact:true}).click();
      await heading('Create your account');
      await page.getByLabel('Name',{exact:true}).fill('Example User');
      await page.getByLabel('Email',{exact:true}).fill('USER@Example.com');
      await page.getByLabel('Password',{exact:true}).fill('offline-password');
      await page.getByLabel('Confirm password',{exact:true}).fill('offline-password');
      await page.getByRole('button',{name:'Create account',exact:true}).click();
      await heading('Welcome back'); await login(); await heading('Documents');
    });
    await scenario({},async({page,go,heading,login})=>{
      await go('/login'); await heading('Welcome back');
      await page.evaluate(()=>history.replaceState({...history.state,usr:{from:'https://example.invalid/'}},''));
      await page.reload(); await heading('Welcome back'); await login(); await heading('Dashboard');
      assert.equal(new URL(page.url()).origin,BASE);
    });
    console.log('PASS intended destination through registration; external redirect rejected');

    for(const width of [1440,768,390]) await scenario({token:TOKEN,width},async({page,go,heading})=>{
      await go('/'); await heading('Dashboard');
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      assert(await page.getByRole('button',{name:'Log out',exact:true}).isVisible());
      await page.screenshot({path:'E:/deepdocs-ai/.verification/authenticated-'+width+'.png',fullPage:true});
      await page.getByRole('button',{name:'Log out',exact:true}).click(); await heading('Welcome back');
    });
    await scenario({token:TOKEN,meStatus:503,width:390},async({page,go,heading})=>{
      await go('/ask');await heading('Unable to verify your account');
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      await page.screenshot({path:'E:/deepdocs-ai/.verification/auth-failure-390.png',fullPage:true});
      await page.getByRole('button',{name:'Sign in again',exact:true}).click(); await heading('Welcome back');
      assert.equal(await page.evaluate(()=>localStorage.length),0);
    });
    console.log('PASS responsive user/logout/retry controls at 1440, 768, 390; no overflow or credential logging');
    console.log('TOTAL:',passed,'browser scenarios passed');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});


