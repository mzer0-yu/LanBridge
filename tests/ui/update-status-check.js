const path=require('path'),assert=require('assert'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'../../ui');
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});try{for(const width of [1440,390]){
const page=await browser.newPage({viewport:{width,height:900}}),errors=[];let phase='pending';
page.on('pageerror',e=>errors.push(e.message));
await page.route('http://lb.preview/**',r=>{const p=new URL(r.request().url()).pathname;
if(!p.startsWith('/api/'))return r.fulfill({path:path.join(root,(p==='/'||p==='/admin')?'index.html':p.slice(1))});
let data={};if(p==='/api/bootstrap')data={initialized:true,authenticated:true,csrf:'test'};
if(p==='/api/state')data={...require('./fixture').state,update_status:{phase,automatic:false,frontend_revision:'one',message:phase==='current'?'':'更新待生效，请重启实例'}};
return r.fulfill({json:data});});
await page.goto('http://lb.preview/admin');await page.locator('#runtime-update-notice').waitFor();assert((await page.locator('#runtime-update-notice').textContent()).includes('请重启实例'));
phase='current';await page.locator('#refresh').click();await page.locator('#runtime-update-notice').waitFor({state:'hidden'});
assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);await page.close();}
console.log('PASS: update status, clear after recovery, desktop/mobile');}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
