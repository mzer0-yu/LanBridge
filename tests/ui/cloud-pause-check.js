// Run from any directory; all API responses are isolated test fixtures.
const fs=require('fs'),path=require('path'),assert=require('assert'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'../..'),work=path.resolve(__dirname,'../../.test-artifacts/ui');
const site={id:'preview',name:'电机控制',hostname:'mc.example.com',origin:'http://192.168.1.20:8088',enabled:true,human_check:true,passcode_required:false,allowed_countries:['CN','HK','JP','US'],allowed_ips:[],requests_per_minute:180,session_minutes:60};
const state={settings:{account_id:'a'.repeat(32),zone_id:'b'.repeat(32),zone_name:'example.com',tunnel_id:'t',turnstile_sitekey:'public-key',gateway_port:8891,admin_port:8890,tunnel_name:'LanBridge',cloudflared_path:'cloudflared.exe'},connector:{installed:true,running:true},credentials:{cf_write_token:true,tunnel_token:true,turnstile_secret:true},cloudflare_setup:{ready:true,missing:[]},cloudflare_permission_issues:[],sites:[site],published_hosts:[site.hostname],site_probes:{},token_management:{managed:{kind:'oauth',scopes:['zone-waf.write']}},browser_auth:{phase:'done',message:'已连接'},audit:[]};
fs.mkdirSync(path.resolve(__dirname,'../../.test-artifacts/ui'),{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'msedge'});
 const page=await browser.newPage();const errors=[],writes=[],authRequests=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('http://lb.preview/**',route=>{
  const p=new URL(route.request().url()).pathname;
  if(p.startsWith('/api/')){
   if(p==='/api/cloudflare/browser-authorize'){authRequests.push(route.request().postDataJSON());return route.fulfill({json:{phase:'authorizing'}});}
   if(p.endsWith('/pause')&&route.request().method()==='POST'){
    const data=route.request().postDataJSON();writes.push(data);
    site.paused=data.paused;
    if(data.cloudflare||state.site_pause?.preview){
     site.paused=true;state.site_pause={preview:{desired:data.paused&&data.cloudflare,paused:data.paused,phase:'queued',message:data.paused?'本机已暂停，正在设置云端阻断':'正在解除云端阻断，完成后恢复转发'}};
    }
    return route.fulfill({json:{...site,site_pause:state.site_pause}});
   }
   return route.fulfill({json:p==='/api/bootstrap'?{initialized:true,authenticated:true,csrf:'preview'}:state});
  }
  return route.fulfill({path:path.join(root,'ui',p==='/'?'index.html':p.slice(1))});
 });
 for(const width of [1440,390,320]){
  site.paused=false;state.site_pause={};await page.setViewportSize({width,height:900});await page.goto('http://lb.preview/');
  await page.locator('[data-view=sites]').click();await page.locator('[data-pause-site]').click();
  assert(await page.locator('#site-pause-authorize').isHidden());
  assert(await page.locator('#pause-dialog').isVisible());assert(!(await page.locator('#pause-cloud').isChecked()));
  assert.equal(await page.locator('#pause-host').textContent(),site.hostname);
  assert((await page.locator('#pause-cloud-hint').textContent()).includes('日常暂停无需勾选'));assert((await page.locator('#pause-cloud-hint').textContent()).includes('持续异常高流量'));
  assert.equal(await page.locator('#pause-dialog').evaluate(el=>el.scrollWidth>el.clientWidth),false);
  const layout=await page.locator('#pause-dialog').evaluate(el=>{const rect=q=>el.querySelector(q).getBoundingClientRect(),title=rect('h2'),host=rect('#pause-host'),label=rect('.check'),hint=rect('#pause-cloud-hint');return {width:el.getBoundingClientRect().width,hostGap:host.top-title.bottom,hintGap:hint.top-label.bottom,labelMargin:getComputedStyle(el.querySelector('.check')).marginTop,padding:getComputedStyle(el).paddingLeft}});
  assert(layout.width<=520,`Pause confirmation too wide: ${layout.width}`);assert.equal(layout.hostGap,6);assert.equal(layout.hintGap,6);assert.equal(layout.labelMargin,'0px');assert.equal(layout.padding,width===1440?'24px':'16px');
  await page.locator('#pause-dialog').screenshot({path:path.join(work,`cloud-pause-dialog-${width}.png`)});
  assert.equal(await page.locator('#pause-cloud').evaluate(el=>getComputedStyle(el).boxShadow),'none');
  await page.locator('#cancel-pause').focus();await page.keyboard.press('Shift+Tab');assert.equal(await page.locator('#pause-cloud').evaluate(el=>el===document.activeElement),true);assert.equal(await page.locator('#pause-cloud').evaluate(el=>getComputedStyle(el).outlineWidth),'2px');assert.equal(await page.locator('#pause-cloud').evaluate(el=>getComputedStyle(el).boxShadow),'none');
  const n=writes.length;await page.locator('#cancel-pause').click();assert.equal(writes.length,n);
  await page.locator('[data-pause-site]').click();await page.locator('#confirm-pause').click();
  await page.waitForFunction(()=>state.sites[0].paused===true);
  assert.deepEqual(writes.at(-1),{paused:true,cloudflare:false});
  await page.locator('[data-pause-site]').click();await page.waitForFunction(()=>state.sites[0].paused===false);
  await page.locator('[data-pause-site]').click();await page.locator('#pause-cloud').check();await page.locator('#confirm-pause').click();
  await page.waitForFunction(()=>state.site_pause?.preview?.phase==='queued');
  assert.deepEqual(writes.at(-1),{paused:true,cloudflare:true});assert(await page.locator('[data-pause-site]').isDisabled());
  await page.locator('[data-pause-details]').click();assert(await page.locator('#site-pause-apply').isDisabled());if(width===1440){const offset=await page.evaluate(()=>{const a=document.querySelector('#site-pause-feedback').getBoundingClientRect(),b=document.querySelector('#site-pause-apply').getBoundingClientRect();return Math.abs(a.y+a.height/2-b.y-b.height/2)});assert(offset<1,`Pending feedback alignment shifted ${offset}px`);}await page.locator('#site-dialog').screenshot({path:path.join(work,`pause-editor-pending-${width}.png`)});await page.locator('#cancel-site').click();
  const pendingHeight=await page.locator('#sites-table tbody tr').evaluate(el=>el.getBoundingClientRect().height);
  state.site_pause.preview={desired:true,phase:'failed',message:'需要 Zone WAF 编辑权限；本机仍暂停，云端结果待核对'};
  await page.evaluate(async()=>await loadState());
  assert((await page.locator('.site-publication-badge').getAttribute('aria-label')).includes('云端操作未完成')); assert.equal(await page.locator('[data-pause-settings], [data-cloud-pause-retry]').count(),0);assert.equal(await page.locator('#sites-table tbody tr').evaluate(el=>el.getBoundingClientRect().height),pendingHeight);
  await page.locator('#websites-management').screenshot({path:path.join(work,`cloud-pause-failed-${width}.png`)});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert.equal(await page.locator('[data-pause-details]').evaluate(el=>el.tagName),'BUTTON');assert((await page.locator('[data-pause-details]').textContent()).includes('已暂停'));await page.locator('[data-pause-details]').click({position:{x:8,y:8}});assert(await page.locator('#site-pause-section').isVisible());assert.equal(await page.locator('#site-pause-section').evaluate(el=>el===document.activeElement),true);assert.equal(await page.locator('#site-dialog').evaluate(el=>el.scrollWidth>el.clientWidth),false);await page.locator('#site-dialog').screenshot({path:path.join(work,`pause-editor-${width}.png`)});await page.locator('#site-pause-error-details>summary').click();assert((await page.locator('#site-pause-error-message').textContent()).includes('Zone WAF'));assert.equal(await page.locator('#site-dialog').evaluate(el=>el.scrollWidth>el.clientWidth),false);await page.locator('#site-pause-error-details>summary').click();await page.locator('#site-pause-apply').click();await page.waitForFunction(()=>state.site_pause?.preview?.phase==='queued');
  assert.deepEqual(writes.at(-1),{paused:true,cloudflare:true});assert(await page.locator('#site-pause-apply').isDisabled());assert(await page.locator('#site-pause-cloud').isDisabled());await page.locator('#cancel-site').click();
  state.site_pause.preview={desired:true,phase:'succeeded',message:'Cloudflare 已阻断公网访问'};await page.evaluate(async()=>await loadState());assert.equal(await page.locator('#sites-table tbody tr').evaluate(el=>el.getBoundingClientRect().height),pendingHeight);
  await page.locator('[data-pause-site]').click();await page.waitForFunction(()=>state.site_pause.preview.desired===false);
  assert.deepEqual(writes.at(-1),{paused:false,cloudflare:false});assert(await page.locator('[data-pause-site]').isDisabled());
 }

 site.paused=true;state.site_pause={};await page.evaluate(async()=>await loadState());
 await page.locator('[data-edit]').click();assert(await page.locator('#site-pause-section').isVisible());
 assert(!(await page.locator('#site-pause-cloud').isChecked()));await page.locator('#site-pause-cloud').check();await page.locator('#site-pause-apply').click();
 await page.waitForFunction(()=>state.site_pause?.preview?.phase==='queued');assert.deepEqual(writes.at(-1),{paused:true,cloudflare:true});await page.locator('#cancel-site').click();
 state.site_pause.preview={desired:true,paused:true,phase:'succeeded',message:'Cloudflare 已阻断公网访问'};await page.evaluate(async()=>await loadState());
 await page.locator('[data-edit]').click();assert(await page.locator('#site-pause-cloud').isChecked());assert(await page.locator('#site-pause-authorize').isHidden());await page.evaluate(async()=>await loadState());assert(await page.locator('#site-pause-authorize').isHidden());await page.evaluate(()=>renderTemporaryAccess());assert(await page.locator('#site-pause-authorize').isHidden());await page.locator('#site-pause-cloud').uncheck();await page.locator('#site-pause-apply').click();
 await page.waitForFunction(()=>state.site_pause?.preview?.phase==='queued');assert.deepEqual(writes.at(-1),{paused:true,cloudflare:false});await page.locator('#cancel-site').click();assert(site.paused);
 state.site_pause.preview={desired:false,paused:true,phase:'failed',message:'云端结果待核对；本机仍暂停'};await page.evaluate(async()=>await loadState());
 await page.locator('[data-edit]').click();await page.locator('#site-pause-apply').click();await page.waitForFunction(()=>state.site_pause?.preview?.phase==='queued');assert.deepEqual(writes.at(-1),{paused:true,cloudflare:false});await page.locator('#cancel-site').click();
 state.site_pause.preview={desired:false,paused:true,phase:'succeeded',message:'云端阻断已解除，仅本机暂停'};await page.evaluate(async()=>await loadState());
 assert.equal(await page.locator('[data-pause-settings], [data-cloud-pause-retry]').count(),0);assert((await page.locator('[data-pause-site]').textContent()).includes('恢复转发'));
 await page.locator('#websites-management').screenshot({path:path.join(work,'pause-mode-adjusted-320.png')});
 console.log('PASS: adjust both directions while remaining locally paused, including failed removal retry');

 state.token_management.managed.scopes=[];
 state.site_pause.preview={desired:true,paused:true,phase:'failed',message:'需要 Zone WAF 编辑权限；本机仍暂停，云端结果待核对'};
 state.cloudflare_permission_issues=[{status:'needs_recheck',credential:'cf_write_token',detail:'Cloudflare API HTTP 403：设置网站云端阻断失败',checked_at:1700000000}];
 await page.evaluate(async()=>await loadState());
 await page.locator('[data-edit]').click();assert((await page.locator('#site-pause-feedback').textContent()).includes('当前授权缺少 WAF 权限'));assert(await page.locator('#site-pause-apply').isHidden());await page.locator('#site-dialog').screenshot({path:path.join(work,'pause-editor-320.png')});await page.locator('#cancel-site').click();
 await page.locator('[data-view=overview]').click();assert(await page.locator('#cloudflare-permission-alert').isHidden());
 await page.locator('[data-view=settings]').click();assert(await page.locator('#account-permission-notice').isHidden());
 assert.equal(await page.locator('#account-config-status').textContent(),'已连接');
 await page.locator('[data-view=sites]').click();await page.locator('[data-edit]').click();assert(await page.locator('#site-pause-authorize').isVisible());
 state.token_management.managed.scopes=['zone-waf.write'];await page.evaluate(async()=>await loadState());assert(await page.locator('#site-pause-authorize').isHidden());assert(await page.locator('#site-pause-apply').isVisible());state.token_management.managed.scopes=[];
 state.cloudflare_permission_issues=[];await page.evaluate(async()=>await loadState());assert(await page.locator('#site-pause-authorize').isVisible());await page.locator('#site-pause-authorize').click();assert.equal(authRequests.at(-1).require_waf,true);assert(await page.locator('#site-dialog').isHidden());
 state.site_pause.preview={desired:true,paused:true,phase:'succeeded',automatic:true,message:'已自动在 Cloudflare 阻断公网访问'};state.token_management.managed.scopes=['zone-waf.write'];await page.evaluate(async()=>await loadState());await page.locator('[data-view=sites]').click();await page.locator('[data-edit]').click();assert((await page.locator('#site-pause-feedback').textContent()).includes('已自动'));assert(await page.locator('#site-pause-authorize').isHidden());await page.locator('#cancel-site').click();
 assert.deepEqual(errors,[]);await browser.close();console.log('PASS: optional cloud pause dialog, local-only choice, asynchronous state, retry and resume at 320/390/1440px');
})().catch(e=>{console.error(e);process.exit(1)});
