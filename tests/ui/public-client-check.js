const fs=require('fs'),path=require('path'),assert=require('node:assert/strict'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'../..'),fixture=require('./fixture').state,work=path.join(root,'.test-artifacts/ui');fs.mkdirSync(work,{recursive:true});
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});try{
for(const width of [1440,390]){
 const page=await browser.newPage({viewport:{width,height:1000}});let enabled=true,remote=true,limited=false,writes=0;const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.route('http://lb.preview/**',async route=>{
  const pathname=new URL(route.request().url()).pathname;
  if(!pathname.startsWith('/api/'))return route.fulfill({path:path.join(root,'ui',pathname==='/admin'?'index.html':pathname.slice(1))});
  if(pathname==='/api/bootstrap')return route.fulfill({json:{initialized:true,authenticated:true,csrf:'fixture',remote,public_client_enabled:enabled}});
  if(pathname==='/api/state')return route.fulfill({json:{...fixture,public_client_enabled:enabled,access_scope:limited?'sites':'admin',access_permissions:['account']}});
  if(pathname==='/api/public-client'){enabled=route.request().postDataJSON().enabled;assert.equal(typeof enabled,'boolean');writes++;return route.fulfill({json:{enabled,saved:true}});}
  return route.fulfill({json:{}});
 });
 await page.goto('http://lb.preview/admin');await page.locator('[data-view=settings]').click();
 const input=page.locator('#public-client-form input'),save=page.locator('#public-client-form button');
 assert(await input.isChecked());assert(await page.locator('main a[href="/client"]').isVisible());
 const spacing=await page.locator('.public-client-note').evaluate(el=>({bottom:getComputedStyle(el).marginBottom,padding:getComputedStyle(el.closest('.panel')).paddingBottom}));assert.equal(spacing.bottom,'0px');
 const before=await page.locator('.public-access-panel').boundingBox(),buttonBefore=await save.boundingBox();
 await input.uncheck();await save.click();await page.getByRole('button',{name:'已保存',exact:true}).waitFor();
 const after=await page.locator('.public-access-panel').boundingBox();assert.equal(after.height,before.height);const buttonAfter=await save.boundingBox();assert.equal(buttonAfter.width,buttonBefore.width);assert.equal(buttonAfter.height,buttonBefore.height);
 await page.emulateMedia({reducedMotion:'reduce'});assert.equal(await save.evaluate(el=>getComputedStyle(el).transitionDuration),'0s');await page.emulateMedia({reducedMotion:'no-preference'});assert.equal(await page.locator('#public-client-feedback').textContent(),'');
 await page.locator('.public-access-panel').screenshot({path:path.join(work,'public-client-saved-'+width+'.png')});
 assert.equal(writes,1);assert(!await page.locator('main a[href="/client"]').isVisible());assert(!(await input.isChecked()));
 await page.reload();await page.locator('[data-view=settings]').click();assert(!(await input.isChecked()));assert(!await page.locator('main a[href="/client"]').isVisible());
 assert(await page.locator('.public-access-panel').isVisible());assert(await page.locator('#auth').isHidden());
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.locator('#view-settings').screenshot({path:path.join(work,'public-client-disabled-'+width+'.png')});
 await input.check();assert.equal(await save.textContent(),'保存');await save.click();await page.getByRole('button',{name:'已保存',exact:true}).waitFor();assert(await page.locator('main a[href="/client"]').isVisible());
 enabled=false;remote=false;await page.reload();await page.locator('[data-view=settings]').click();assert(!(await input.isChecked()));assert(await page.locator('main a[href="/client"]').isVisible());
 limited=true;await page.reload();await page.locator('[data-view=settings]').click();assert(!await page.locator('.public-access-panel').isVisible());
 assert.deepEqual(errors,[]);await page.close();
}
console.log('PASS: public-client control, immediate links, persistence, local access, temporary permission and desktop/mobile layout');
}finally{await browser.close();}})().catch(error=>{console.error(error);process.exit(1)});
