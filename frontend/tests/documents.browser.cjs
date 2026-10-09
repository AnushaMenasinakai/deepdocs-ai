const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
const BASE=(process.env.FRONTEND_TEST_URL || 'http://127.0.0.1:5176'), TOKEN='mockheader.mockpayload.mocksignature';
const kb=(id,name)=>({id,name,description:null,created_at:'2026-01-01T00:00:00Z',updated_at:'2026-01-01T00:00:00Z'});
const bases=[kb('base-a','Research'),kb('base-b','Projects')];
const doc=(id,base,filename)=>({id,knowledge_base_id:base,filename,content_type:'application/pdf',file_size:2048,status:'uploaded',created_at:'2026-09-25T10:00:00Z',updated_at:'2026-09-25T10:00:00Z'});
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
     return route.fulfill({status,json:status===200?(path.endsWith('/browse')?{items:snapshot.map(d=>({...d,indexed:false,failed:d.status==='failed'})),page:1,limit:20,total:snapshot.length,total_pages:snapshot.length?1:0}:snapshot):{detail:'PRIVATE_DETAIL'},headers});
    }
    assert.equal(method,'POST');
    // Check multipart metadata only; never read a PDF from disk.
    assert.match(req.headers()['content-type'],/^multipart\/form-data; boundary=/);
    const form=await new Request('http://mock',{method:'POST',headers:{'content-type':req.headers()['content-type']},body:req.postDataBuffer()}).formData();
    assert.deepEqual([...form.keys()],['file']);
    assert.equal(form.get('file').name,'notes.pdf');
    assert.equal(form.get('file').type,'application/pdf');
    assert.equal(form.get('file').size,fixture.buffer.length);
    if(state.delay)await new Promise(r=>setTimeout(r,state.delay));
    if(state.network)return route.abort();
    const item=doc('new-doc',id,'notes.pdf');
    if(state.uploadStatus===201)state.docs[id]=[item,...state.docs[id]];
    return route.fulfill({status:state.uploadStatus,json:state.uploadStatus===201?item:{detail:'PRIVATE_DETAIL'},headers});
   }
   if(path.startsWith('/api/documents/')&&method==='DELETE'){
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
  const button=name=>page.getByRole('button',{name,exact:true});
  const go=()=>page.goto(BASE+'/documents',{waitUntil:'domcontentloaded'});
  const ready=async()=>{await go();await button('Upload PDF').waitFor();await page.waitForFunction(()=>!document.querySelector('.document-upload button[type=submit]').disabled)};
  const pick=async file=>page.getByLabel('PDF file',{exact:true}).setInputFiles(file||fixture);
  try{
   await run({page,state,go,ready,pick,button});
   assert.deepEqual(errors,[]);assert.deepEqual(consoleErrors,[]);
   const text=await page.locator('body').innerText();
   for(const forbidden of [TOKEN,'PRIVATE_DETAIL','storage_path','stored_filename','owner_id'])assert(!text.includes(forbidden));
   passed++;console.log('PASS '+name);
  }catch(e){await page.screenshot({path:'E:/deepdocs-ai/.verification/document-test-failure.png',fullPage:true}); console.log('Failed scenario: '+name); console.log('Runtime errors: '+errors.length); throw e;}finally{await context.close();}
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
   assert(await button('Uploading PDF…').isDisabled());assert(await page.getByLabel('Knowledge Base',{exact:true}).isDisabled());
   await page.getByText('PDF uploaded.',{exact:true}).waitFor();await page.getByRole('heading',{name:'notes.pdf'}).waitFor();
   assert.equal(await page.getByLabel('PDF file',{exact:true}).inputValue(),'');
   assert.equal(state.calls.filter(c=>c.method==='POST').length,1);
   assert(!state.calls.some(c=>c.path.startsWith('/api/documents/')&&c.method==='GET'));
  });
  for(const [name,file,message] of [
   ['no selection',null,'Choose a PDF file first.'],
   ['wrong extension',{...fixture,name:'notes.txt'},'Only PDF files are accepted.'],
   ['wrong MIME',{...fixture,mimeType:'text/plain'},'Only PDF files are accepted.'],
   ['empty',{...fixture,buffer:Buffer.alloc(0)},'The selected PDF is empty.'],
   ['oversized',{...fixture,buffer:Buffer.alloc(10*1024*1024+1)},'Choose a PDF no larger than 10 MiB.'],
  ])await scenario('Client validation: '+name,async({page,state,ready,pick,button})=>{
   await ready();if(file)await pick(file);await button('Upload PDF').click();
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
   await button('Delete notes.pdf').click();assert.equal(state.calls.filter(c=>c.method==='DELETE').length,0);
   assert((await page.locator('.kb-delete').innerText()).includes('notes.pdf'));
   await button('Cancel').click();assert.equal(state.calls.filter(c=>c.method==='DELETE').length,0);
   await button('Delete notes.pdf').click();state.delay=300;await button('Confirm deletion').click();
   await button('Deleting…').waitFor();assert(await button('Deleting…').isDisabled());
   await page.getByText('Document deleted.',{exact:true}).waitFor();await page.getByRole('heading',{name:'No documents here yet'}).waitFor();
   assert.equal(state.calls.filter(c=>c.method==='DELETE').length,1);
  });
  await scenario('Failed delete retains document and retries',async({page,state,ready,button})=>{
   state.docs['base-a']=[doc('a','base-a','notes.pdf')];await ready();await button('Delete notes.pdf').click();
   state.deleteStatus=503;await button('Confirm deletion').click();await page.getByRole('alert').waitFor();
   assert.equal(await page.getByRole('heading',{name:'notes.pdf'}).count(),1);
   state.deleteStatus=204;await button('Confirm deletion').click();await page.getByText('Document deleted.',{exact:true}).waitFor();
  });
  await scenario('Delete 404 reconciles list',async({page,state,ready,button})=>{
   state.docs['base-a']=[doc('a','base-a','notes.pdf')];await ready();await button('Delete notes.pdf').click();
   state.docs['base-a']=[];state.deleteStatus=404;await button('Confirm deletion').click();
   await page.getByRole('heading',{name:'No documents here yet'}).waitFor();
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
   if(kind==='delete'){state.docs['base-a']=[doc('a','base-a','notes.pdf')];await ready();await button('Delete notes.pdf').click();state.deleteStatus=401;await button('Confirm deletion').click();}
   await page.getByRole('heading',{name:'Welcome back'}).waitFor();
   assert.equal(await page.evaluate(()=>localStorage.getItem('deepdocs_access_token')),null);
  });
  await scenario('Knowledge Base 409 actionable message',async({page,state,button})=>{
   state.kbDeleteStatus=409;await page.goto(BASE+'/knowledge-bases',{waitUntil:'domcontentloaded'});await button('Delete Research').click();
   await button('Confirm deletion').click();await page.getByRole('alert').filter({hasText:'Delete its documents before deleting the Knowledge Base'}).waitFor();
  });
  for(const width of [1440,768,390])await scenario('Responsive '+width,async({page,state,ready,pick,button})=>{
   state.docs['base-a']=[doc('a','base-a','Research-and-reference-material-'.repeat(5)+'.pdf')];
   await ready();await pick();
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await page.screenshot({path:'E:/deepdocs-ai/.verification/documents-'+width+'.png',fullPage:true});
   await page.locator('.document-card').getByRole('button',{name:/^Delete /}).click();assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await button('Cancel').click();
  },width);
  console.log('TOTAL: '+passed+' document browser scenarios passed.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1});


