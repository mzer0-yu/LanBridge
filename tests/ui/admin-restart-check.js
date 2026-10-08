const fs=require('fs'),path=require('path'),assert=require('assert'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'../../ui');
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});try{
for(const width of [1440,760,390,320])for(const remote of [false,true]){
 const page=await browser.newPage({viewport:{width,height:950}}),errors=[];let pending=null,restarts=0,fail=true,oldRuntime=false,autoConnect=true,autoSaves=0;
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('http://lb.preview/**',r=>{const p=new URL(r.request().url()).pathname;
  if(!p.startsWith('/api/'))return r.fulfill({path:path.join(root,(p==='/'||p==='/admin')?'index.html':p.slice(1))});
  let data={};if(p==='/api/bootstrap')data={initialized:true,authenticated:true,csrf:'test',remote};
  if(p==='/api/state')data={...require('./fixture').state,pending_admin_port:pending,connector_auto_start:autoConnect,connector_auto_start_supported:!oldRuntime,admin_port_supported:!oldRuntime,restart_supported:!remote&&!oldRuntime};
  if(p==='/api/connector-auto-start'){autoSaves++;autoConnect=JSON.parse(r.request().postData()).enabled;data={enabled:autoConnect,saved:true};}
  if(p==='/api/admin-port'){pending=JSON.parse(r.request().postData()).port;data={saved:true,restart_required:true,pending_admin_port:pending,connector_auto_start:autoConnect,connector_auto_start_supported:!oldRuntime,admin_port_supported:!oldRuntime,restart_supported:!remote&&!oldRuntime};}
  if(p==='/api/restart'){restarts++;return fail?r.fulfill({status:400,json:{detail:'待生效的管理端口已被占用'}}):r.fulfill({json:{restarting:true,port:pending,url:'http://127.0.0.1:'+pending+'/admin'}});}
  return r.fulfill({json:data});
 });
 await page.goto('http://lb.preview/admin');await page.locator('#shell').waitFor();await page.locator('[data-view="settings"]').click();
 if(remote){assert(await page.locator('#sidebar-restart').isHidden());assert(await page.locator('#connector-auto-start-form input').isDisabled());assert(await page.locator('#connector-auto-start-form button').isDisabled());assert(await page.locator('[data-local-setting="admin"]').isDisabled());assert.equal(await page.locator('#restart').count(),0);}
 else{
  if(width>760){await page.locator('#sidebar-restart').click();await page.locator('#restart-dialog').waitFor();await page.locator('#restart-dialog').evaluate(dialog=>dialog.close());assert.equal(restarts,0);}
  const autoInput=page.locator('#connector-auto-start-form input');
  assert(await autoInput.isChecked());await autoInput.uncheck();await page.locator('#refresh').click();assert(!(await autoInput.isChecked()));
  await page.locator('#connector-auto-start-form button').click();await page.waitForFunction(()=>document.querySelector('#connector-auto-start-form button').textContent==='已保存');
  assert.equal(autoConnect,false);assert.equal(autoSaves,1);
  await page.reload();await page.locator('[data-view="settings"]').click();assert(!(await autoInput.isChecked()));
  const positions=()=>page.locator('.local-security-panel').evaluate(panel=>{
   const origin=panel.getBoundingClientRect();
   return ['.local-security-heading h2','.local-security-switch'].map(selector=>{
    const r=panel.querySelector(selector).getBoundingClientRect();return {x:r.x-origin.x,y:r.y-origin.y,width:r.width,height:r.height};
   });
  });
  const collapsed=await positions();
  await page.locator('.local-security-panel').screenshot({path:path.resolve(__dirname,'../../.test-artifacts/ui/local-security-collapsed-')+width+'.png'});
  for(const setting of ['admin','gateway','password']){
   await page.locator(`[data-local-setting="${setting}"]`).click();
   assert.deepEqual(await positions(),collapsed,`${setting} expands without moving header or tabs at ${width}`);
   await page.locator('#local-security-close').click();
   assert.deepEqual(await positions(),collapsed,`${setting} collapses without moving header or tabs at ${width}`);
   assert(!(await page.locator('#local-security-editor').isVisible()));
  }
  await page.locator('[data-local-setting="admin"]').click();
  const portLayout=await page.locator('#admin-port-form .gateway-port-controls').evaluate(row=>{
   const [input,button]=row.children, a=input.getBoundingClientRect(), b=button.getBoundingClientRect();
   return {height:a.height-button.getBoundingClientRect().height,top:a.top-b.top,margin:getComputedStyle(input).marginTop};
  });
  assert.equal(portLayout.margin,'0px');assert.equal(portLayout.height,0);assert.equal(portLayout.top,0);
  await page.locator('#admin-port-form input').fill('9900');await page.locator('#admin-port-form button').click();
  await page.waitForFunction(()=>document.querySelector('#admin-port-feedback').textContent.includes('下次启动'));
  assert((await page.locator('#admin-port-current').textContent()).includes('9900'));
  await page.locator('.local-security-panel').screenshot({path:path.resolve(__dirname,'../../.test-artifacts/ui/local-security-expanded-')+width+'.png'});
  assert.equal(pending,9900);assert.equal(require('./fixture').state.settings.admin_port,8890);
  await page.locator('#sidebar-restart').click();assert((await page.locator('#restart-address').textContent()).includes('9900'));
  await page.locator('#confirm-restart').click();await page.locator('#restart-error').filter({hasText:'占用'}).waitFor();assert(await page.locator('#confirm-restart').isEnabled());
  await page.locator('#cancel-restart').click();
 }
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);
 await page.screenshot({path:path.resolve(__dirname,'../../.test-artifacts/ui/admin-ports-')+width+(remote?'-remote':'')+'.png'});
 if(!remote){fail=false;await page.locator('#sidebar-restart').click();await page.locator('#confirm-restart').click();await page.locator('#platform-restarting').waitFor();assert.equal(restarts,2);if(width===390){oldRuntime=true;await page.reload();await page.locator('#shell').waitFor();await page.locator('[data-view="settings"]').click();assert(await page.locator('[data-local-setting="admin"]').isDisabled());assert(await page.locator('#sidebar-restart').isDisabled());assert((await page.locator('#local-security-note').textContent()).includes('旧版本'));}}
 await page.close();
}console.log('PASS: deferred admin port, restart confirmation/error/success, remote restriction, desktop/mobile layout');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
