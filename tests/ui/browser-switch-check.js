const path=require('path'),assert=require('assert'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'../../ui');
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});try{for(const width of [1440,390]){
 const page=await browser.newPage({viewport:{width,height:950}}),errors=[],calls=[];let job={phase:'authorizing',browser:'default',updated_at:Date.now()/1000,message:'请在浏览器确认授权'};
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('http://lb.preview/**',async r=>{
  const p=new URL(r.request().url()).pathname;
  if(!p.startsWith('/api/'))return r.fulfill({path:path.join(root,p==='/admin'?'index.html':p.slice(1))});
  let data={};if(p==='/api/bootstrap')data={initialized:true,authenticated:true,csrf:'test',remote:false};
  if(p==='/api/state')data={...require('./fixture').state,browser_auth:job};
  if(p==='/api/local-login/browsers')data={browsers:[{id:'default',name:'系统默认浏览器'},{id:'chrome',name:'Chrome'}]};
  if(p==='/api/cloudflare/browser-authorize-restart'){const body=r.request().postDataJSON();calls.push(body);await new Promise(resolve=>setTimeout(resolve,200));job={...job,browser:body.browser,updated_at:Date.now()/1000};data=job;}
  return r.fulfill({json:data});
 });
 await page.goto('http://lb.preview/admin');await page.locator('[data-view="settings"]').click();
 await page.locator('#cloudflare-browser option[value="chrome"]').waitFor({state:'attached'});
 await page.locator('#cloudflare-browser').selectOption('chrome');assert(await page.locator('#cloudflare-browser').isDisabled());
 await page.waitForFunction(()=>!document.querySelector('#cloudflare-browser').disabled);
 assert.deepEqual(calls,[{browser:'chrome'}]);assert.equal(await page.locator('#cloudflare-browser').inputValue(),'chrome');
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);await page.close();
 }console.log('PASS: active browser switch restarts selected Chrome consent, busy lock, desktop/mobile');}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
