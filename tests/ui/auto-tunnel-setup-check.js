const path=require('path'),assert=require('assert'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'../../ui');
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});try{for(const width of [1440,390,320]){
 const page=await browser.newPage({viewport:{width,height:950}}),errors=[],calls=[];
 let job={phase:'error',authorization_saved:true,next_action:'configure_tunnel',updated_at:Date.now()/1000,message:'Cloudflare 授权已保存，但自动配置未完成：连接超时'};
 const testState=structuredClone(require('./fixture').state);
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('http://lb.preview/**',async r=>{
  const p=new URL(r.request().url()).pathname;
  if(!p.startsWith('/api/'))return r.fulfill({path:path.join(root,p==='/admin'?'index.html':p.slice(1))});
  let data={};if(p==='/api/bootstrap')data={initialized:true,authenticated:true,csrf:'test',remote:false};
  if(p==='/api/state')data={...testState,browser_auth:job};
  if(p==='/api/local-login/browsers')data={browsers:[{id:'default',name:'系统默认浏览器'}]};
  if(r.request().method()==='POST'){calls.push(p);assert.equal(p,'/api/cloudflare/browser-authorize-setup');job={phase:'creating',authorization_saved:true,updated_at:Date.now()/1000,message:'授权已保存，正在继续配置隧道…'};data=job;}
  return r.fulfill({json:data});
 });
 await page.goto('http://lb.preview/admin');await page.locator('[data-view="settings"]').click();
 await page.locator('#account-config-details').evaluate(el=>el.open=true);
 const retry=page.locator('#browser-setup-retry');assert(await retry.isVisible());
 assert.match(await page.locator('#browser-auth-status').textContent(),/授权已保存/);
 await retry.click();await page.waitForFunction(()=>document.querySelector('#browser-auth-status').textContent.includes('正在继续配置'));
 assert(await retry.isHidden());assert(await page.locator('#browser-authorize').isDisabled());
 job={phase:'done',authorization_saved:true,tunnel_ready:true,message:'本次授权已完成，隧道连接令牌已保存。'};
 await page.locator('#refresh').click();await page.waitForFunction(()=>document.querySelector('#browser-auth-status').textContent.includes('隧道连接令牌已保存'));
 assert(await retry.isHidden());assert.deepEqual(calls,['/api/cloudflare/browser-authorize-setup']);
 await page.locator('#refresh').click();await page.waitForFunction(()=>document.querySelector('#browser-auth-status').textContent==='');
 await page.reload();await page.locator('[data-view="settings"]').click();
 assert.equal(await page.locator('#browser-auth-status').textContent(),'');
 job={phase:'error',updated_at:Date.now()/1000,message:'授权失败，请重试'};
 await page.reload();await page.locator('[data-view="settings"]').click();
 assert.equal(await page.locator('#browser-auth-status').textContent(),'授权失败，请重试');
 testState.credentials.tunnel_token=false;
 await page.locator('#refresh').click();await page.waitForFunction(()=>document.querySelector('#tunnel-maintenance').open);
 await page.locator('#account-config-details').evaluate(el=>el.open=false);
 await page.evaluate(()=>{showAuth();csrf='test';openShell();});
 await page.waitForFunction(()=>document.querySelector('#tunnel-maintenance').open&&document.querySelector('#account-config-details').open);
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);await page.close();
 }console.log('PASS: saved authorization retries tunnel setup without new consent; progress/success and 320px layout');}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
