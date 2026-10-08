const fs=require('fs'),path=require('path'),assert=require('assert'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'../../ui');
const preview={id:'test-job',domain:'new.example.net',zone_id:'c'.repeat(32),old_nameservers:['dns1.hichina.com','dns2.hichina.com'],new_nameservers:['one.ns.cloudflare.com','two.ns.cloudflare.com'],records:[{type:'TXT',name:'new.example.net',content:'<img src=x onerror="window.injected=true">'+('long-value'.repeat(20)),ttl:600}],phase:'preview',message:'准备完成，请核对记录并确认迁移'};
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});try{
 for(const width of [1440,390,320])for(const empty of [false,true]){
  const page=await browser.newPage({viewport:{width,height:950}}),errors=[];let job=null,prepared=0,confirmed=0,credentials=0,statusReads=0,authMode='manual';
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('http://lb.preview/**',r=>{
   const p=new URL(r.request().url()).pathname;
   if(!p.startsWith('/api/'))return r.fulfill({path:path.join(root,(p==='/'||p==='/admin')?'index.html':p.slice(1))});
   let data={};if(p==='/api/bootstrap')data={initialized:true,authenticated:true,csrf:'test',remote:false};
   if(p==='/api/state')data={...require('./fixture').state,access_scope:'admin',domain_onboarding:job?{domain:job.domain,phase:job.phase}:null};
   if(p==='/api/domain-onboarding'){statusReads++;data={configured:true,job};}
   if(p==='/api/domain-onboarding/credentials'){credentials++;data={configured:true,job};}
   if(p==='/api/domain-onboarding/oauth'){authMode='oauth';data={configured:true,job};}
   if(p==='/api/domain-onboarding/prepare'){prepared++;job=JSON.parse(JSON.stringify(preview));if(empty)job.records=[];data={configured:true,job};}
   if(p==='/api/domain-onboarding/confirm'){assert.deepEqual(r.request().postDataJSON(),{id:preview.id,confirmed_domain:preview.domain});confirmed++;job={...job,phase:'waiting',message:'变更任务已提交，正在等待 Cloudflare 激活'};data={configured:true,job};}
   if(p==='/api/domain-onboarding/check'){job={...job,phase:'done',message:'域名已激活并接入 LanBridge，可添加网站'};data={configured:true,job};}
   if(p.startsWith('/api/domain-onboarding'))data.auth_mode=authMode;
   return r.fulfill({json:data});
  });
  await page.goto('http://lb.preview/admin');await page.locator('#shell').waitFor();await page.locator('[data-view="settings"]').click();
  await page.locator('#domain-onboarding>summary').click();await page.waitForFunction(()=>document.querySelector('#domain-credential-status').textContent==='RAM 凭据已保存');
  assert.equal(await page.locator('#domain-auth-details').evaluate(el=>el.open),false);
  assert.equal(prepared,0);assert.equal(confirmed,0);assert.equal(credentials,0);
  assert.equal(await page.locator('#domain-credentials [type=password]').count(),0);
  await page.locator('#domain-credentials').evaluate(form=>{for(let parent=form.parentElement;parent;parent=parent.parentElement)if(parent.tagName==='DETAILS')parent.open=true;});
  await page.locator('#domain-credentials [name=access_key_id]').fill('test-key');await page.locator('#domain-credentials [name=access_key_secret]').fill('test-secret');
  await page.locator('#domain-credentials button').click();await page.waitForFunction(()=>document.querySelector('#domain-onboarding-status').textContent==='阿里云凭据已保存');
  assert.equal(await page.locator('#domain-credentials [name=access_key_secret]').inputValue(),'');
  await page.locator('#domain-credentials').evaluate(form=>form.closest('details').open=false);
  await page.locator('#domain-auth-details>summary').click();
  await page.locator('#domain-use-browser').click();await page.locator('#domain-oauth').click();await page.waitForFunction(()=>document.querySelector('#domain-credential-status').textContent==='已授权');
  assert.equal(await page.locator('#domain-auth-details').evaluate(el=>el.open),false);
  assert.equal(await page.locator('#domain-onboarding-status').textContent(),'');
  await page.locator('#domain-auth-details>summary').click();
  assert.equal(await page.locator('#domain-oauth').textContent(),'重新授权 ↗');
  assert(!(await page.locator('#domain-auth-description').textContent()).includes('安装'));
  assert.equal(await page.locator('#domain-use-browser').getAttribute('aria-pressed'),'true');
  await page.locator('#domain-use-ram').focus();await page.keyboard.press('Enter');
  assert(await page.locator('#domain-credentials').isVisible());
  assert(await page.locator('#domain-browser-path').isHidden());
  assert.equal(credentials,1);assert.equal(authMode,'oauth');
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert(await page.evaluate(()=>document.querySelector('#aliyun-connected-domains').getBoundingClientRect().top>=document.querySelector('#domain-auth-details').getBoundingClientRect().bottom));
  fs.mkdirSync(path.resolve(__dirname,'../../.test-artifacts/ui'),{recursive:true});
  await page.locator('#domain-auth-details').screenshot({path:path.resolve(__dirname,`../../.test-artifacts/ui/aliyun-account-manual-${width}.png`)});
  await page.locator('#domain-credentials [name=access_key_id]').fill('unsaved-key');
  await page.locator('#domain-use-browser').click();
  assert(await page.locator('#domain-credentials').isHidden());
  await page.locator('#domain-use-ram').click();
  assert.equal(await page.locator('#domain-credentials [name=access_key_id]').inputValue(),'unsaved-key');
  await page.locator('#domain-use-browser').click();
  fs.mkdirSync(path.resolve(__dirname,'../../.test-artifacts/ui'),{recursive:true});
  await page.waitForTimeout(200);
  await page.screenshot({path:path.resolve(__dirname,`../../.test-artifacts/ui/aliyun-account-auth-${width}.png`)});
  await page.locator('#domain-auth-details>summary').click();
  assert.equal(prepared,0);assert.equal(confirmed,0);
  await page.locator('#domain-prepare input').fill('new.example.net');await page.locator('#domain-prepare button').click();await page.locator('#domain-confirm').waitFor();
  assert.equal(await page.locator('#domain-onboarding-status').textContent(),'尚未切换 DNS，域名尚未完成接入。请核对预览，点击“确认接入并切换 DNS”继续。');
  assert.equal(prepared,1);assert.equal(confirmed,0);assert.equal(await page.locator('#domain-records img').count(),0);assert(!(await page.evaluate(()=>window.injected)));
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:path.resolve(__dirname,'../../.test-artifacts/ui/domain-onboarding-preview-')+width+(empty?'-empty':'')+'.png',fullPage:true});
  await page.locator('[data-view="sites"]').click();
  assert(await page.locator('#domain-task-notice').isVisible());
  assert((await page.locator('#domain-task-message').textContent()).includes('尚未切换 DNS'));
  assert.equal(await page.locator('#settings-nav-status').isHidden(),false);
  await page.reload();await page.locator('#shell').waitFor();
  assert(await page.locator('#domain-task-notice').isVisible());
  await page.locator('#domain-task-open').click();await page.locator('#domain-confirm').waitFor();
  assert(await page.locator('#domain-task-notice').isHidden());assert.equal(confirmed,0);
  assert.equal(await page.locator('#domain-confirm-fields').isHidden(),empty);
  assert.equal(await page.locator('#domain-confirm-note').isHidden(),!empty);
  assert.equal(await page.locator('#domain-confirm [name=confirmed_domain]').evaluate(el=>el.required),!empty);
  assert.equal(await page.locator('#domain-confirm [name=acknowledged]').evaluate(el=>el.required),!empty);
  assert.equal(await page.locator('#domain-confirm [type=submit]').textContent(),'确认接入并切换 DNS');
  if(empty){assert((await page.locator('#domain-confirm-note').textContent()).includes(preview.domain));}
  else{
   await page.locator('#domain-confirm [type=submit]').click();assert.equal(confirmed,0);
   await page.locator('#domain-confirm [name=confirmed_domain]').fill('new.example.net');
   await page.locator('#domain-confirm [type=submit]').click();assert.equal(confirmed,0);
   await page.locator('#domain-confirm [name=acknowledged]').check();
  }
  await page.locator('#domain-confirm [type=submit]').click();
  await page.locator('#domain-tracking').waitFor();assert.equal(await page.locator('#domain-onboarding-status').textContent(),'DNS 切换已提交，正在等待生效及 Cloudflare 激活。');assert.equal(confirmed,1);assert(await page.locator('#domain-prepare button').isDisabled());
  await page.locator('[data-view=sites]').click();assert((await page.locator('#domain-task-message').textContent()).includes('正在等待生效'));await page.locator('#domain-task-open').click();
  await page.locator('#domain-check').click();await page.waitForFunction(()=>document.querySelector('#domain-preview').hidden&&document.querySelector('#domain-onboarding-status').textContent==='');
  await page.locator('[data-view=audit]').click();assert.equal(await page.locator('#domain-completed-title').textContent(),preview.domain);
  assert(await page.locator('#domain-tracking').isHidden());assert.equal(confirmed,1);await page.locator('[data-view=sites]').click();assert(await page.locator('#domain-task-notice').isHidden());
  await page.evaluate(()=>showAuth());assert(await page.locator('#domain-onboarding').isHidden());assert.equal(await page.locator('#domain-records').textContent(),'');
  assert.deepEqual(errors,[]);await page.close();
 }
 // A result arriving after logout cannot restore records, secrets or the visible panel.
 const page=await browser.newPage();let pending=null;
 await page.route('http://lb.preview/**',r=>{
  const p=new URL(r.request().url()).pathname;
  if(!p.startsWith('/api/'))return r.fulfill({path:path.join(root,(p==='/'||p==='/admin')?'index.html':p.slice(1))});
  if(p==='/api/bootstrap')return r.fulfill({json:{initialized:true,authenticated:true,csrf:'test'}});
  if(p==='/api/state')return r.fulfill({json:require('./fixture').state});
  if(p==='/api/domain-onboarding')return new Promise(resolve=>{pending=()=>r.fulfill({json:{configured:true,job:preview}}).then(resolve);});
  return r.fulfill({json:{}});
 });
 await page.goto('http://lb.preview/admin');await page.locator('#shell').waitFor();await page.locator('[data-view="settings"]').click();await page.locator('#domain-onboarding>summary').click();
 await page.waitForTimeout(100);assert(pending);await page.evaluate(()=>showAuth());await pending();await page.waitForTimeout(100);
 assert(await page.locator('#domain-onboarding').isHidden());assert.equal(await page.locator('#domain-records').textContent(),'');await page.close();
 // Remote and temporary-account sessions never expose migration controls.
 for(const [remote,scope] of [[true,'admin'],[false,'sites']]){
  const page=await browser.newPage();await page.route('http://lb.preview/**',r=>{
   const p=new URL(r.request().url()).pathname;if(!p.startsWith('/api/'))return r.fulfill({path:path.join(root,(p==='/'||p==='/admin')?'index.html':p.slice(1))});
   return r.fulfill({json:p==='/api/bootstrap'?{initialized:true,authenticated:true,csrf:'test',remote}:p==='/api/state'?{...require('./fixture').state,access_scope:scope,access_permissions:['account']}:{} });
  });await page.goto('http://lb.preview/admin');await page.locator('#shell').waitFor();assert(await page.locator('#domain-onboarding').isHidden());await page.close();
 }
 console.log('PASS: domain onboarding desktop/mobile, empty-domain simplified confirmation, existing-record validation, credential clearing, XSS escaping, stale session and access boundaries');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
