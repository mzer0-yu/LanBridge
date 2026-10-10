const path=require('path'),assert=require('assert'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'../../ui');
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});try{for(const width of [1440,390]){
const page=await browser.newPage({viewport:{width,height:900}}),errors=[];let phase='pending',revision='one',documents=0;
page.on('pageerror',e=>errors.push(e.message));
await page.route('http://lb.preview/**',r=>{const p=new URL(r.request().url()).pathname;
if(r.request().isNavigationRequest())documents++;
if(!p.startsWith('/api/'))return r.fulfill({path:path.join(root,(p==='/'||p==='/admin')?'index.html':p.slice(1))});
let data={};if(p==='/api/bootstrap')data={initialized:true,authenticated:true,csrf:'test'};
if(p==='/api/state')data={...require('./fixture').state,update_status:{phase,automatic:false,frontend_revision:revision,message:phase==='current'?'':'更新待生效，请重启实例'}};
return r.fulfill({json:data});});
await page.goto('http://lb.preview/admin');await page.locator('#runtime-update-notice').waitFor();assert((await page.locator('#runtime-update-notice').textContent()).includes('请重启实例'));
phase='current';await page.locator('#refresh').click();await page.locator('#runtime-update-notice').waitFor({state:'hidden'});
phase='frontend';revision='two';await page.locator('#refresh').click();await page.locator('#runtime-update-notice').waitFor();assert.equal(await page.locator('#runtime-update-notice').textContent(),'界面已更新，请刷新页面');const before=documents;
await page.locator('#refresh').click();await page.waitForFunction(()=>document.querySelector('#last-refresh')?.textContent.includes('更新于'));await page.locator('#runtime-update-notice').waitFor({state:'hidden'});assert.equal(documents,before+1,'Update refresh must reload the document');
await page.locator('#refresh').click();await page.waitForFunction(()=>document.querySelector('#refresh').dataset.refreshState==='success');assert.equal(documents,before+1,'Current interface refresh only fetches state');
assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);await page.close();}
console.log('PASS: update status, clear after recovery, desktop/mobile');}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
