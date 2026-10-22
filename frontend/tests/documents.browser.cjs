const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
const BASE=(process.env.FRONTEND_TEST_URL || 'http://127.0.0.1:5176'), TOKEN='mockheader.mockpayload.mocksignature';
const kb=(id,name)=>({id,name,description:null,created_at:'2026-01-01T00:00:00Z',updated_at:'2026-01-01T00:00:00Z'});
const bases=[kb('base-a','Research'),kb('base-b','Projects')];
const doc=(id,base,filename)=>({id,knowledge_base_id:base,filename,content_type:'application/pdf',file_size:2048,status:'uploaded',created_at:'2026-09-25T10:00:00Z',updated_at:'2026-09-25T10:00:00Z'});
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve}};
const fixture={name:'notes.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-synthetic-test-fixture')};
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 let passed=0;
 async function scenario(name,run,width=1440){
  const context=await browser.newContext({viewport:{width,height:960}});
  const state={bases:[...bases],docs:{'base-a':[],'base-b':[]},baseStatus:200,listStatus:200,uploadStatus:201,deleteStatus:204,kbDeleteStatus:204,delay:0,baseDelay:0,listDelay:{},network:false,calls:[]};
  const errors=[],consoleErrors=[];
  await context.addInitScript(token=>localStorage.setItem('deepdocs_access_token',token),TOKEN);
  await context.route('**/api/**',async route=>{
   const req=route.request(),url=new URL(req.url()),method=req.method(),path=url.pathname;
   const headers={'access-control-allow-origin':'*'};
   if(method==='OPTIONS')return route.fulfill({status:204,headers:{...headers,'access-control-allow-headers':'*','access-control-allow-methods':'GET, POST, DELETE, PATCH, OPTIONS'}});
   if(path==='/api/auth/me')return route.fulfill({json:{id:'test-user',name:'Example User',email:'user@example.com',created_at:'2026-01-01T00:00:00Z'},headers});
   if(path==='/api/health')return route.fulfill({json:{status:'ok'},headers});
   assert.equal(req.headers().authorization,'Bearer '+TOKEN);
   state.calls.push({method,path});
   if(path==='/api/knowledge-bases/browse')return route.fulfill({json:{items:state.bases,page:1,limit:20,total:state.bases.length,total_pages:1},headers});
   if(path==='/api/knowledge-bases'){
    if(state.baseDelay)await new Promise(r=>setTimeout(r,state.baseDelay));
    return route.fulfill({status:state.baseStatus,json:state.baseStatus===200?state.bases:{detail:'PRIVATE_DETAIL'},headers});
   }
   if(path.match(/^\/api\/knowledge-bases\/[^/]+$/)&&method==='DELETE'){
    return route.fulfill({status:state.kbDeleteStatus,...(state.kbDeleteStatus!==204?{json:{detail:'PRIVATE_DETAIL'}}:{}),headers});
   }
   if((path.endsWith('/documents')||path.endsWith('/documents/browse'))&&path.startsWith('/api/knowledge-bases/')){
    const id=path.split('/')[3];
    if(method==='GET'){
     const snapshot=structuredClone(state.docs[id]||[]),status=state.listStatus,delay=state.listDelay[id]||0;
     if(delay)await new Promise(r=>setTimeout(r,delay));
     return route.fulfill({status,json:status===200?(path.endsWith('/browse')?{items:snapshot.map(d=>({...d,indexed:d.indexed??false,failed:d.failed??(d.status==='failed')})),page:1,limit:20,total:snapshot.length,total_pages:snapshot.length?1:0}:snapshot):{detail:'PRIVATE_DETAIL'},headers});
    }
    assert.equal(method,'POST');
    // Check multipart metadata only; never read a PDF from disk.
    assert.match(req.headers()['content-type'],/^multipart\/form-data; boundary=/);
    const form=await new Request('http://mock',{method:'POST',headers:{'content-type':req.headers()['content-type']},body:req.postDataBuffer()}).formData();
    assert.deepEqual([...form.keys()],['file']);
    assert.equal(form.get('file').name,'notes.pdf');
    assert.equal(form.get('file').type,'application/pdf');
    assert.equal(form.get('file').size,fixture.buffer.length);
    if(state.gate){state.started.resolve();await state.gate.promise;}
    if(state.delay)await new Promise(r=>setTimeout(r,state.delay));
    if(state.network)return route.abort();
    const item=doc('new-doc',id,'notes.pdf');
    if(state.uploadStatus===201)state.docs[id]=[item,...state.docs[id]];
    return route.fulfill({status:state.uploadStatus,json:state.uploadStatus===201?item:{detail:'PRIVATE_DETAIL'},headers});
   }
   if(path.startsWith('/api/documents/')&&method==='DELETE'){
    if(state.gate){state.started.resolve();await state.gate.promise;}
    if(state.delay)await new Promise(r=>setTimeout(r,state.delay));
    if(state.network)return route.abort();
    if(state.deleteStatus===204){
     for(const id of Object.keys(state.docs))state.docs[id]=state.docs[id].filter(d=>d.id!==path.split('/').at(-1));
     return route.fulfill({status:204,headers});
    }
    return route.fulfill({status:state.deleteStatus,json:{detail:'PRIVATE_DETAIL'},headers});
   }
   throw Error('Unexpected API route');
  });
  const page=await context.newPage();
  page.on('pageerror',e=>errors.push(e.message));
  page.on('console',m=>{
   if(m.text().includes(TOKEN)||m.text().includes('PRIVATE_DETAIL'))errors.push('Sensitive output');
   if(m.type()==='error'&&!m.text().startsWith('Failed to load resource:'))consoleErrors.push(m.text());
  });
  const button=name=>name==='Upload PDF'?page.locator('.document-upload button[type=submit], .document-heading-actions > .kb-primary:not(:disabled)'):page.getByRole('button',{name,exact:true});
  const go=()=>page.goto(BASE+'/documents',{waitUntil:'domcontentloaded'});
  const ready=async()=>{await go();await button('Upload PDF').waitFor();await page.waitForFunction(()=>!document.querySelector('.document-heading-actions .kb-primary')?.disabled)};
  const openUpload=async()=>{if(!await page.getByRole('dialog',{name:'Upload PDF',exact:true}).isVisible())await button('Upload PDF').click();await page.getByRole('dialog',{name:'Upload PDF',exact:true}).waitFor()};
  const pick=async file=>{await openUpload();await page.getByLabel('PDF file',{exact:true}).setInputFiles(file||fixture)};
  try{
   await run({page,state,go,ready,pick,button,openUpload});
   assert.deepEqual(errors,[]);assert.deepEqual(consoleErrors,[]);
   const text=await page.locator('body').innerText();
   for(const forbidden of [TOKEN,'PRIVATE_DETAIL','storage_path','stored_filename','owner_id'])assert(!text.includes(forbidden));
   passed++;console.log('PASS '+name);
  }catch(e){await page.screenshot({path:'E:/deepdocs-ai/.verification/document-test-failure.png',fullPage:true}); console.log('Failed scenario: '+name); console.log('Runtime errors: '+errors.length); throw e;}finally{state.gate?.resolve();await context.close();}
 }
 try{
  await scenario('KB loading, first selection and empty document list',async({page,state,go})=>{
   state.baseDelay=1000;state.listDelay['base-a']=2000;
   await go();await page.getByText('Loading Knowledge Bases…',{exact:true}).waitFor();
   await page.getByText('Loading documents…',{exact:true}).waitFor();
   await page.getByRole('heading',{name:'No documents here yet'}).waitFor();
   assert.equal(await page.getByLabel('Knowledge Base',{exact:true}).inputValue(),'base-a');
  });
  await scenario('No Knowledge Bases navigation',async({page,state,go})=>{
   state.bases=[];await go();await page.getByRole('heading',{name:'Create a Knowledge Base first'}).waitFor();
   assert.equal(await page.getByRole('link',{name:'Go to Knowledge Bases'}).getAttribute('href'),'/knowledge-bases');
   assert(!state.calls.some(c=>c.path.endsWith('/documents')));
  });
  await scenario('KB service failure and retry',async({page,state,go,button})=>{
   state.baseStatus=503;await go();await page.getByRole('alert').waitFor();
   state.baseStatus=200;await button('Try again').click();await page.getByRole('heading',{name:'No documents here yet'}).waitFor();
  });
  await scenario('Populated safe metadata and switching',async({page,state,ready})=>{
   state.docs['base-a']=[doc('a','base-a','Alpha.pdf')];state.docs['base-b']=[doc('b','base-b','Beta.pdf')];
   await ready();await page.getByRole('heading',{name:'Alpha.pdf'}).waitFor();
   await page.getByLabel('Knowledge Base',{exact:true}).selectOption('base-b');
   await page.getByRole('heading',{name:'Beta.pdf'}).waitFor();
   assert.equal(await page.getByRole('heading',{name:'Alpha.pdf'}).count(),0);
   await page.getByText('Uploaded',{exact:true}).waitFor();
  });
  await scenario('Slow stale response cannot replace new selection',async({page,state,go})=>{
   state.docs['base-a']=[doc('a','base-a','Stale.pdf')];state.docs['base-b']=[doc('b','base-b','Current.pdf')];state.listDelay['base-a']=700;
   await go();await page.getByText('Loading documents…',{exact:true}).waitFor();
   await page.getByLabel('Knowledge Base',{exact:true}).selectOption('base-b');
   await page.getByRole('heading',{name:'Current.pdf'}).waitFor();
   await page.waitForTimeout(850);
   assert.equal(await page.getByRole('heading',{name:'Stale.pdf'}).count(),0);
  });
  await scenario('Upload FormData, pending protection, local update and reset',async({page,state,ready,pick,button})=>{
   await ready();await pick();await page.locator('.document-selected').filter({hasText:'notes.pdf'}).waitFor();
   state.delay=400;await button('Upload PDF').click();await button('Uploading PDF…').waitFor();
   assert(await button('Uploading PDF…').isDisabled());assert(await page.locator('#document-base').isDisabled());
   await page.getByText('PDF uploaded.',{exact:true}).waitFor();await page.getByRole('heading',{name:'notes.pdf'}).waitFor();
   await button('Upload PDF').click();assert.equal(await page.getByLabel('PDF file',{exact:true}).inputValue(),'');
   assert.equal(state.calls.filter(c=>c.method==='POST').length,1);
   assert(!state.calls.some(c=>c.path.startsWith('/api/documents/')&&c.method==='GET'));
  });
  for(const [name,file,message] of [
   ['no selection',null,'Choose a PDF file first.'],
   ['wrong extension',{...fixture,name:'notes.txt'},'Only PDF files are accepted.'],
   ['wrong MIME',{...fixture,mimeType:'text/plain'},'Only PDF files are accepted.'],
   ['empty',{...fixture,buffer:Buffer.alloc(0)},'The selected PDF is empty.'],
   ['oversized',{...fixture,buffer:Buffer.alloc(10*1024*1024+1)},'Choose a PDF no larger than 10 MiB.'],
  ])await scenario('Client validation: '+name,async({page,state,ready,pick,button,openUpload})=>{
   await ready();await openUpload();if(file)await pick(file);await button('Upload PDF').click();
   await page.getByRole('alert').filter({hasText:message}).waitFor();assert.equal(state.calls.filter(c=>c.method==='POST').length,0);
  });
  await scenario('Clear/change file and missing MIME upload',async({page,ready,pick,button})=>{
   await ready();await pick();await button('Clear file').click();assert.equal(await page.getByLabel('PDF file',{exact:true}).inputValue(),'');
   await pick({...fixture,mimeType:''});await button('Upload PDF').click();await page.getByText('PDF uploaded.',{exact:true}).waitFor();
  });
  for(const status of [413,415,422,503])await scenario('Upload error '+status+' safely retains file and retries',async({page,state,ready,pick,button})=>{
   await ready();await pick();state.uploadStatus=status;await button('Upload PDF').click();await page.getByRole('alert').waitFor();
   assert.notEqual(await page.getByLabel('PDF file',{exact:true}).inputValue(),'');
   state.uploadStatus=201;await button('Upload PDF').click();await page.getByText('PDF uploaded.',{exact:true}).waitFor();
  });
  await scenario('Upload network failure',async({page,state,ready,pick,button})=>{
   await ready();await pick();state.network=true;await button('Upload PDF').click();await page.getByRole('alert').filter({hasText:'Unable to confirm'}).waitFor();
  });
  await scenario('List failure and retry',async({page,state,go,button})=>{
   state.listStatus=503;await go();await page.getByRole('alert').waitFor();state.listStatus=200;
   await button('Try again').click();await page.getByRole('heading',{name:'No documents here yet'}).waitFor();
  });
  await scenario('Delete confirmation, cancellation, pending protection and success',async({page,state,ready,button})=>{
   state.docs['base-a']=[doc('a','base-a','notes.pdf')];await ready();
   await button('Actions for notes.pdf').click();await page.getByRole('menuitem',{name:'Delete',exact:true}).click();assert.equal(state.calls.filter(c=>c.method==='DELETE').length,0);
   assert((await page.getByRole('dialog',{name:'Delete document?',exact:true}).innerText()).includes('notes.pdf'));
   await button('Cancel').click();assert.equal(state.calls.filter(c=>c.method==='DELETE').length,0);
   await button('Actions for notes.pdf').click();await page.getByRole('menuitem',{name:'Delete',exact:true}).click();state.delay=300;await button('Confirm deletion').click();
   await button('Working…').waitFor();assert(await button('Working…').isDisabled());
   await page.getByText('Document deleted.',{exact:true}).waitFor();await page.getByRole('heading',{name:'No documents here yet'}).waitFor();
   assert.equal(state.calls.filter(c=>c.method==='DELETE').length,1);
  });
  await scenario('Failed delete retains document and retries',async({page,state,ready,button})=>{
   state.docs['base-a']=[doc('a','base-a','notes.pdf')];await ready();await button('Actions for notes.pdf').click();await page.getByRole('menuitem',{name:'Delete',exact:true}).click();
   state.deleteStatus=503;await button('Confirm deletion').click();await page.getByRole('alert').waitFor();
   assert.equal(await page.getByRole('heading',{name:'notes.pdf',includeHidden:true}).count(),1);
   state.deleteStatus=204;await button('Confirm deletion').click();await page.getByText('Document deleted.',{exact:true}).waitFor();
  });
  await scenario('Delete 404 reconciles list',async({page,state,ready,button})=>{
   state.docs['base-a']=[doc('a','base-a','notes.pdf')];await ready();await button('Actions for notes.pdf').click();await page.getByRole('menuitem',{name:'Delete',exact:true}).click();
   state.docs['base-a']=[];state.deleteStatus=404;await button('Confirm deletion').click();
   await page.getByRole('heading',{name:'No documents here yet'}).waitFor();
   await page.waitForFunction(()=>!document.querySelector('dialog:modal'));
   assert.equal(await page.getByRole('heading',{name:'Delete document?'}).count(),0);
  });
  await scenario('Upload 404 reloads Knowledge Bases',async({page,state,ready,pick,button})=>{
   await ready();await pick();state.uploadStatus=404;state.bases=[bases[1]];
   await button('Upload PDF').click();await page.getByRole('heading',{name:'Documents in Projects'}).waitFor();
   assert.equal(await page.getByLabel('Knowledge Base',{exact:true}).inputValue(),'base-b');
  });
  await scenario('List 404 refresh action',async({page,state,go,button})=>{
   state.listStatus=404;await go();await button('Refresh Knowledge Bases').waitFor();
   state.bases=[];await button('Refresh Knowledge Bases').click();await page.getByRole('heading',{name:'Create a Knowledge Base first'}).waitFor();
  });
  for(const kind of ['base','list','upload','delete'])await scenario('401 uses existing logout: '+kind,async({page,state,go,ready,pick,button})=>{
   if(kind==='base'){state.baseStatus=401;await go();}
   if(kind==='list'){state.listStatus=401;await go();}
   if(kind==='upload'){await ready();await pick();state.uploadStatus=401;await button('Upload PDF').click();}
   if(kind==='delete'){state.docs['base-a']=[doc('a','base-a','notes.pdf')];await ready();await button('Actions for notes.pdf').click();await page.getByRole('menuitem',{name:'Delete',exact:true}).click();state.deleteStatus=401;await button('Confirm deletion').click();}
   await page.getByRole('heading',{name:'Welcome back'}).waitFor();
   assert.equal(await page.evaluate(()=>localStorage.getItem('deepdocs_access_token')),null);
  });
  await scenario('Knowledge Base 409 actionable message',async({page,state,button})=>{
   state.kbDeleteStatus=409;await page.goto(BASE+'/knowledge-bases',{waitUntil:'domcontentloaded'});await page.locator('.kb-card').filter({has:page.getByRole('heading',{name:'Research',exact:true})}).getByRole('button',{name:'Actions for Research',exact:true}).click();await page.getByRole('menuitem',{name:'Delete',exact:true}).click();
   await button('Confirm deletion').click();await page.getByRole('alert').filter({hasText:'Delete its documents before deleting the Knowledge Base'}).waitFor();
  });
  await scenario('upload pending blocks dismissal and duplicates; restores focus after success',async({page,state,ready,pick,button})=>{
   await ready();assert.equal(await page.locator('.document-upload').count(),0);await pick();
   state.gate=deferred();state.started=deferred();
   await button('Upload PDF').evaluate(e=>{e.click();e.click()});await state.started.promise;
   const dialog=page.getByRole('dialog',{name:'Upload PDF',exact:true});
   await page.keyboard.press('Escape');assert.equal(await dialog.isVisible(),true);
   assert.equal(await button('Close dialog').isDisabled(),true);await button('Close dialog').evaluate(e=>e.click());
   assert.equal(await dialog.isVisible(),true);assert.equal(state.calls.filter(c=>c.method==='POST').length,1);
   assert.equal(await page.locator('.document-heading-actions .kb-primary').isDisabled(),true);
   state.gate.resolve();await page.getByText('PDF uploaded.',{exact:true}).waitFor();
   await page.waitForFunction(()=>!document.querySelector('dialog:modal')&&document.activeElement===document.querySelector('.document-heading-actions .kb-primary'));
   assert.equal(await page.evaluate(()=>document.body.style.overflow),'');
   assert.equal(state.calls.filter(c=>c.method==='POST').length,1);
  });
  await scenario('delete pending blocks Escape and duplicate clicks; failure permits only explicit retry',async({page,state,ready,button})=>{
   state.docs['base-a']=[doc('a','base-a','notes.pdf')];await ready();
   await button('Actions for notes.pdf').click();await page.getByRole('menuitem',{name:'Delete',exact:true}).click();
   state.gate=deferred();state.started=deferred();state.deleteStatus=503;
   await button('Confirm deletion').evaluate(e=>{e.click();e.click()});await state.started.promise;
   await page.keyboard.press('Escape');assert.equal(await page.getByRole('dialog',{name:'Delete document?',exact:true}).isVisible(),true);
   for(const name of ['Cancel','Close dialog','Working…'])assert.equal(await button(name).isDisabled(),true);
   assert.equal(state.calls.filter(c=>c.method==='DELETE').length,1);state.gate.resolve();await page.getByRole('alert').waitFor();
   assert.equal(state.calls.filter(c=>c.method==='DELETE').length,1);state.gate=null;state.deleteStatus=204;
   await button('Confirm deletion').evaluate(e=>{e.click();e.click()});await page.getByText('Document deleted.',{exact:true}).waitFor();
   await page.waitForFunction(()=>!document.querySelector('dialog:modal')&&document.activeElement===document.querySelector('.document-list-heading h2'));
   assert.equal(state.calls.filter(c=>c.method==='DELETE').length,2);
   await page.getByRole('button',{name:'Confirm deletion',exact:true,includeHidden:true}).evaluate(e=>e.click());
   assert.equal(state.calls.filter(c=>c.method==='DELETE').length,2);
  });
  await scenario('closing upload cannot queue another overlay; explicit reopening and keyboard focus work',async({page,state,ready,button,openUpload})=>{
   state.docs['base-a']=[doc('a','base-a','notes.pdf')];await ready();await openUpload();
   await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>document.activeElement.getAttribute('aria-label')),'Close dialog');
   await page.keyboard.press('Shift+Tab');assert.equal(await page.evaluate(()=>document.activeElement.type),'submit');
   await page.evaluate(()=>{document.querySelector('dialog[open] button[aria-label="Close dialog"]').click();queueMicrotask(()=>document.querySelector('.document-heading-actions .kb-primary').click())});
   await page.waitForFunction(()=>!document.querySelector('dialog:modal'));
   assert.equal(await page.getByRole('dialog').count(),0);assert.equal(state.calls.filter(c=>c.method!=='GET').length,0);
   await openUpload();await page.keyboard.press('Escape');await page.waitForFunction(()=>!document.querySelector('dialog:modal'));
   await button('Actions for notes.pdf').click();await page.getByRole('menuitem',{name:'Delete',exact:true}).click();
   assert.equal(await page.getByRole('dialog').count(),1);await button('Cancel').click();
   await page.waitForFunction(()=>!document.querySelector('dialog:modal'));
  });
  await scenario('cancelled upload cannot publish stale result into another KB',async({page,state,ready,pick,button})=>{
   await ready();await pick();state.gate=deferred();state.started=deferred();await button('Upload PDF').click();await state.started.promise;
   const failed=page.waitForEvent('requestfailed',r=>r.method()==='POST');
   await page.evaluate(()=>{history.pushState({},'', '/documents?kb=base-b');dispatchEvent(new PopStateEvent('popstate'))});await failed;
   await page.getByRole('heading',{name:'Documents in Projects',exact:true}).waitFor();state.gate.resolve();
   assert.equal(await page.getByRole('dialog').count(),0);assert.equal(await page.getByText('PDF uploaded.',{exact:true}).count(),0);
   assert.equal(state.calls.filter(c=>c.method==='POST').length,1);await button('Upload PDF').click();
   assert.equal(await page.getByLabel('PDF file',{exact:true}).inputValue(),'');
  });
  await scenario('row status precedence and separate menu eligibility',async({page,state,ready})=>{
   state.docs['base-a']=[
    {...doc('a','base-a','Failed.pdf'),indexed:true,failed:true},
    {...doc('b','base-a','Indexed.pdf'),indexed:true},
    {...doc('c','base-a','Processed.pdf'),status:'processed'},
    doc('d','base-a','Uploaded.pdf'),
   ];await ready();
   assert.deepEqual(await page.locator('.document-status').allTextContents(),['Failed','Indexed','Processed','Uploaded']);
   for(const filename of ['Failed.pdf','Indexed.pdf','Processed.pdf','Uploaded.pdf']){
    await page.getByRole('button',{name:'Actions for '+filename,exact:true}).click();
    assert.equal(await page.getByRole('menuitem',{name:'Process PDF',exact:true}).count(),filename==='Uploaded.pdf'?1:0);
    assert.equal(await page.getByRole('menuitem',{name:'Inspect status',exact:true}).isEnabled(),true);
    assert.equal(await page.getByRole('menuitem',{name:'Delete',exact:true}).isEnabled(),true);
    await page.keyboard.press('Escape');
   }
   assert.equal(state.calls.filter(c=>c.method!=='GET').length,0);
  });
  await scenario('keyboard menu has no navigation or requests and restores focus',async({page,state,ready})=>{
   state.docs['base-a']=[doc('a','base-a','notes.pdf')];await ready();
   const trigger=page.getByRole('button',{name:'Actions for notes.pdf',exact:true}),url=page.url(),calls=state.calls.length;
   await trigger.focus();await page.keyboard.press('Enter');
   const focused=()=>page.evaluate(()=>document.activeElement.textContent);
   assert.equal(await focused(),'Process PDF');await page.keyboard.press('ArrowDown');assert.equal(await focused(),'Inspect status');
   await page.keyboard.press('End');assert.equal(await focused(),'Delete');await page.keyboard.press('Home');assert.equal(await focused(),'Process PDF');
   await page.keyboard.press('Escape');assert.equal(await trigger.evaluate(e=>e===document.activeElement),true);
   assert.equal(await trigger.evaluate(e=>getComputedStyle(e).outlineStyle!=='none'),true);
   assert.equal(page.url(),url);assert.equal(state.calls.length,calls);
   await page.keyboard.press('Space');await page.keyboard.press('End');await page.keyboard.press('Enter');
   await page.getByRole('heading',{name:'Delete document?',exact:true}).waitFor();
   assert.equal(state.calls.length,calls);await page.getByRole('button',{name:'Cancel',exact:true}).click();
   await page.waitForFunction(()=>document.activeElement?.getAttribute('aria-label')==='Actions for notes.pdf');
  });
  for(const width of [1440,1024,768,600,390])await scenario('Responsive '+width,async({page,state,ready,pick,button})=>{
   state.docs['base-a']=[doc('a','base-a','Research-and-reference-material-'.repeat(5)+'.pdf')];
   await ready();await pick();
   const uploadBounds=await page.getByRole('dialog',{name:'Upload PDF',exact:true}).boundingBox();assert(uploadBounds.x>=0&&uploadBounds.x+uploadBounds.width<=width);
   await button('Close dialog').click();await page.waitForFunction(()=>!document.querySelector('dialog:modal'));
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await page.screenshot({path:'E:/deepdocs-ai/.verification/documents-'+width+'.png',fullPage:true});
   const trigger=page.locator('.document-card').getByRole('button',{name:/^Actions for /});
   await trigger.scrollIntoViewIfNeeded();await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   await trigger.click();
   const bounds=await page.getByRole('menu').boundingBox();assert(bounds.x>=0&&bounds.x+bounds.width<=width);
   assert.equal(await page.locator('.document-row-actions .dropdown-trigger').evaluate(e=>e.getBoundingClientRect().height>=44),true);
   await page.getByRole('menuitem',{name:'Delete',exact:true}).click();
   const deleteBounds=await page.getByRole('dialog',{name:'Delete document?',exact:true}).boundingBox();assert(deleteBounds.x>=0&&deleteBounds.x+deleteBounds.width<=width);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await button('Cancel').click();
  },width);
  console.log('TOTAL: '+passed+' document browser scenarios passed.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1});


