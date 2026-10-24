const path=require('node:path'),fs=require('node:fs'),assert=require('node:assert/strict'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'../..'),fixture=require('./fixture').state;
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});try{for(const width of [320,390,1440]){
 const state=structuredClone(fixture),writes=[],errors=[];const id=state.sites[0].id;delete state.sites[0].origin_host_header;
 const page=await browser.newPage({viewport:{width,height:950}});page.on('pageerror',e=>errors.push(e.message));
 await page.route('http://lb.preview/**',r=>{const p=new URL(r.request().url()).pathname;
  if(!p.startsWith('/api/'))return r.fulfill({path:path.join(root,'ui',p==='/'?'index.html':p.slice(1))});
  if(p==='/api/sites'&&r.request().method()==='POST'){
   const data=r.request().postDataJSON();writes.push(data);state.sites=state.sites.map(site=>site.id===data.id?{...site,...data}:site);
   return r.fulfill({json:{...data,saved:true,publication:{status:'queued'}}});
  }
  return r.fulfill({json:p==='/api/bootstrap'?{initialized:true,authenticated:true,csrf:'test'}:p==='/api/state'?state:{}});
 });
 await page.goto('http://lb.preview/');await page.locator('#shell').waitFor();await page.evaluate(id=>editSite(id),id);
 const options=page.locator('#site-origin-options'),select=page.locator('[name=origin_host_header]');assert.equal(await select.inputValue(),'origin');
 await options.locator('summary').click();await select.selectOption('public');
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 const box=await options.boundingBox(),dialog=await page.locator('#site-dialog').boundingBox();assert(box.x>=dialog.x&&box.x+box.width<=dialog.x+dialog.width+1);
 fs.mkdirSync(path.join(root,'.test-artifacts/ui'),{recursive:true});await page.screenshot({path:path.join(root,`.test-artifacts/ui/origin-host-${width}.png`),fullPage:true});
 await page.locator('#site-form button.primary').click();await page.locator('#site-dialog').waitFor({state:'hidden'});assert.equal(writes[0].origin_host_header,'public');
 await page.evaluate(id=>editSite(id),id);assert.equal(await select.inputValue(),'public');
 await page.locator('#site-forward-lanbridge').check();assert(await options.isHidden());assert(await select.isDisabled());
 await page.locator('#site-forward-lanbridge').uncheck();await page.locator('#site-origin-input').fill('file:///D:/Web/index.html');assert(await options.isHidden());assert(await select.isDisabled());
 await page.locator('#site-origin-input').fill('http://127.0.0.1:9300');assert(await options.isVisible());assert(!(await select.isDisabled()));
 assert.deepEqual(errors,[]);await page.close();
}console.log('PASS source Host legacy default, save/reopen, static/LanBridge visibility and 320/390/1440px layout')}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});
