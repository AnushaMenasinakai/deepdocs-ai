// In-memory harness only: no test route, fixture page or API calls in production.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const ROOT = process.env.FRONTEND_TEST_URL || 'http://127.0.0.1:5176';
async function contrast(locator) {
 return locator.evaluate(element => {
  const style = getComputedStyle(element);
  const luminance = color => color.match(/[\d.]+/g).slice(0,3).map(Number).map(v => {v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
  const a=luminance(style.color),b=luminance(style.backgroundColor);
  return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
 });
}
const html = `<!doctype html><html><head><link rel="stylesheet" href="/src/styles.css"></head><body><div id="root"></div><script type="module">
import RefreshRuntime from '/@react-refresh';
RefreshRuntime.injectIntoGlobalHook(window); window.$RefreshReg$ = () => {}; window.$RefreshSig$ = () => type => type; window.__vite_plugin_react_preamble_installed__ = true;
const entry = await (await fetch('/src/main.jsx')).text();
const dependency = name => entry.split('"').find(value => value.startsWith('/node_modules/') && value.includes(name));
const {default: React} = await import(dependency('react.js'));
const {default: {createRoot}} = await import(dependency('react-dom_client.js'));
const {default: Modal} = await import('/src/components/Modal.jsx');
const {default: ConfirmDialog} = await import('/src/components/ConfirmDialog.jsx');
const h = React.createElement;
window.confirmations = 0; window.cancellations = 0; window.backgroundClicks = 0;
function Harness() {
 const [mode,setMode] = React.useState(''), [pending,setPending] = React.useState(false), [mounted,setMounted] = React.useState(true);
 window.closeModal = () => setMode(''); window.unmountModal = () => setMounted(false);
 window.reopen = () => setMode('modal');
 return h('div', {style:{minHeight:'1800px',padding:'20px'}},
 h('button',{onClick:()=>setMode('modal')},'Open modal'),
 h('button',{onClick:()=>setMode('confirm')},'Open confirmation'),
 h('button',{onClick:()=>window.backgroundClicks++},'Background action'),
 mounted && h(Modal,{open:mode==='modal',onClose:()=>setMode(''),title:'Example form',description:'Form description'},
 h('label',null,'Name',h('input',{name:'name'})), h('textarea',{'aria-label':'Notes'}),h('button',null,'Last control'),
 h('div',{style:{height:'1000px'}},'Long content')),
 mounted && h(ConfirmDialog,{open:mode==='confirm',title:'Delete example?',description:'This cannot be undone.',destructive:true,pending,onCancel:()=>{window.cancellations++;setMode('')},onConfirm:()=>{window.confirmations++;setPending(true)}}));
}
createRoot(document.getElementById('root')).render(h(React.StrictMode,null,h(Harness)));
</script></body></html>`;
(async () => {
 const browser = await chromium.launch({channel:'msedge',headless:true}); let passed=0;
 async function test(name, run, width=1440, reducedMotion='no-preference') {
  const context=await browser.newContext({viewport:{width,height:800},reducedMotion});
  const page=await context.newPage(), errors=[];
  page.on('pageerror',e=>{errors.push(e.message);console.error(e.message)});
  await page.route('**/__modal-test',r=>r.fulfill({contentType:'text/html',body:html}));
  await page.route('**/api/**',r=>{errors.push('Unexpected API');return r.abort()});
  try {await page.goto(ROOT+'/__modal-test');await page.getByText('Open modal',{exact:true}).waitFor();await run(page);assert.deepEqual(errors,[]);passed++;console.log('PASS '+name)} finally {await context.close()}
 }
 const modal=p=>p.getByRole('dialog',{name:'Example form'});
 async function open(p){await p.getByText('Open modal',{exact:true}).click();await modal(p).waitFor();await modal(p).evaluate(async e=>Promise.allSettled(e.getAnimations().map(a=>a.finished)))}
 async function closed(p){await modal(p).waitFor({state:'hidden'});assert.equal(await p.evaluate(()=>document.body.style.overflow),'');assert.equal(await p.locator('#root').getAttribute('class'),'')}
 try {
  await test('open, accessible title/description, close and focus restoration',async p=>{await open(p);assert.equal(await modal(p).getAttribute('aria-describedby')!==null,true);assert.ok(await p.locator('.modal-title').first().evaluate(e=>e===document.activeElement));await modal(p).getByRole('button',{name:'Close dialog'}).click();await closed(p);assert.ok(await p.getByText('Open modal',{exact:true}).evaluate(e=>e===document.activeElement))});
  await test('Escape dismissal',async p=>{await open(p);await p.keyboard.press('Escape');await closed(p)});
  await test('Tab and Shift+Tab containment with form controls',async p=>{await open(p);await p.keyboard.press('Shift+Tab');assert.ok(await p.getByRole('button',{name:'Last control'}).evaluate(e=>e===document.activeElement));await p.keyboard.press('Tab');assert.ok(await modal(p).getByRole('button',{name:'Close dialog'}).evaluate(e=>e===document.activeElement));await p.keyboard.press('Tab');assert.ok(await p.getByRole('textbox',{name:'Name',exact:true}).evaluate(e=>e===document.activeElement))});
  await test('backdrop blocks pointers and keyboard, locks scroll',async p=>{await open(p);await p.mouse.click(10,10);assert.equal(await p.evaluate(()=>window.backgroundClicks),0);await p.getByText('Background action').evaluate(e=>e.focus());assert.ok(await modal(p).evaluate(e=>e.contains(document.activeElement)));assert.equal(await p.evaluate(()=>document.body.style.overflow),'hidden');await p.keyboard.press('Escape');await closed(p)});
  await test('cancel never confirms and destructive dialog starts on Cancel',async p=>{await p.getByText('Open confirmation').click();assert.ok(await p.getByRole('button',{name:'Cancel',exact:true}).evaluate(e=>e===document.activeElement));await p.getByRole('button',{name:'Cancel',exact:true}).click();await p.getByRole('dialog').waitFor({state:'hidden'});assert.equal(await p.evaluate(()=>window.confirmations),0);assert.equal(await p.evaluate(()=>window.cancellations),1)});
  await test('explicit confirmation only, pending locks close/cancel/duplicate confirm',async p=>{await p.getByText('Open confirmation').click();assert.ok(await contrast(p.getByRole('button',{name:'Confirm',exact:true}))>=4.5);await p.getByRole('button',{name:'Confirm',exact:true}).dblclick();assert.equal(await p.evaluate(()=>window.confirmations),1);for(const b of await p.getByRole('dialog').getByRole('button').all())assert.ok(await b.isDisabled());await p.keyboard.press('Escape');assert.ok(await p.getByRole('dialog').isVisible());assert.equal(await p.evaluate(()=>window.cancellations),0);assert.ok(await p.getByRole('status').isVisible());assert.ok(await contrast(p.getByRole('button',{name:'Working…'}))>=4.5);await p.keyboard.press('Tab');assert.ok(await p.getByRole('dialog').evaluate(e=>e.contains(document.activeElement)))});
  for(const width of [1440,768,390]) await test('responsive long content and no overflow '+width,async p=>{await open(p);const b=await modal(p).boundingBox();assert.ok(b.x>=0&&b.y>=0&&b.x+b.width<=width&&b.y+b.height<=800);assert.ok(await modal(p).evaluate(e=>e.scrollHeight>e.clientHeight));assert.ok(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await modal(p).evaluate(e=>e.scrollTop=e.scrollHeight);assert.ok(await modal(p).evaluate(e=>e.scrollTop>0))},width);
  await test('animation, backdrop opacity and scoped background effects',async p=>{await open(p);assert.equal(await modal(p).evaluate(e=>getComputedStyle(e).animationDuration),'0.24s');assert.equal(await modal(p).evaluate(e=>getComputedStyle(e,'::backdrop').backgroundColor),'rgba(0, 0, 0, 0.45)');assert.equal(await p.locator('#root').evaluate(e=>getComputedStyle(e).filter),'blur(3px)');await p.evaluate(()=>window.closeModal());await closed(p)});
  await test('reduced motion removes modal and background animation',async p=>{await open(p);assert.equal(await modal(p).evaluate(e=>getComputedStyle(e).animationName),'none');assert.equal(await p.locator('#root').evaluate(e=>getComputedStyle(e).transform),'none');await p.keyboard.press('Escape');await closed(p)},390,'reduce');
  await test('unmount restores scroll and scoped background',async p=>{await open(p);await p.evaluate(()=>window.unmountModal());await closed(p)});
  await test('rapid controlled close/reopen retains correct scroll lifetime',async p=>{await open(p);await p.evaluate(()=>window.closeModal());await p.locator('.modal-closing').waitFor();await p.evaluate(()=>window.reopen());await modal(p).waitFor();assert.equal(await p.evaluate(()=>document.body.style.overflow),'hidden');await p.keyboard.press('Escape');await closed(p)});
  console.log('Modal foundations: '+passed+' passed, 0 failed, 0 skipped.');
 } finally {await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
