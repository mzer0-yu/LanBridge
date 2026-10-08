const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'../../ui');
const job={id:'done',domain:'mobomicro.com',zone_id:'c'.repeat(32),old_nameservers:['dns7.hichina.com'],new_nameservers:['one.ns.cloudflare.com'],records:[],phase:'done',message:'域名已激活并接入 LanBridge，可添加网站'};
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});try{
 for(const width of [320,390,1440]){
  const page=await browser.newPage({viewport:{width,height:950}}),errors=[];let reads=0,held=null;
  const state=structuredClone(require('./fixture').state);
  state.domain_onboarding={domain:job.domain,phase:'done'};
  state.settings.zone_name='yuboyi.me';state.settings.zones=[{zone_id:'b'.repeat(32),zone_name:'yuboyi.me'},{zone_id:'c'.repeat(32),zone_name:'mobomicro.com'}];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('http://lb.preview/**',route=>{
   const p=new URL(route.request().url()).pathname;
   if(!p.startsWith('/api/'))return route.fulfill({path:path.join(root,(p==='/'||p==='/admin')?'index.html':p.slice(1))});
   if(p==='/api/bootstrap')return route.fulfill({json:{initialized:true,authenticated:true,csrf:'test'}});
   if(p==='/api/state')return route.fulfill({json:state});
   if(p==='/api/domain-onboarding')return route.fulfill({json:{configured:true,auth_mode:'oauth',job}});
   if(p==='/api/domain-onboarding/connected-domains'){
    reads++;
    if(reads===2)return route.fulfill({status:503,json:{detail:'模拟网络失败'}});
    const data={configured:true,domains:state.settings.zones.map(z=>({...z,default:z.zone_id===state.settings.zone_id})),checked_at:1791460800};
    if(reads===4)return new Promise(resolve=>{held=()=>route.fulfill({json:data}).then(resolve)});
    return route.fulfill({json:data});
   }
   assert.equal(route.request().method(),'GET','inventory must not submit cloud mutations');
   return route.fulfill({json:{}});
  });
  await page.goto('http://lb.preview/admin');await page.locator('[data-view=audit]').click();
  await page.locator('#domain-completed-title').filter({hasText:'mobomicro.com'}).waitFor({state:'attached'});
  await page.locator('[data-view=settings]').click();await page.locator('#domain-onboarding>summary').click();
  await page.locator('#aliyun-domains-list').filter({hasText:'yuboyi.me'}).waitFor();
  assert((await page.locator('#aliyun-domains-list').textContent()).includes('mobomicro.com'));
  assert.equal(await page.locator('#aliyun-domains-list .configured-zone').count(),2);
  assert.equal(await page.locator('#domain-preview').getAttribute('open'),null);
  assert(await page.locator('#domain-preview').isHidden());
  assert.equal(await page.locator('#domain-onboarding-status').textContent(),'');
  await page.locator('[data-view=audit]').click();
  await page.locator('#domain-completed-history>summary').click();
  assert.equal(await page.locator('#domain-completed-title').textContent(),'mobomicro.com');
  assert.equal(await page.locator('#domain-completed-old-ns').textContent(),'dns7.hichina.com');
  assert.equal(await page.locator('#domain-completed-history a').getAttribute('href'),'/api/domain-onboarding/backup');
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.locator('[data-view=settings]').click();
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  fs.mkdirSync(path.resolve(__dirname,'../../.test-artifacts/ui'),{recursive:true});
  await page.screenshot({path:path.resolve(__dirname,`../../.test-artifacts/ui/aliyun-connected-domains-${width}.png`)});
  await page.locator('#aliyun-domains-refresh').click();await page.locator('#aliyun-domains-status').filter({hasText:'查询失败'}).waitFor();
  assert.equal(await page.locator('#aliyun-domains-list .configured-zone').count(),2);
  assert((await page.locator('#aliyun-domains-status').textContent()).includes('上次查询结果'));
  await page.locator('#aliyun-domains-refresh').click();await page.locator('#aliyun-domains-status').filter({hasText:'共 2 个域名'}).waitFor();
  await page.locator('#aliyun-domains-refresh').click();await page.waitForFunction(()=>document.querySelector('#aliyun-domains-refresh').disabled);
  await page.evaluate(()=>showAuth());assert(held);await held();await page.waitForTimeout(50);
  assert(await page.locator('#domain-completed-history').isHidden());assert.equal(await page.locator('#domain-completed-title').textContent(),'');
  assert.equal(await page.locator('#aliyun-domains-list').textContent(),'');assert(await page.locator('#domain-onboarding').isHidden());
  assert.deepEqual(errors,[]);await page.close();
 }
 console.log('PASS: Aliyun owned/attached list with both domains, completed task hidden with audit details/backup, failed refresh retains labelled snapshot, logout discards late response, desktop/mobile');
}finally{await browser.close()}})().catch(error=>{console.error(error);process.exitCode=1});
