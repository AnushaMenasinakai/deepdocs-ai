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
      if (path === '/api/dashboard/recent-documents') return route.fulfill({json:{documents:[]},headers});
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
          if(state.deleteGate)await state.deleteGate;
          if(state.deleteDelay)await new Promise(r=>setTimeout(r,state.deleteDelay));
          if(state.deleteStatus===204){state.history[base]=state.history[base].filter(item=>item.id!==path.split('/').at(-1));return route.fulfill({status:204,headers})}
          return route.fulfill({status:state.deleteStatus,json:{detail:'PRIVATE'},headers});
        }
        state.historyReads=(state.historyReads||0)+1;
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
        if(state.askGate) await state.askGate;
        else if(state.delay) await new Promise(r=>setTimeout(r,state.delay));
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
    await scenario('saved question answer status timestamp and source ranges',async({page,ready})=>{await ready();await page.locator('.history-select').click();await page.getByText('Saved answer.',{exact:true}).waitFor();await page.getByText('Answered',{exact:true}).waitFor();await page.getByRole('button',{name:'Supporting sources',exact:true}).click();await page.getByText('Page 1',{exact:true}).waitFor();await page.getByText('Pages 3–4',{exact:true}).waitFor();assert.equal(await page.locator('.history-item time').getAttribute('datetime'),'2026-10-02T10:30:00Z');assert.equal(await page.locator('.source-card').count(),2);assert.equal(await page.locator('.history-item').getByText('history-a',{exact:true}).count(),0)});
    for(const outcome of ['answered','insufficient_context'])await scenario('completed ask refreshes history '+outcome,async({page,state,ready,ask})=>{state.history['base-a']=[];if(outcome==='insufficient_context')state.result={status:outcome,answer:'Insufficient information.',retrieved_chunk_count:0,sources:[]};await ready();await ask();await page.locator('.ask-answer').waitFor();await page.locator('.history-question').filter({hasText:'How does this work?'}).waitFor();assert.equal(state.calls.length,1);await page.locator('.history-select').click();assert.equal(await page.locator('.ask-answer .answer-text').textContent(),state.result.answer);assert.equal(await page.locator('.source-card').count(),outcome==='answered'?2:0);const ids=await page.locator('[id]').evaluateAll(nodes=>nodes.map(node=>node.id));assert.equal(new Set(ids).size,ids.length)});
    await scenario('new history appears above previous entry',async({page,ready,ask})=>{await ready();await ask();await page.waitForFunction(()=>document.querySelector('.history-question')?.textContent==='How does this work?');assert.equal(await page.locator('.history-item').count(),2)});
    await scenario('switch clears old history and loads new',async({page,ready})=>{await ready();await page.getByText('Saved question?',{exact:true}).waitFor();await page.getByLabel('Knowledge Base',{exact:true}).selectOption('base-b');await page.getByText('Project question?',{exact:true}).waitFor();assert.equal(await page.getByText('Saved question?',{exact:true}).count(),0)});
    await scenario('late history never replaces new KB',async({page,state,ready})=>{state.historyDelay['base-a']=700;await ready();await page.getByLabel('Knowledge Base',{exact:true}).selectOption('base-b');await page.getByText('Project question?',{exact:true}).waitFor();await page.waitForTimeout(800);assert.equal(await page.getByText('Saved question?',{exact:true}).count(),0)});
    await scenario('delete confirmation cancellation then success and duplicate guard',async({page,state,ready})=>{await ready();await page.getByRole('button',{name:'Delete previous question: Saved question?'}).click();assert.equal(state.deleteCalls,0);await page.getByRole('button',{name:'Cancel',exact:true}).click();assert.equal(state.deleteCalls,0);await page.getByRole('button',{name:'Delete previous question: Saved question?'}).click();state.deleteDelay=500;await page.getByRole('button',{name:'Confirm delete',exact:true}).click();assert.equal(await page.getByRole('button',{name:'Deleting…',exact:true}).isDisabled(),true);await page.getByRole('heading',{name:'No previous questions yet'}).waitFor();assert.equal(state.deleteCalls,1);assert.equal(await page.locator('.history-item').count(),0)});
    await scenario('delete failure keeps history and retry succeeds',async({page,state,ready})=>{state.deleteStatus=503;await ready();await page.locator('.history-select').click();await page.getByRole('button',{name:'Delete previous question: Saved question?'}).click();await page.getByRole('button',{name:'Confirm delete',exact:true}).click();await page.getByRole('alert').waitFor();assert.equal(await page.locator('.history-item').count(),1);assert.equal(await page.locator('.ask-displayed-question').textContent(),'Saved question?');assert.ok(!(await page.getByRole('alert').textContent()).includes('PRIVATE'));state.deleteStatus=204;await page.getByRole('button',{name:'Confirm delete',exact:true}).click();await page.getByRole('heading',{name:'No previous questions yet'}).waitFor()});
    await scenario('delete 404 reconciles history',async({page,state,ready})=>{await ready();await page.getByRole('button',{name:'Delete previous question: Saved question?'}).click();state.deleteStatus=404;state.history['base-a']=[];await page.getByRole('button',{name:'Confirm delete',exact:true}).click();await page.getByRole('heading',{name:'No previous questions yet'}).waitFor()});
    await scenario('history 401 uses existing logout',async({page,state,go})=>{state.historyStatus=401;await go();await page.getByRole('heading',{name:'Welcome back'}).waitFor();assert.equal(await page.evaluate(()=>localStorage.getItem('deepdocs_access_token')),null)});
    await scenario('historical text is escaped',async({page,state,ready})=>{state.history['base-a'][0].question='<img src=x onerror=alert(1)>';state.history['base-a'][0].answer='<script>bad()</script>';await ready();await page.locator('.history-select').click();assert.equal(await page.locator('.history-item img,.history-item script,.ask-answer img,.ask-answer script').count(),0);assert.match(await page.locator('.ask-answer .answer-text').textContent(),/<script>/)});
    await scenario('pending history deletion cannot affect switched KB',async({page,state,ready})=>{state.deleteDelay=700;await ready();await page.getByRole('button',{name:'Delete previous question: Saved question?'}).click();await page.getByRole('button',{name:'Confirm delete',exact:true}).click();await page.getByLabel('Knowledge Base',{exact:true}).selectOption('base-b');await page.getByText('Project question?',{exact:true}).waitFor();await page.waitForTimeout(800);assert.equal(await page.locator('.history-item').count(),1)});
    for(const width of [1440,1024,768,600,390])await scenario('history responsive '+width,async({page,state,ready})=>{state.history['base-a'][0].question='Long question '.repeat(60);state.history['base-a'][0].sources[0].source_filename='long-source-'.repeat(20)+'.pdf';await ready();if(width<=900)await page.getByRole('button',{name:'History',exact:true}).click();await page.locator('.history-select').click();if(width<=900)await page.waitForFunction(()=>!document.querySelector('dialog:modal'));assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);assert.equal(await page.locator('.ask-workspace').evaluate(e=>getComputedStyle(e).gridTemplateColumns.split(' ').length),width>900?2:1);assert.equal(await page.locator('.ask-history').count(),1);await page.locator('.ask-answer').screenshot({path:'.verification/phase10-history-'+width+'.png'});if(width<=900)await page.getByRole('button',{name:'History',exact:true}).click();await page.getByRole('button',{name:/Delete previous question:/}).click();assert.equal(await page.getByRole('button',{name:'Confirm delete'}).isVisible(),true)},width);
    await scenario('selection and New Question are local, accessible and preserve KB',async({page,state,ready,input})=>{
      await ready();const select=page.locator('.history-select');await select.waitFor();const reads=state.historyReads;
      await select.focus();await page.keyboard.press('Enter');assert.equal(await select.getAttribute('aria-pressed'),'true');
      assert.equal(await page.locator('.history-item-selected').count(),1);assert.equal(await page.locator('.ask-displayed-question').textContent(),'Saved question?');
      await page.getByText('Historical snapshot: these source documents may have changed or been deleted.').waitFor();
      await page.getByRole('button',{name:'New Question',exact:true}).click();assert.equal(await page.locator('.ask-answer').count(),0);
      assert.equal(await select.getAttribute('aria-pressed'),'false');assert.equal(await input().evaluate(e=>e===document.activeElement),true);
      assert.equal(await page.getByLabel('Knowledge Base',{exact:true}).inputValue(),'base-a');assert.equal(state.calls.length,0);assert.equal(state.historyReads,reads);assert.equal(await select.count(),1);
    });
    await scenario('current result captures submitted question and never guesses history ID',async({page,state,ready,ask,input})=>{
      await ready();await ask();await page.locator('.ask-answer').waitFor();await page.locator('.history-select').filter({hasText:'How does this work?'}).waitFor();
      assert.equal(await page.locator('.history-select[aria-pressed=true]').count(),0);assert.equal(await page.locator('.ask-view-toolbar').textContent(),'Current resultNew Question');
      await input().fill('Different draft');assert.equal(await page.locator('.ask-displayed-question').textContent(),'How does this work?');assert.equal(await page.locator('.answer-text').textContent(),state.result.answer);
      await page.locator('.history-select').last().click();assert.equal(await page.locator('.ask-displayed-question').textContent(),'Saved question?');assert.equal(state.calls.length,1);
    });
    for(const status of [204,404])await scenario('selected deletion reconciles answer '+status,async({page,state,ready})=>{
      await ready();await page.locator('.history-select').click();await page.getByRole('button',{name:'Delete previous question: Saved question?'}).click();
      state.deleteStatus=status;if(status===404)state.history['base-a']=[];
      await page.getByRole('button',{name:'Confirm delete',exact:true}).click();await page.getByRole('heading',{name:'No previous questions yet'}).waitFor();assert.equal(await page.locator('.ask-answer').count(),0);assert.equal(state.calls.length,0);
    });
    await scenario('pending Ask blocks view changes and duplicate submissions until acknowledgement',async({page,state,ready,ask})=>{
      let release;state.askGate=new Promise(resolve=>{release=resolve});await ready();await page.locator('.history-select').waitFor();await ask();
      await page.getByRole('heading',{name:'Finding your answer'}).waitFor();assert.equal(await page.getByRole('button',{name:'New Question',exact:true}).isDisabled(),true);assert.equal(await page.locator('.history-select').isDisabled(),true);
      await page.locator('.history-select').evaluate(e=>e.click());await page.getByRole('button',{name:'New Question',exact:true}).evaluate(e=>e.click());
      await page.locator('.ask-composer form').evaluate(e=>e.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));release();await page.locator('.ask-answer').waitFor();assert.equal(state.calls.length,1);assert.equal(await page.locator('.ask-displayed-question').textContent(),'How does this work?');
    });
    await scenario('cancelled Ask cannot overwrite selected answer in another KB',async({page,state,ready,ask})=>{
      let release;state.askGate=new Promise(resolve=>{release=resolve});await ready();const requested=page.waitForRequest(r=>r.url().endsWith('/ask'));await ask();const request=await requested;
      const aborted=page.waitForEvent('requestfailed',r=>r===request);await page.getByLabel('Knowledge Base',{exact:true}).selectOption('base-b');await aborted;
      await page.locator('.history-select').click();release();await page.getByText('Historical snapshot: these source documents may have changed or been deleted.').waitFor();assert.equal(await page.locator('.ask-displayed-question').textContent(),'Project question?');assert.equal(state.calls.length,1);
    });
    await scenario('latest twenty use one default-limit history request and preserve ordering',async({page,state,ready})=>{
      state.history['base-a']=Array.from({length:20},(_,i)=>entry('entry-'+i,'Question '+i+'?'));await ready();await page.locator('.history-select').last().waitFor();
      assert.equal(await page.locator('.history-select').count(),20);assert.deepEqual(await page.locator('.history-question').allTextContents(),state.history['base-a'].map(v=>v.question));assert.equal(state.historyReads,1);assert.equal(state.calls.length,0);
    });
    for(const width of [768,600,390])await scenario('mobile history selection, dismissal and single lifecycle '+width,async({page,state,ready})=>{
      await ready();const history=page.getByRole('button',{name:'History',exact:true});await history.click();await page.locator('.history-select').waitFor();const reads=state.historyReads;
      assert.equal(await page.locator('dialog:modal').count(),1);await page.locator('.history-select').click();await page.waitForFunction(()=>!document.querySelector('dialog:modal'));
      assert.equal(await page.locator('.ask-displayed-question').textContent(),'Saved question?');assert.equal(await history.evaluate(e=>e===document.activeElement),true);
      await history.click();await page.keyboard.press('Escape');await page.waitForFunction(()=>!document.querySelector('dialog:modal'));
      assert.equal(state.historyReads,reads);assert.equal(state.calls.length,0);assert.equal(await page.evaluate(()=>document.body.style.overflow),'');
      await history.click();await page.getByRole('button',{name:'Delete previous question: Saved question?'}).click();await page.getByRole('button',{name:'Confirm delete',exact:true}).click();await page.getByRole('heading',{name:'No previous questions yet'}).waitFor();assert.equal(await page.locator('.ask-answer').count(),0);
      assert.equal(await page.locator('#history-title').evaluate(e=>e===document.activeElement),true);await page.getByRole('button',{name:'Close drawer'}).click();await page.waitForFunction(()=>!document.querySelector('dialog:modal'));assert.equal(await history.evaluate(e=>e===document.activeElement),true);
    },width);
    await scenario('history overlay conflicts, resize and reduced motion',async({page,state,ready})=>{
      await ready();await page.emulateMedia({reducedMotion:'reduce'});const history=page.getByRole('button',{name:'History',exact:true});
      await page.getByRole('button',{name:'Open navigation menu'}).click();await history.evaluate(e=>e.click());assert.equal(await history.getAttribute('aria-expanded'),'false');assert.equal(await page.locator('dialog:modal').count(),1);
      await page.keyboard.press('Escape');await page.waitForFunction(()=>!document.querySelector('dialog:modal'));await history.click();await page.locator('.history-select').waitFor();const reads=state.historyReads;
      await page.getByRole('button',{name:'Open navigation menu'}).evaluate(e=>e.click());assert.equal(await page.locator('dialog:modal').count(),1);assert.equal(await page.getByRole('dialog',{name:'Question history'}).evaluate(e=>getComputedStyle(e).animationName),'none');
      await page.setViewportSize({width:1440,height:900});await page.waitForFunction(()=>!document.querySelector('dialog:modal'));assert.equal(await page.locator('#history-title').evaluate(e=>e===document.activeElement),true);assert.equal(state.historyReads,reads);
      await page.setViewportSize({width:390,height:900});assert.equal(await history.getAttribute('aria-expanded'),'false');await history.click();await page.getByRole('button',{name:'Close drawer'}).click();await page.waitForFunction(()=>!document.querySelector('dialog:modal'));assert.equal(state.historyReads,reads);
    },390);
    await scenario('mobile delete pending dismissal, explicit retry and closing open guard',async({page,state,ready})=>{
      await ready();const history=page.getByRole('button',{name:'History',exact:true});await history.click();await page.locator('.history-select').waitFor();
      let release;state.deleteGate=new Promise(resolve=>{release=resolve});state.deleteStatus=503;
      await page.getByRole('button',{name:'Delete previous question: Saved question?'}).click();await page.getByRole('button',{name:'Confirm delete',exact:true}).click();
      assert.equal(await page.getByRole('button',{name:'Close drawer'}).isDisabled(),true);await page.keyboard.press('Escape');assert.equal(await page.locator('dialog:modal').count(),1);
      release();await page.getByRole('alert').waitFor();assert.equal(state.deleteCalls,1);state.deleteGate=null;state.deleteStatus=204;
      await page.getByRole('button',{name:'Confirm delete',exact:true}).click();await page.getByRole('heading',{name:'No previous questions yet'}).waitFor();assert.equal(state.deleteCalls,2);
      await page.getByRole('button',{name:'Close drawer'}).click();await history.evaluate(e=>e.click());assert.equal(await history.getAttribute('aria-expanded'),'false');await page.waitForFunction(()=>!document.querySelector('dialog:modal'));
      await history.click();await page.getByRole('dialog',{name:'Question history'}).waitFor();assert.equal(await page.locator('.ask-history').count(),1);
    },390);
    console.log('TOTAL: '+passed+' history browser scenarios passed; no application console/runtime errors.');
  }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
