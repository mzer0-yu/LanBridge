const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'../..'),fixture=require('./fixture').state;
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});try{
for(const width of [320,760,1440]){
 const page=await browser.newPage({viewport:{width,height:900}}),errors=[];
 const state=structuredClone(fixture);
 state.sites[0].name='长网站名称'.repeat(20);
 state.sites[0].hostname='long-subdomain-name-for-layout.example.com';
 state.sites[0].origin='http://192.168.1.20:8088/'+ 'long-path/'.repeat(18);
 state.audit=[{id:1,at:Date.now()/1000,action:'site_saved',detail:{name:state.sites[0].name,hostname:state.sites[0].hostname}}];
 state.audit_storage={count:1,size_bytes:4096,limit_mb:10};
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('http://lb.preview/**',route=>{const p=new URL(route.request().url()).pathname;
 if(!p.startsWith('/api/'))return route.fulfill({path:path.join(root,'ui',p==='/'?'index.html':p.slice(1))});
 return route.fulfill({json:p==='/api/bootstrap'?{initialized:true,authenticated:true,csrf:'fixture'}:p==='/api/state'?state:p==='/api/temporary-tokens'?{tokens:[]}: {}});
 });
 await page.goto('http://lb.preview/');
 for(const view of ['overview','sites','interfaces','audit','tokens','settings']){
  await page.locator(`nav [data-view=${view}]`).click();
  await page.locator(`#view-${view}`).waitFor();
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${view} page overflow at ${width}`);
  const unexpected=await page.locator(`#view-${view}`).evaluate(el=>[...el.querySelectorAll('button,input,select,textarea')].filter(n=>n.getBoundingClientRect().width>0&&!n.matches('#temporary-token-value,.temporary-token-revealed')).filter(n=>!getComputedStyle(n).fontFamily.includes('Segoe UI')).map(n=>({tag:n.tagName,id:n.id,font:getComputedStyle(n).fontFamily})));
  assert.deepEqual(unexpected,[],`${view} inconsistent form fonts at ${width}`);
  // Inspect expanded controls too; inherited sizing can differ inside disclosures.
  await page.locator(`#view-${view} details`).evaluateAll(nodes=>nodes.forEach(n=>n.open=true));
  const controls=await page.locator(`#view-${view} button.primary,#view-${view} button.secondary`).evaluateAll(nodes=>nodes.filter(n=>n.getBoundingClientRect().width).map(n=>{const c=getComputedStyle(n),r=n.getBoundingClientRect();return {id:n.id||n.textContent.trim(),font:c.fontSize,line:c.lineHeight,radius:c.borderRadius,height:r.height}}));
  for(const control of controls){assert.equal(control.font,'13px',`${view}/${control.id} font at ${width}`);assert.equal(control.line,'20px',`${view}/${control.id} line height at ${width}`);assert.equal(control.radius,'6px',`${view}/${control.id} radius at ${width}`);assert(control.height>=(width<=760?40:36),`${view}/${control.id} height ${control.height} at ${width}`);}
  if(view==='settings'){
   const update=await page.locator('#connector-check-update').boundingBox(),save=await page.locator('#connector-auto-start-form button').boundingBox();
   assert.equal(save.width,update.width,`Connector action widths at ${width}`);assert.equal(save.height,update.height,`Connector action heights at ${width}`);assert.equal(save.x+save.width,update.x+update.width,`Connector actions right alignment at ${width}`);
   await page.locator('.connector-software>.advanced-options').evaluate(n=>n.open=false);
   await page.locator('.connector-software').screenshot({path:path.join(root,`.test-artifacts/ui/connector-controls-${width}.png`)});
  }
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${view} expanded page overflow at ${width}`);
 }
 assert.deepEqual(errors,[]);await page.close();
}
for(const width of [320,390,760,1440]){
 const page=await browser.newPage({viewport:{width,height:900}}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.route('http://lb.preview/**',route=>{
  const p=new URL(route.request().url()).pathname;
  if(p==='/api/client/routes')return route.fulfill({json:{updated_at:1,routes:[{name:'长网站名称'.repeat(18),hostname:'long-subdomain.example.com',origin:'http://192.168.1.20:8080/'+ 'long-path/'.repeat(15),status:'已暂停',published:true}]}});
  return route.fulfill({path:path.join(root,'ui',p==='/client'?'client.html':p.slice(1))});
 });
 await page.goto('http://lb.preview/client');await page.locator('.route').waitFor();
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`Client page overflow at ${width}`);
 const style=await page.locator('#refresh').evaluate(n=>{const c=getComputedStyle(n);return {font:c.fontSize,line:c.lineHeight,height:n.getBoundingClientRect().height,family:c.fontFamily};});
 assert.equal(style.font,'13px');assert.equal(style.line,'20px');assert(style.height>=(width<=760?40:36));assert(style.family.includes('Segoe UI'));
 assert.equal(await page.locator('.public-url').getAttribute('href'),null);
 await page.screenshot({path:path.join(root,`.test-artifacts/ui/client-layout-${width}.png`)});
 assert.deepEqual(errors,[]);await page.close();
}
console.log('PASS: six pages with long names/URLs at 320/760/1440px; expanded button sizes/fonts, connector action alignment and client page overflow');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
