const path=require('path'),assert=require('assert'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'../..'),fixture=require('./fixture').state;
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});try{for(const width of [1440,390])for(const hostname of ['app.other.com','other.com']){
 const state=structuredClone(fixture),writes=[],errors=[];
 state.settings.zones=[{zone_id:'b'.repeat(32),zone_name:'example.com'},{zone_id:'c'.repeat(32),zone_name:'other.com'}];
 state.sites[0].zone_id='b'.repeat(32);
 const page=await browser.newPage({viewport:{width,height:950}});page.on('pageerror',e=>errors.push(e.message));
 await page.route('http://lb.preview/**',r=>{const p=new URL(r.request().url()).pathname;
 if(!p.startsWith('/api/'))return r.fulfill({path:path.join(root,'ui',p==='/'?'index.html':p.slice(1))});
 if(p==='/api/sites'&&r.request().method()==='POST'){
  const data=r.request().postDataJSON();writes.push(data);
  if(!data.id){assert.equal(data.zone_id,'c'.repeat(32));assert.equal(data.hostname,hostname);state.sites.push({...data,id:'secondary'});}
  else{assert.equal(data.id,'secondary');assert.equal(data.hostname,'example.com');assert.equal(data.zone_id,'b'.repeat(32));state.sites=state.sites.map(site=>site.id===data.id?{...site,...data}:site);}
  return r.fulfill({json:{...data,id:data.id||'secondary'}});
 }
 return r.fulfill({json:p==='/api/bootstrap'?{initialized:true,authenticated:true,csrf:'test'}:p==='/api/state'?state:{}});
 });
 await page.goto('http://lb.preview/');await page.locator('nav [data-view=sites]').click();
 // Both existing and new websites permit choosing an attached domain.
 await page.evaluate(()=>editSite('preview'));assert(await page.locator('#site-zone').isEnabled());assert.equal(await page.locator('#site-form [name=hostname]').evaluate(el=>el.readOnly),false);await page.locator('#cancel-site').click();
 await page.locator('#add-site').click();await page.locator('#site-dialog').waitFor();assert(await page.locator('#site-zone').isEnabled());
 await page.locator('#site-zone').selectOption('c'.repeat(32));
 await page.locator('#site-form [name=name]').fill('Secondary website');
 await page.locator('#site-form [name=hostname]').fill(hostname);
 await page.locator('#site-form [name=origin]').fill('http://127.0.0.1:9300');
 await page.locator('#site-form [name=human_check]').uncheck();
 await page.locator('#site-form button.primary').click();await page.locator('#site-dialog').waitFor({state:'hidden'});
 assert.equal(writes.length,1);
 await page.evaluate(()=>editSite('secondary'));assert.equal(await page.locator('#site-zone').inputValue(),'c'.repeat(32));
 await page.locator('#site-form [name=name]').fill('Edited secondary');await page.locator('#site-form [name=hostname]').fill('example.com');await page.locator('#site-zone').selectOption('b'.repeat(32));await page.locator('#site-form button.primary').click();await page.locator('#site-dialog').waitFor({state:'hidden'});
 assert.equal(writes.length,2);assert.equal(writes[1].zone_id,'b'.repeat(32));assert.equal(writes[1].hostname,'example.com');
 assert.deepEqual(errors,[]);await page.close();
}console.log('PASS: secondary-domain root/subdomain create/edit payloads after editing primary website, desktop/mobile');}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
