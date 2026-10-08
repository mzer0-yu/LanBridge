const path=require('path'),fs=require('fs'),assert=require('assert'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'../..'),fixture=require('./fixture').state,work=path.join(root,'.test-artifacts/ui');
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});try{for(const width of [320,390,1440]){
 const state=structuredClone(fixture),writes=[],errors=[];const page=await browser.newPage({viewport:{width,height:950}});page.on('pageerror',e=>errors.push(e.message));
 await page.route('http://lb.preview/**',r=>{const p=new URL(r.request().url()).pathname;
  if(!p.startsWith('/api/'))return r.fulfill({path:path.join(root,'ui',p==='/'?'index.html':p.slice(1))});
  if(p==='/api/sites'&&r.request().method()==='POST'){const data=r.request().postDataJSON();writes.push(data);state.sites=state.sites.map(s=>s.id===data.id?{...s,...data}:s);return r.fulfill({json:{...data,publication:{status:'published'}}});}
  return r.fulfill({json:p==='/api/bootstrap'?{initialized:true,authenticated:true,csrf:'preview'}:state});
 });
 await page.goto('http://lb.preview/');await page.locator('[data-view=sites]').click();await page.locator('[data-edit]').first().click();
 const days=page.locator('[name=human_remember_days]');assert.equal(await days.inputValue(),'1');
 await days.fill('30');await page.locator('#site-form button.primary').click();await page.locator('#site-dialog').waitFor({state:'hidden'});
 assert.equal(writes[0].human_remember_days,30);assert.equal(writes[0].session_minutes,60);
 await page.locator('[data-edit]').first().click();assert.equal(await days.inputValue(),'30');
 await page.locator('.site-dialog-body').evaluate(el=>el.scrollTop=el.scrollHeight);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 assert.equal(await page.locator('#site-form').evaluate(el=>el.scrollWidth>el.clientWidth),false);assert.deepEqual(errors,[]);
 fs.mkdirSync(work,{recursive:true});await page.screenshot({path:path.join(work,`human-memory-policy-${width}.png`)});await page.close();
}console.log('PASS: legacy one-day default, saved thirty-day memory, independent session duration and editor layout at 320/390/1440px');}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
