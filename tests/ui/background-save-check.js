const path=require('path'),assert=require('assert'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'../..'),fixture=require('./fixture').state;
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});try{for(const width of [1440,390]){
 const state=structuredClone(fixture),writes=[],errors=[];let finishWrite;state.site_publication=null;
 const page=await browser.newPage({viewport:{width,height:900}});page.on('pageerror',e=>errors.push(e.message));
 await page.route('http://lb.preview/**',async r=>{const p=new URL(r.request().url()).pathname;
 if(!p.startsWith('/api/'))return r.fulfill({path:path.join(root,'ui',p==='/'?'index.html':p.slice(1))});
 if(p==='/api/sites'&&r.request().method()==='POST'){
  const data=r.request().postDataJSON();writes.push(data);assert.equal(data.background,true);
  if(data.retry_publication){assert.deepEqual(data,{background:true,retry_publication:true});state.site_publication={phase:'publishing',saved:true,message:'配置已保存，正在发布 DNS 和网站路由并核验'};}
  else{await new Promise(resolve=>finishWrite=resolve);state.sites.push({...data,id:'new'});state.site_publication={phase:'verification',saved:true,message:'配置已保存，正在同步人类验证配置'};}
  return r.fulfill({json:{saved:true,publication:{status:'queued'},site_publication:state.site_publication}});
 }
 return r.fulfill({json:p==='/api/bootstrap'?{initialized:true,authenticated:true,csrf:'test'}:p==='/api/state'?state:{}});
 });
 await page.goto('http://lb.preview/');await page.locator('nav [data-view=sites]').click();await page.locator('#add-site').click();await page.locator('#site-dialog').waitFor();
 await page.locator('#site-form [name=name]').fill('Async test');await page.locator('#site-form [name=hostname]').fill('new.example.com');await page.locator('#site-form [name=origin]').fill('http://127.0.0.1:9300');
 await page.locator('#site-form button.primary').click();await page.waitForFunction(()=>document.querySelector('#site-form button.primary').disabled);
 await page.evaluate(()=>{const form=document.querySelector('#site-form');form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));form.querySelector('button.primary').disabled=false;form.requestSubmit(form.querySelector('button.primary'));});
 assert.equal(writes.length,1);finishWrite();await page.locator('#site-dialog').waitFor({state:'hidden'});
 assert.equal(await page.locator('#site-form [name=name]').isEnabled(),true);
 assert(await page.locator('#site-publication-notice').isVisible());assert((await page.locator('#site-publication-message').textContent()).includes('正在同步'));
 assert.equal(writes.length,1);
 state.site_publication={phase:'failed',saved:true,message:'配置已保存，发布未完成：模拟网络失败'};await page.evaluate(()=>loadState());
 assert(await page.locator('#site-publication-retry').isVisible());await page.locator('#site-publication-retry').click();await page.waitForFunction(()=>document.querySelector('#site-publication-message').textContent.includes('正在发布 DNS'));
 assert.equal(writes.length,2);assert.equal(state.sites.filter(s=>s.id==='new').length,1);
 state.site_publication={phase:'succeeded',saved:true,message:'配置已保存，后台发布已完成'};await page.evaluate(()=>loadState());assert(await page.locator('#site-publication-retry').isHidden());assert(await page.locator('#site-publication-notice').isHidden());await page.evaluate(()=>render());assert(await page.locator('#site-publication-notice').isHidden());await page.reload();await page.locator('#shell').waitFor();assert(await page.locator('#site-publication-notice').isHidden());
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.evaluate(()=>showAuth());assert(await page.locator('#site-publication-notice').isHidden());
 assert.deepEqual(errors,[]);await page.close();
}console.log('PASS: background save feedback, failure/retry, no duplicate save, completion and logout, desktop/mobile');}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
