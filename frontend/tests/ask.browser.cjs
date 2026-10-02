// Offline browser checks. Use an existing Playwright installation via PLAYWRIGHT_MODULE.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const baseURL = process.env.FRONTEND_TEST_URL || 'http://127.0.0.1:5176';
const bases = [{id:'base-a',name:'Research'},{id:'base-b',name:'Projects'}];
const source = {document_id:'a'.repeat(24),source_filename:'guide.pdf',page_start:1,page_end:1};
const answer = {status:'answered',answer:'Grounded answer.\n\nAnother paragraph.',retrieved_chunk_count:2,sources:[source,{...source,page_start:3,page_end:4}]};
(async () => {
  const browser = await chromium.launch({channel:'msedge',headless:true});
  let passed = 0;
  async function scenario(name, run, width=1440) {
    const context = await browser.newContext({viewport:{width,height:1000}});
    const state = {bases,baseStatus:200,baseDelay:0,status:200,delay:0,network:false,result:structuredClone(answer),calls:[],auth:200};
    await context.addInitScript(() => localStorage.setItem('deepdocs_access_token','fake.test.token'));
    await context.route('**/api/**',async route => {
      const req=route.request(), path=new URL(req.url()).pathname;
      const headers={'access-control-allow-origin':'*'};
      if(req.method()==='OPTIONS') return route.fulfill({status:204,headers:{...headers,'access-control-allow-headers':'*','access-control-allow-methods':'GET, POST, OPTIONS'}});
      if(path==='/api/auth/me') return route.fulfill({status:state.auth,json:state.auth===200?{id:'user',name:'Example User',email:'user@example.com',created_at:'2026-01-01'}:{detail:'PRIVATE'},headers});
      if(path==='/api/health') return route.fulfill({json:{status:'ok'},headers});
      assert.equal(req.headers().authorization,'Bearer fake.test.token');
      if(path==='/api/knowledge-bases') {
        if(state.baseDelay) await new Promise(r=>setTimeout(r,state.baseDelay));
        return route.fulfill({status:state.baseStatus,json:state.baseStatus===200?state.bases:{detail:'PRIVATE'},headers});
      }
      if(path.endsWith('/ask-history')) return route.fulfill({json:[],headers});
      if(path.endsWith('/ask')) {
        const body=req.postDataJSON();
        assert.deepEqual(Object.keys(body),['question']);
        assert.equal(body.question,body.question.trim());
        state.calls.push({path,body});
        const result=structuredClone(state.result), status=state.status;
        if(state.delay) await new Promise(r=>setTimeout(r,state.delay));
        if(state.network) return route.abort();
        return route.fulfill({status,json:status===200?result:{detail:'PRIVATE'},headers});
      }
      throw Error('Unexpected route: '+path);
    });
    const page=await context.newPage(), errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    page.on('console',m=>{if(m.type()==='error'&&!m.text().startsWith('Failed to load resource:'))errors.push(m.text());if(/fake\.test\.token|PRIVATE/.test(m.text()))errors.push('Sensitive output');});
    const go=()=>page.goto(baseURL+'/ask');
    const input=()=>page.getByLabel('Your question',{exact:true});
    const button=()=>page.getByRole('button',{name:'Ask DeepDocs',exact:true});
    const ready=async()=>{await go();await input().waitFor()};
    const ask=async()=>{await input().fill('  How does this work?  ');await button().click()};
    try {await run({page,state,go,ready,input,button,ask});assert.deepEqual(errors,[]);passed++;console.log('PASS '+name)}
    finally {await context.close()}
  }
  try {
    await scenario('KB loading and first selection',async({page,state,go,input})=>{state.baseDelay=500;await go();await page.getByText('Loading Knowledge Bases…',{exact:true}).waitFor();await input().waitFor();assert.equal(await page.getByLabel('Knowledge Base',{exact:true}).inputValue(),'base-a')});
    await scenario('empty Knowledge Bases',async({page,state,go})=>{state.bases=[];await go();await page.getByRole('heading',{name:'Create a Knowledge Base first'}).waitFor();assert.equal(await page.getByLabel('Your question').count(),0)});
    await scenario('KB error and retry',async({page,state,go,input})=>{state.baseStatus=503;await go();await page.getByRole('button',{name:'Try again',exact:true}).waitFor();state.baseStatus=200;await page.getByRole('button',{name:'Try again',exact:true}).click();await input().waitFor()});
    await scenario('empty whitespace and maximum length',async({ready,input,button,page,state})=>{await ready();assert.equal(await button().isDisabled(),true);await input().fill('   ');assert.equal(await button().isDisabled(),true);assert.equal(await input().getAttribute('maxlength'),'1000');await input().fill('x'.repeat(1000));assert.equal(await button().isEnabled(),true);await input().press('End');await input().press('x');assert.equal((await input().inputValue()).length,1000);assert.equal(state.calls.length,0)});
    await scenario('answer paragraphs and source pages',async({page,ready,ask,state})=>{await ready();await ask();await page.getByRole('heading',{name:'Answer',exact:true}).waitFor();assert.equal(state.calls.length,1);assert.equal(state.calls[0].body.question,'How does this work?');assert.equal(await page.locator('.source-card').count(),2);await page.getByText('Page 1',{exact:true}).waitFor();await page.getByText('Pages 3–4',{exact:true}).waitFor();assert.equal(await page.locator('.answer-text').textContent(),answer.answer)});
    await scenario('safe text and defensive source deduplication',async({page,state,ready,ask})=>{state.result.answer='<img src=x onerror=alert(1)> harmless text';state.result.sources=[source,source];await ready();await ask();await page.locator('.answer-text').waitFor();assert.equal(await page.locator('.answer-text img').count(),0);assert.equal(await page.locator('.source-card').count(),1);assert.match(await page.locator('.answer-text').textContent(),/<img/)});
    await scenario('duplicate submission prevented',async({page,state,ready,ask})=>{state.delay=500;await ready();await ask();assert.equal(await page.getByRole('button',{name:'Finding an answer…'}).isDisabled(),true);await page.locator('.ask-composer form').evaluate(form=>{form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}))});await page.locator('.answer-text').waitFor();assert.equal(state.calls.length,1)});
    await scenario('insufficient context is not an error',async({page,state,ready,ask})=>{state.result={status:'insufficient_context',answer:'Not enough relevant information.',retrieved_chunk_count:0,sources:[]};await ready();await ask();await page.getByRole('heading',{name:'Not enough relevant information'}).waitFor();assert.equal(await page.locator('.source-card').count(),0);assert.equal(await page.getByRole('alert').count(),0)});
    for(const status of [503,422,404,401]) await scenario('safe HTTP '+status,async({page,state,ready,ask})=>{state.status=status;await ready();await ask();if(status===401){await page.getByRole('heading',{name:'Welcome back'}).waitFor();assert.equal(await page.evaluate(()=>localStorage.getItem('deepdocs_access_token')),null)}else{await page.getByRole('alert').waitFor();assert.ok(!(await page.getByRole('alert').textContent()).includes('PRIVATE'));if(status===404){state.bases=[bases[1]];await page.getByRole('button',{name:'Refresh Knowledge Bases'}).click();await page.waitForFunction(()=>document.querySelector('#ask-base')?.value==='base-b')}}});
    await scenario('network error and retry submission',async({page,state,ready,ask,button})=>{state.network=true;await ready();await ask();await page.getByRole('alert').waitFor();state.network=false;await button().click();await page.locator('.answer-text').waitFor()});
    for(const bad of [null,{...answer,sources:null},{...answer,sources:[{...source,page_start:0}]},{...answer,sources:[{...source,source_filename:'C:/private.pdf'}]},{...answer,status:'unknown'}]) await scenario('malformed response safely rejected',async({page,state,ready,ask})=>{state.result=bad;await ready();await ask();await page.getByText('We could not read the answer. Please try again.').waitFor();assert.equal(await page.locator('.answer-text').count(),0)});
    await scenario('switch cancels old response',async({page,state,ready,ask})=>{state.delay=700;state.result.answer='OLD ANSWER';await ready();await ask();await page.getByLabel('Knowledge Base',{exact:true}).selectOption('base-b');state.delay=0;state.result.answer='NEW ANSWER';await ask();await page.getByText('NEW ANSWER',{exact:true}).waitFor();await page.waitForTimeout(800);assert.equal(await page.getByText('OLD ANSWER',{exact:true}).count(),0);assert.equal(state.calls.at(-1).path,'/api/knowledge-bases/base-b/ask')});
    await scenario('unmount cancels pending request',async({page,state,ready,ask})=>{state.delay=500;await ready();await ask();await page.getByRole('link',{name:'Dashboard',exact:true}).click();await page.waitForTimeout(650);assert.equal(await page.locator('.answer-text').count(),0)});
    for(const width of [1440,768,390]) await scenario('responsive '+width,async({page,state,ready,ask})=>{state.result.sources[0].source_filename='long-filename-'.repeat(30)+'.pdf';state.result.answer='Readable answer.\n\n'+'longword'.repeat(80);await ready();await ask();await page.locator('.source-card').first().waitFor();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);assert.equal(await page.getByLabel('Knowledge Base',{exact:true}).isVisible(),true);await page.screenshot({path:'.verification/phase9-'+width+'.png',fullPage:true})},width);
    for (const outcome of ['answered', 'insufficient_context', 'failure']) await scenario('loader lifecycle '+outcome, async ({page,state,ready,ask}) => {
      state.delay=500;
      if(outcome==='failure') state.status=503;
      if(outcome==='insufficient_context') state.result={status:outcome,answer:'Not enough relevant information.',retrieved_chunk_count:0,sources:[]};
      await ready();await ask();
      await page.getByRole('heading',{name:'Finding your answer',exact:true}).waitFor();
      assert.equal(await page.locator('.ask-loading').getAttribute('role'),'status');
      assert.equal(await page.locator('.ask-loading-dots span').count(),3);
      assert.equal(await page.locator('.ask-loading-dots').getAttribute('aria-hidden'),'true');
      assert.deepEqual(await page.locator('.ask-loading-dots span').evaluateAll(dots=>dots.map(dot=>getComputedStyle(dot).animationDelay)),['0s','0.2s','0.4s']);
      await page.locator(outcome==='failure'?'[role=alert]':'.answer-text').waitFor();
      assert.equal(await page.locator('.ask-loading').count(),0);
    });
    for (const width of [1440,768,390]) await scenario('loader responsive and reduced motion '+width, async ({page,state,ready,ask}) => {
      // Hold only the mocked response; application request timing is unchanged.
      state.delay=2500;await ready();await ask();
      await page.locator('.ask-loading').waitFor();
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
      assert.equal(await page.locator('.ask-loading-dots span').first().evaluate(dot=>getComputedStyle(dot).animationName),'ask-dot-pulse');
      await page.screenshot({path:'.verification/phase9-loader-'+width+'.png',fullPage:true});
      await page.emulateMedia({reducedMotion:'reduce'});
      assert.deepEqual(await page.locator('.ask-loading-dots span').evaluateAll(dots=>dots.map(dot=>getComputedStyle(dot).animationName)),['none','none','none']);
      assert.equal(await page.getByRole('heading',{name:'Finding your answer',exact:true}).isVisible(),true);
      await page.locator('.answer-text').waitFor();
    }, width);
    console.log('TOTAL: '+passed+' Ask browser scenarios passed; no application console/runtime errors.');
  } finally {await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
