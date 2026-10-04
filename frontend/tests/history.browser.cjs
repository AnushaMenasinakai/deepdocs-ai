// Offline browser checks. Use an existing Playwright installation via PLAYWRIGHT_MODULE.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const baseURL = process.env.FRONTEND_TEST_URL || 'http://127.0.0.1:5176';
const bases = [{id:'base-a',name:'Research'},{id:'base-b',name:'Projects'}];
const source = {document_id:'a'.repeat(24),source_filename:'guide.pdf',page_start:1,page_end:1};
const entry = (id='history-a',question='Saved question?') => ({id,question,status:'answered',answer:'Saved answer.',retrieved_chunk_count:2,sources:[{...source},{...source,page_start:3,page_end:4}],created_at:'2026-10-02T10:30:00Z'});
const answer = {status:'answered',answer:'Grounded answer.\n\nAnother paragraph.',retrieved_chunk_count:2,sources:[{...source},{...source,page_start:3,page_end:4}]};
(async () => {
  const browser = await chromium.launch({channel:'msedge',headless:true});
  let passed = 0;
  async function scenario(name, run, width=1440) {
    const context = await browser.newContext({viewport:{width,height:1000}});
    const state = {bases,baseStatus:200,baseDelay:0,status:200,delay:0,network:false,result:structuredClone(answer),calls:[],auth:200,history:{'base-a':[entry()],'base-b':[entry('history-b','Project question?')]},historyStatus:200,historyDelay:{},deleteStatus:204,deleteDelay:0,deleteCalls:0,historyNetwork:false};
    await context.addInitScript(() => localStorage.setItem('deepdocs_access_token','fake.test.token'));
    await context.route('**/api/**',async route => {
      const req=route.request(), path=new URL(req.url()).pathname;
      const headers={'access-control-allow-origin':'*'};
      if(req.method()==='OPTIONS') return route.fulfill({status:204,headers:{...headers,'access-control-allow-headers':'*','access-control-allow-methods':'GET, POST, DELETE, OPTIONS'}});
      if(path==='/api/auth/me') return route.fulfill({status:state.auth,json:state.auth===200?{id:'user',name:'Example User',email:'user@example.com',created_at:'2026-01-01'}:{detail:'PRIVATE'},headers});
      if(path==='/api/dashboard/summary') return route.fulfill({json:{knowledge_base_count:0,document_count:0,indexed_document_count:0,processing_document_count:0,failed_document_count:0,ask_history_count:0,recent_knowledge_bases:[]},headers});
      if(path.startsWith('/api/health')) return route.fulfill({json:{status:'ok'},headers});
      assert.equal(req.headers().authorization,'Bearer fake.test.token');
      if(path==='/api/knowledge-bases') {
        if(state.baseDelay) await new Promise(r=>setTimeout(r,state.baseDelay));
        return route.fulfill({status:state.baseStatus,json:state.baseStatus===200?state.bases:{detail:'PRIVATE'},headers});
      }
      if(path.includes('/ask-history')) {
        const base=path.split('/')[3];
        if(req.method()==='DELETE') {
          state.deleteCalls++;
          if(state.deleteDelay)await new Promise(r=>setTimeout(r,state.deleteDelay));
          if(state.deleteStatus===204){state.history[base]=state.history[base].filter(item=>item.id!==path.split('/').at(-1));return route.fulfill({status:204,headers})}
          return route.fulfill({status:state.deleteStatus,json:{detail:'PRIVATE'},headers});
        }
        const snapshot=structuredClone(state.history[base]),status=state.historyStatus;
        if(state.historyDelay[base])await new Promise(r=>setTimeout(r,state.historyDelay[base]));
        if(state.historyNetwork)return route.abort();
        return route.fulfill({status,json:status===200?snapshot:{detail:'PRIVATE'},headers});
      }
      if(path.endsWith('/ask')) {
        const body=req.postDataJSON();
        assert.deepEqual(Object.keys(body),['question']);
        assert.equal(body.question,body.question.trim());
        state.calls.push({path,body});
        const result=structuredClone(state.result), status=state.status;
        if(state.delay) await new Promise(r=>setTimeout(r,state.delay));
        if(state.network) return route.abort();
        if(status===200)state.history[path.split('/')[3]].unshift({...entry('new-history',body.question),...result});
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
    await scenario('history loading then populated',async({page,state,ready})=>{state.historyDelay['base-a']=500;await ready();await page.getByText('Loading previous questions…',{exact:true}).waitFor();await page.getByText('Saved question?',{exact:true}).waitFor()});
    await scenario('empty history',async({page,state,ready})=>{state.history['base-a']=[];await ready();await page.getByRole('heading',{name:'No previous questions yet'}).waitFor()});
    await scenario('history loading failure and retry',async({page,state,ready})=>{state.historyStatus=503;await ready();await page.getByRole('button',{name:'Retry history'}).waitFor();state.historyStatus=200;await page.getByRole('button',{name:'Retry history'}).click();await page.getByText('Saved question?',{exact:true}).waitFor()});
    await scenario('history network failure',async({page,state,ready})=>{state.historyNetwork=true;await ready();await page.getByText('Unable to connect to Ask history. Please try again.').waitFor()});
    await scenario('malformed history safely rejected',async({page,state,ready})=>{state.history['base-a']=[{...entry(),created_at:'invalid'}];await ready();await page.getByText('We could not read Ask history. Please try again.').waitFor()});
    await scenario('saved question answer status timestamp and source ranges',async({page,ready})=>{await ready();await page.locator('.history-item summary').click();await page.getByText('Saved answer.',{exact:true}).waitFor();await page.getByText('Answered',{exact:true}).waitFor();await page.getByText('Page 1',{exact:true}).waitFor();await page.getByText('Pages 3–4',{exact:true}).waitFor();assert.equal(await page.locator('.history-item time').getAttribute('datetime'),'2026-10-02T10:30:00Z');assert.equal(await page.locator('.history-item .source-card').count(),2);assert.equal(await page.locator('.history-item').getByText('history-a',{exact:true}).count(),0)});
    for(const outcome of ['answered','insufficient_context'])await scenario('completed ask refreshes history '+outcome,async({page,state,ready,ask})=>{state.history['base-a']=[];if(outcome==='insufficient_context')state.result={status:outcome,answer:'Insufficient information.',retrieved_chunk_count:0,sources:[]};await ready();await ask();await page.locator('.ask-answer').waitFor();await page.locator('.history-question').filter({hasText:'How does this work?'}).waitFor();assert.equal(state.calls.length,1);await page.locator('.history-item summary').click();assert.equal(await page.locator('.history-item .answer-text').textContent(),state.result.answer);assert.equal(await page.locator('.history-item .source-card').count(),outcome==='answered'?2:0);const ids=await page.locator('[id]').evaluateAll(nodes=>nodes.map(node=>node.id));assert.equal(new Set(ids).size,ids.length)});
    await scenario('new history appears above previous entry',async({page,ready,ask})=>{await ready();await ask();await page.waitForFunction(()=>document.querySelector('.history-question')?.textContent==='How does this work?');assert.equal(await page.locator('.history-item').count(),2)});
    await scenario('switch clears old history and loads new',async({page,ready})=>{await ready();await page.getByText('Saved question?',{exact:true}).waitFor();await page.getByLabel('Knowledge Base',{exact:true}).selectOption('base-b');await page.getByText('Project question?',{exact:true}).waitFor();assert.equal(await page.getByText('Saved question?',{exact:true}).count(),0)});
    await scenario('late history never replaces new KB',async({page,state,ready})=>{state.historyDelay['base-a']=700;await ready();await page.getByLabel('Knowledge Base',{exact:true}).selectOption('base-b');await page.getByText('Project question?',{exact:true}).waitFor();await page.waitForTimeout(800);assert.equal(await page.getByText('Saved question?',{exact:true}).count(),0)});
    await scenario('delete confirmation cancellation then success and duplicate guard',async({page,state,ready})=>{await ready();await page.getByRole('button',{name:'Delete previous question: Saved question?'}).click();assert.equal(state.deleteCalls,0);await page.getByRole('button',{name:'Cancel',exact:true}).click();assert.equal(state.deleteCalls,0);await page.getByRole('button',{name:'Delete previous question: Saved question?'}).click();state.deleteDelay=500;await page.getByRole('button',{name:'Confirm delete',exact:true}).click();assert.equal(await page.getByRole('button',{name:'Deleting…',exact:true}).isDisabled(),true);await page.getByRole('heading',{name:'No previous questions yet'}).waitFor();assert.equal(state.deleteCalls,1);assert.equal(await page.locator('.history-item').count(),0)});
    await scenario('delete failure keeps history and retry succeeds',async({page,state,ready})=>{state.deleteStatus=503;await ready();await page.getByRole('button',{name:'Delete previous question: Saved question?'}).click();await page.getByRole('button',{name:'Confirm delete',exact:true}).click();await page.getByRole('alert').waitFor();assert.equal(await page.locator('.history-item').count(),1);assert.ok(!(await page.getByRole('alert').textContent()).includes('PRIVATE'));state.deleteStatus=204;await page.getByRole('button',{name:'Confirm delete',exact:true}).click();await page.getByRole('heading',{name:'No previous questions yet'}).waitFor()});
    await scenario('delete 404 reconciles history',async({page,state,ready})=>{await ready();await page.getByRole('button',{name:'Delete previous question: Saved question?'}).click();state.deleteStatus=404;state.history['base-a']=[];await page.getByRole('button',{name:'Confirm delete',exact:true}).click();await page.getByRole('heading',{name:'No previous questions yet'}).waitFor()});
    await scenario('history 401 uses existing logout',async({page,state,go})=>{state.historyStatus=401;await go();await page.getByRole('heading',{name:'Welcome back'}).waitFor();assert.equal(await page.evaluate(()=>localStorage.getItem('deepdocs_access_token')),null)});
    await scenario('historical text is escaped',async({page,state,ready})=>{state.history['base-a'][0].question='<img src=x onerror=alert(1)>';state.history['base-a'][0].answer='<script>bad()</script>';await ready();await page.locator('.history-item summary').click();assert.equal(await page.locator('.history-item img,.history-item script').count(),0);assert.match(await page.locator('.history-item .answer-text').textContent(),/<script>/)});
    await scenario('pending history deletion cannot affect switched KB',async({page,state,ready})=>{state.deleteDelay=700;await ready();await page.getByRole('button',{name:'Delete previous question: Saved question?'}).click();await page.getByRole('button',{name:'Confirm delete',exact:true}).click();await page.getByLabel('Knowledge Base',{exact:true}).selectOption('base-b');await page.getByText('Project question?',{exact:true}).waitFor();await page.waitForTimeout(800);assert.equal(await page.locator('.history-item').count(),1)});
    for(const width of [1440,768,390])await scenario('history responsive '+width,async({page,state,ready})=>{state.history['base-a'][0].question='Long question '.repeat(60);state.history['base-a'][0].sources[0].source_filename='long-source-'.repeat(20)+'.pdf';await ready();await page.locator('.history-item summary').click();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await page.locator('.history-item').screenshot({path:'.verification/phase10-history-'+width+'.png'});await page.getByRole('button',{name:/Delete previous question:/}).click();assert.equal(await page.getByRole('button',{name:'Confirm delete'}).isVisible(),true)},width);
    console.log('TOTAL: '+passed+' history browser scenarios passed; no application console/runtime errors.');
  }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
