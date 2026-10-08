const path=require('path'),assert=require('assert'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'../../ui');
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});try{for(const width of [1440,390]){
const page=await browser.newPage({viewport:{width,height:1000}}),errors=[];let job={phase:'error',updated_at:1791446400,message:'浏览器授权已超时，请重新发起授权'};
page.on('pageerror',e=>errors.push(e.message));
await page.route('http://lb.preview/**',r=>{const p=new URL(r.request().url()).pathname;
if(!p.startsWith('/api/'))return r.fulfill({path:path.join(root,p==='/admin'?'index.html':p.slice(1))});
let data={};if(p==='/api/bootstrap')data={initialized:true,authenticated:true,csrf:'test',remote:false};
if(p==='/api/state')data={...require('./fixture').state,browser_auth:job};
if(p==='/api/cloudflare/browser-authorize'){job={phase:'authorizing',browser:'default',updated_at:Date.now()/1000,message:'等待确认授权'};data=job;}
return r.fulfill({json:data});});
await page.goto('http://lb.preview/admin');await page.locator('[data-view=settings]').click();await page.locator('#account-config-toggle').click();
await page.locator('#browser-auth-error-time').waitFor();
const gap=await page.locator('#browser-auth-error-time').evaluate(el=>el.getBoundingClientRect().top-document.querySelector('#browser-auth-status').getBoundingClientRect().bottom);assert(Math.abs(gap-4)<1);
await page.locator('#account-browser-path').screenshot({path:path.resolve(__dirname,'../../.test-artifacts/ui/auth-feedback-spacing-')+width+'.png'});assert((await page.locator('#browser-auth-error-time').textContent()).startsWith('发生时间：'));
await page.locator('#browser-authorize').click();await page.locator('#browser-auth-error-time').waitFor({state:'hidden'});assert.equal(await page.locator('#browser-auth-error-time').textContent(),'');
assert((await page.locator('#browser-auth-status').textContent()).includes('剩余约'));assert.deepEqual(errors,[]);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.close();
}console.log('PASS: failure timestamp and retry clears old time, countdown unchanged, desktop/mobile');}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
