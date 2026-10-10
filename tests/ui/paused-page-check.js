const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{spawnSync}=require('node:child_process'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'../..'),work=path.join(root,'.test-artifacts/ui');
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});try{
 for(const width of [320,390,760,1440])for(const long of [false,true]){
  const name='电机控制';
  const hostname=long?('long-domain-label-'.repeat(3)+'.').repeat(3)+'example.com':'mc.yuboyi.me';
  const code="import json,sys;from starlette.requests import Request;from lanbridge.gateway import paused_response;r=paused_response(Request({'type':'http','method':'GET','scheme':'https','path':'/news','headers':[(b'accept',b'text/html')],'query_string':b''}),{'name':sys.argv[1],'hostname':sys.argv[2]});print(json.dumps({'status':r.status_code,'headers':dict(r.headers),'body':r.body.decode('utf-8')}))";
  const result=spawnSync(path.join(root,'.venv/Scripts/python.exe'),['-c',code,name,hostname],{cwd:root,encoding:'utf8'});
  assert.equal(result.status,0,result.stderr);const response=JSON.parse(result.stdout);
  const page=await browser.newPage({viewport:{width,height:800}});let navigations=0;
  await page.route('https://paused.preview/**',route=>{navigations++;return route.fulfill(response)});
  const loaded=await page.goto('https://paused.preview/news?view=1');assert.equal(loaded.status(),503);
  assert.equal(await page.evaluate(()=>document.characterSet),'UTF-8');
  assert.equal(await page.locator('h1').textContent(),hostname);
  assert(!response.body.includes(name),'Private site name leaked in paused HTML');
  assert.equal(await page.locator('h2').textContent(),'网站暂时不可访问');
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert.equal(await page.locator('p').textContent(),'管理员已暂停此网站，请稍后再试。');
  const button=page.locator('a');assert.equal(await button.textContent(),'重新尝试');await button.focus();assert(await button.evaluate(el=>el===document.activeElement));
  assert.equal(await button.evaluate(el=>getComputedStyle(el).outlineStyle),'solid');
  await page.screenshot({path:path.join(work,`paused-page-${width}${long?'-long':''}.png`),fullPage:true});
  await button.click();await page.waitForLoadState();assert.equal(navigations,2);assert.equal(new URL(page.url()).search,'?view=1');
  await page.close();
 }
 console.log('PASS: paused page real renderer, UTF-8, private name hidden, long domains, keyboard refresh at 320/390/760/1440px');
}finally{await browser.close()}})().catch(error=>{console.error(error);process.exit(1)});
