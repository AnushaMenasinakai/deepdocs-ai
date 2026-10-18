// In-memory Vite harness; no production test route or fixture file.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const ROOT = process.env.FRONTEND_TEST_URL || 'http://127.0.0.1:5176';
exports.assert = assert;
exports.settle = locator => locator.evaluate(async e => Promise.allSettled(e.getAnimations().map(a => a.finished)));
exports.run = async function run(name, source, tests) {
  const browser = await chromium.launch({ channel: 'msedge', headless: true }); let passed = 0;
  const html = `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/src/styles.css"></head><body><div id="root"></div><script type="module">
import RefreshRuntime from '/@react-refresh';
RefreshRuntime.injectIntoGlobalHook(window); window.$RefreshReg$=()=>{}; window.$RefreshSig$=()=>type=>type; window.__vite_plugin_react_preamble_installed__=true;
const entry=await (await fetch('/src/main.jsx')).text();
const dependency=(text,name)=>text.split('"').find(value=>value.startsWith('/node_modules/')&&value.includes(name));
const {default: React}=await import(dependency(entry,'react.js'));
const {default:{createRoot}}=await import(dependency(entry,'react-dom_client.js'));
const {default:Drawer}=await import('/src/components/Drawer.jsx');
const {default:DropdownMenu}=await import('/src/components/DropdownMenu.jsx');
const {default:Modal}=await import('/src/components/Modal.jsx');
const {default:Sidebar}=await import('/src/components/Sidebar.jsx');
const sidebarSource=await (await fetch('/src/components/Sidebar.jsx')).text();
const {MemoryRouter}=await import(dependency(sidebarSource,'react-router-dom.js'));
const h=React.createElement;
${source}
createRoot(document.getElementById('root')).render(h(React.StrictMode,null,h(Harness)));
</script></body></html>`;
  async function scenario(label, fn, options = {}) {
    const context = await browser.newContext({ viewport: { width: options.width || 1440, height: 850 }, reducedMotion: options.motion || 'no-preference' });
    const page = await context.newPage(), errors = [];
    page.on('pageerror', e => { errors.push(e.message); console.error(e.message) });
    await page.route('**/__overlay-test', route => route.fulfill({ contentType: 'text/html', body: html }));
    await page.route('**/api/**', route => { errors.push('Unexpected API request'); return route.abort() });
    try { await page.goto(ROOT + '/__overlay-test'); await page.getByTestId('ready').waitFor(); await fn(page); assert.deepEqual(errors, []); passed++; console.log('PASS ' + label) }
    finally { await context.close() }
  }
  try { await tests(scenario); console.log(`${name}: ${passed} passed, 0 failed, 0 skipped.`) }
  finally { await browser.close() }
};
