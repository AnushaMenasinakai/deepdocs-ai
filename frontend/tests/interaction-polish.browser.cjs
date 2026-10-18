const { run, assert, settle } = require('./overlay-harness.cjs');
const source = `
function Harness(){
 const [kind,setKind]=React.useState('');
 window.closeOverlay=()=>setKind(''); window.actions=window.actions||0;
 const content=h(React.Fragment,null,
   h('button',{onClick:()=>window.actions++},'Usable action'),
   h('button',{style:{visibility:'hidden'}},'Hidden action'),
   h('fieldset',{disabled:true},h('button',null,'Disabled by fieldset')),
   h('div',{style:{height:1200}},'Long content'));
 return h('main',{'data-testid':'ready'},
   h('button',{onClick:()=>setKind('Modal')},'Open Modal'),
   h('button',{onClick:()=>setKind('Drawer')},'Open Drawer'),
   h(Modal,{open:kind==='Modal',onClose:()=>setKind(''),title:'Modal example'},content),
   h(Drawer,{open:kind==='Drawer',onClose:()=>setKind(''),title:'Drawer example'},content));
}`;
async function open(p, kind) {
 await p.getByRole('button', {name:'Open '+kind, exact:true}).click();
 const dialog=p.getByRole('dialog',{name:kind+' example'});
 await dialog.waitFor(); await settle(dialog); return dialog;
}
run('Interaction polish',source,async test=>{
 for(const kind of ['Modal','Drawer']) {
   await test(kind+' skips hidden and fieldset-disabled focus targets',async p=>{
     const dialog=await open(p,kind), close=dialog.getByRole('button',{name:kind==='Modal'?'Close dialog':'Close drawer'});
     await close.focus(); await p.keyboard.press('Shift+Tab');
     assert.ok(await dialog.getByRole('button',{name:'Usable action'}).evaluate(e=>e===document.activeElement));
     await p.keyboard.press('Tab'); assert.ok(await close.evaluate(e=>e===document.activeElement));
   });
   await test(kind+' closing content cannot activate while animation is pending',async p=>{
     const dialog=await open(p,kind);
     await p.evaluate(()=>window.closeOverlay());
     await p.locator(kind==='Modal'?'.modal-closing':'.side-drawer-closing').waitFor();
     await dialog.evaluate(e=>e.getAnimations().forEach(a=>a.pause()));
     const action=dialog.getByRole('button',{name:'Usable action',includeHidden:true});
     assert.ok(await action.evaluate(e=>!!e.closest('[inert]')));
     await action.evaluate(e=>e.focus()); await p.keyboard.press('Enter');
     assert.equal(await p.evaluate(()=>window.actions),0);
     assert.equal(await p.evaluate(()=>document.body.style.overflow),'hidden');
     await dialog.evaluate(e=>e.getAnimations().forEach(a=>a.finish()));
     await dialog.waitFor({state:'hidden'});
     assert.equal(await p.evaluate(()=>document.body.style.overflow),'');
     assert.ok(await p.getByRole('button',{name:'Open '+kind,exact:true}).evaluate(e=>e===document.activeElement));
   });
   for(const width of [1440,1024,768,600,390]) await test(kind+' enlarged text, focus and scrolling '+width,async p=>{
     await p.addStyleTag({content:'button, input, p { font-size: 200% !important; }'});
     const dialog=await open(p,kind), box=await dialog.boundingBox();
     assert.ok(box.x>=0&&box.y>=0&&box.x+box.width<=width+1&&box.y+box.height<=851);
     assert.ok(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
     const scroller=kind==='Modal'?dialog:p.locator('.side-drawer-content');
     assert.ok(await scroller.evaluate(e=>e.scrollHeight>e.clientHeight));
     await scroller.evaluate(e=>e.scrollTop=e.scrollHeight);
     assert.ok(await scroller.evaluate(e=>e.scrollTop>0));
     await p.keyboard.press('Tab');
     assert.ok(await dialog.evaluate(e=>e.contains(document.activeElement)));
     assert.ok(await p.evaluate(()=>parseFloat(getComputedStyle(document.activeElement).outlineWidth)>0));
     await p.keyboard.press('Escape'); await dialog.waitFor({state:'hidden'});
   },{width});
   await test(kind+' reduced motion preserves keyboard dismissal and cleanup',async p=>{
     const dialog=await open(p,kind);
     assert.equal(await dialog.evaluate(e=>getComputedStyle(e).animationName),'none');
     assert.equal(await p.locator('#root').evaluate(e=>getComputedStyle(e).transform),'none');
     await p.keyboard.press('Tab'); assert.ok(await dialog.evaluate(e=>e.contains(document.activeElement)));
     await p.keyboard.press('Escape'); await dialog.waitFor({state:'hidden'});
     assert.equal(await p.evaluate(()=>document.body.style.overflow),'');
   },{width:390,motion:'reduce'});
 }
}).catch(e=>{console.error(e);process.exitCode=1});
