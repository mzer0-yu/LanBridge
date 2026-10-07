const fs=require('fs'),path=require('path'),assert=require('assert'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'../../ui');
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});try{for(const width of [1440,390]){
const page=await browser.newPage({viewport:{width,height:900}}),errors=[];let running=false,port=8891,retries=0;
page.on('pageerror',e=>errors.push(e.message));
await page.route('http://lb.preview/**',r=>{const p=new URL(r.request().url()).pathname;
if(!p.startsWith('/api/'))return r.fulfill({path:path.join(root,(p==='/'||p==='/admin')?'index.html':p.slice(1))});
let data={};if(p==='/api/bootstrap')data={initialized:true,authenticated:true,csrf:'test'};
if(p==='/api/state')data={...require('./fixture').state,gateway:{running,port,error:running?'':'网关端口被占用'}};
if(p==='/api/gateway-retry'){retries++;running=true;data={running:true,port};}
return r.fulfill({json:data});});
await page.goto('http://lb.preview/admin');await page.locator('#overview-connector-detail').filter({hasText:'网关端口被占用'}).waitFor();
await page.locator('[data-view="settings"]').click();await page.locator('[data-local-setting="gateway"]').click();
assert((await page.locator('#gateway-runtime-status').textContent()).includes('转发暂不可用'));
await page.locator('#gateway-retry').click();await page.waitForFunction(()=>document.querySelector('#gateway-port-feedback').textContent.includes('已启动'));
assert.equal(retries,1);assert(await page.locator('#gateway-retry').isHidden());
assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);
await page.screenshot({path:path.resolve(__dirname,'../../.test-artifacts/ui/gateway-recovery-')+width+'.png'});
await page.close();}console.log('PASS: gateway failure remains visible, local retry recovers, desktop/mobile layout');}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
