// Run from any directory; all API responses are isolated test fixtures.
const fs=require('fs'),path=require('path'),assert=require('assert'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'../..'),work=path.resolve(__dirname,'../../.test-artifacts/ui');
const site={id:'preview',name:'电机控制',hostname:'mc.example.com',origin:'http://192.168.1.20:8088',enabled:true,human_check:true,passcode_required:false,allowed_countries:['CN','HK','JP','US'],allowed_ips:[],requests_per_minute:180,session_minutes:60};
const state={settings:{account_id:'a'.repeat(32),zone_id:'b'.repeat(32),zone_name:'example.com',tunnel_id:'t',turnstile_sitekey:'public-key',gateway_port:8891,admin_port:8890,tunnel_name:'LanBridge',cloudflared_path:'cloudflared.exe'},connector:{installed:true,running:true},credentials:{cf_write_token:true,tunnel_token:true,turnstile_secret:true},cloudflare_setup:{ready:true,missing:[]},cloudflare_permission_issues:[],sites:[site],published_hosts:[site.hostname],site_probes:{},token_management:{managed:{kind:'oauth'}},browser_auth:{phase:'done',message:'已连接'},audit:[]};
fs.mkdirSync(path.resolve(__dirname,'../../.test-artifacts/ui'),{recursive:true});
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[],writes=[];page.on('pageerror',e=>errors.push(e.message));await page.route('http://lb.preview/**',route=>{const p=new URL(route.request().url()).pathname;if(p.startsWith('/api/')){if(route.request().method()==='POST')writes.push({path:p,data:route.request().postDataJSON()});return route.fulfill({json:p==='/api/bootstrap'?{initialized:true,authenticated:true,csrf:'preview'}:state});}const file=p==='/'?'index.html':p.slice(1);return route.fulfill({path:path.join(root,'ui',file)})});await page.goto('http://lb.preview/');await page.locator('#sites-table tr').last().waitFor({state:'attached'});assert.equal(await page.locator('[data-view=security]').count(),0);await page.locator('[data-view=sites]').click();assert.equal(await page.locator('#page-title').textContent(),'网站转发');assert((await page.locator('#sites-table').textContent()).includes('已发布'));assert.equal(await page.locator('#sites-table th').first().evaluate(el=>getComputedStyle(el).backgroundColor),'rgba(0, 0, 0, 0)');await page.screenshot({path:path.join(work,'website-management.png')});await page.locator('#sites-table [data-edit]').click();assert.equal(await page.locator('#site-dialog .site-edit-section:visible h3').count(),2);assert.equal(await page.locator('#site-form [name=hostname]').inputValue(),site.hostname);const closeAlignment=await page.locator('#close-dialog').evaluate(el=>{const b=el.getBoundingClientRect(),s=el.querySelector('svg').getBoundingClientRect(),h=document.querySelector('#site-dialog-title').getBoundingClientRect();return {x:Math.abs(b.x+b.width/2-s.x-s.width/2),y:Math.abs(b.y+b.height/2-s.y-s.height/2),title:Math.abs(b.y+b.height/2-h.y-h.height/2)}});assert(closeAlignment.x<.1&&closeAlignment.y<.1&&closeAlignment.title<.1);assert.equal(await page.locator('#site-form [name=human_check]').isChecked(),true);await page.screenshot({path:path.join(work,'website-editor.png')});const policyGap=await page.evaluate(()=>{const previous=document.querySelector('#site-form [name=requests_per_minute]').getBoundingClientRect(),next=document.querySelector('#site-session-field').getBoundingClientRect();return next.top-previous.bottom});assert(Math.abs(policyGap-14)<1,`Session gap is ${policyGap}px`);await page.locator('#site-session-field').scrollIntoViewIfNeeded();await page.locator('#site-dialog').screenshot({path:path.join(work,'website-policy-spacing-1440.png')});await page.locator('#cancel-site').click();await page.locator('[data-view=settings]').click();assert.equal(await page.locator('#human-verification-panel').isVisible(),true);
for(const width of [1440,390]){
 await page.setViewportSize({width,height:1000});
 await page.locator('#verification-advanced').evaluate(el=>el.open=true);
 const key=page.locator('#verification-credentials-form [name=turnstile_sitekey]'),secret=page.locator('#verification-credentials-form [name=turnstile_secret]'),feedback=page.locator('#verification-feedback');
 assert.equal(await feedback.textContent(),'');
 assert.equal(await secret.evaluate(el=>el.tagName),'TEXTAREA');
 assert.equal(await page.locator('#verification-credentials-form input[type=password]').count(),0);
 assert.equal(await secret.evaluate(el=>getComputedStyle(el).webkitTextSecurity),'disc');
 assert.equal(await secret.getAttribute('autocomplete'),'off');
 // A browser may emit input/change without changing the saved value.
 await key.dispatchEvent('input');await key.dispatchEvent('change');assert.equal(await feedback.textContent(),'');
 await key.fill('  public-key  ');assert.equal(await feedback.textContent(),'');
 await key.fill('changed-public-key');assert((await feedback.textContent()).includes('未保存'));
 await page.locator('#refresh').click();await page.waitForFunction(()=>document.querySelector('#refresh').dataset.refreshState==='success');
 assert.equal(await key.inputValue(),'changed-public-key');
 await key.fill('public-key');assert.equal(await feedback.textContent(),'');
 await secret.fill('test-only-new-secret');assert.equal(await feedback.textContent(),'有未保存的修改');
 await page.locator('#human-verification-panel').screenshot({path:path.join(work,`verification-masked-${width}.png`)});
 await page.locator('#refresh').click();await page.waitForFunction(()=>document.querySelector('#refresh').dataset.refreshState==='success');assert.equal(await secret.inputValue(),'test-only-new-secret');
 await secret.fill('');assert.equal(await feedback.textContent(),'');
 await secret.fill('   ');assert.equal(await feedback.textContent(),'');await secret.fill('');
 await secret.fill('test-only-\nnew-secret\n');assert.equal(await secret.inputValue(),'test-only-new-secret');await secret.fill('');assert.equal(await feedback.textContent(),'');
 await page.locator('#verification-advanced').evaluate(el=>el.open=false);
 await page.locator('#verification-advanced>summary').click();assert.equal(await feedback.textContent(),'');
 assert.equal(await page.locator('#verification-credentials-form').getAttribute('data-dirty'),'');
 await page.locator('#human-verification-panel').screenshot({path:path.join(work,`verification-dirty-${width}.png`)});
}
// The masked key still submits correctly and is cleared after saving.
const secret=page.locator('#verification-credentials-form [name=turnstile_secret]');
await secret.fill('test-only-saved-secret');await secret.press('Enter');
await page.waitForFunction(()=>document.querySelector('#verification-feedback').textContent==='人类验证配置已保存');
assert.equal(await secret.inputValue(),'');
assert.equal(writes.filter(item=>item.path==='/api/credentials').length,0);
assert.deepEqual(writes.find(item=>item.path==='/api/settings').data,{turnstile_sitekey:'public-key',turnstile_secret:'test-only-saved-secret'});
assert.equal(writes.find(item=>item.path==='/api/settings').data.turnstile_sitekey,'public-key');
// Compare the same row before/after pausing; simulated state only.
await page.locator('[data-view=sites]').click();
for(const width of [1920,1440,760,390,320]){
 await page.setViewportSize({width,height:1000});
 const measure=()=>page.locator('#sites-table tbody tr').first().evaluate(row=>{
  const rect=el=>{const r=el.getBoundingClientRect();return {x:r.x,width:r.width,height:r.height}};
  return {row:rect(row),name:rect(row.querySelector('b')),cells:[...row.cells].map(rect),
   badge:rect(row.querySelector('[data-label="发布状态"] .badge')),button:rect(row.querySelector('[data-pause-site]')),
   leading:row.querySelector('b').getBoundingClientRect().x-row.getBoundingClientRect().x};
 });
 await page.evaluate(()=>{state.sites[0].paused=false;render()});const before=await measure();
 await page.evaluate(()=>{state.sites[0].paused=true;render()});const after=await measure();
 for(const key of ['row','name','badge','button'])for(const field of (key==='row'?['x','width']:['x','width','height']))assert(Math.abs(before[key][field]-after[key][field])<.1,`Pause changed ${key}/${field} at ${width}px`);
 for(let i=0;i<before.cells.length;i++)for(const field of ['x','width'])assert(Math.abs(before.cells[i][field]-after.cells[i][field])<.1,`Pause shifted column ${i}/${field} at ${width}px`);
 assert(after.leading>=12,`Pause marker touches site name at ${width}px`);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 await page.locator('#websites-management').screenshot({path:path.join(work,`paused-row-${width}.png`)});
}
await page.evaluate(()=>{state.sites[0].paused=false;render()});
console.log('PASS: pause/resume columns and primary control dimensions stay stable; pause settings live in the editor at 320/390/760/1440/1920px');
for(const width of [1440,390,320]){
 await page.setViewportSize({width,height:900});await page.evaluate(()=>{state.sites[0].target='website';editSite('preview')});
 assert.equal(await page.locator('#site-target-options').evaluate(el=>el.open),false);assert(await page.locator('#site-form [name=target]').isHidden());assert(await page.locator('#site-origin-field').isVisible());
 await page.locator('#site-target-options>summary').click();await page.locator('#site-forward-lanbridge').check();assert(await page.locator('#site-origin-field').isHidden());assert(await page.locator('#site-lanbridge-hint').isVisible());assert.equal(await page.locator('#site-form [name=origin]').isDisabled(),true);assert.equal(await page.locator('#site-form').evaluate(el=>new FormData(el).get('target')),'lanbridge');assert.equal(await page.locator('#site-dialog').evaluate(el=>el.scrollWidth>el.clientWidth),false);
 await page.locator('#site-forward-lanbridge').uncheck();assert(await page.locator('#site-origin-field').isVisible());assert.equal(await page.locator('#site-form [name=origin]').isDisabled(),false);assert.equal(await page.locator('#site-form').evaluate(el=>new FormData(el).get('target')),'website');await page.locator('#cancel-site').click();
 await page.evaluate(()=>{state.sites[0].target='lanbridge';editSite('preview')});assert.equal(await page.locator('#site-target-options').evaluate(el=>el.open),true);assert.equal(await page.locator('#site-form [name=target]').inputValue(),'lanbridge');assert(await page.locator('#site-forward-lanbridge').isChecked());await page.locator('#site-dialog').screenshot({path:path.join(work,`site-target-advanced-${width}.png`)});await page.locator('#cancel-site').click();
}
await page.evaluate(()=>{state.sites[0].target='website';editSite('preview')});await page.locator('#site-dialog').screenshot({path:path.join(work,'site-target-default.png')});await page.locator('#cancel-site').click();
await page.setViewportSize({width:390,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);assert.deepEqual(errors,[]);console.log('浏览器实测：网站管理、编辑分区、密钥遮罩输入、编辑/保存反馈及手机布局通过；仅使用模拟 API');await browser.close()})().catch(e=>{console.error(e);process.exit(1)});
