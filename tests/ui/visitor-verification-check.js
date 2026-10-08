const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {spawnSync}=require('node:child_process');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'../..');
const generated=spawnSync(path.join(root,'.venv/Scripts/python.exe'),['-c',
  "from types import SimpleNamespace; from lanbridge.gateway import gate_page; service=SimpleNamespace(settings=lambda:{'turnstile_sitekey':'fake-public-test-key'},store=SimpleNamespace(secret=lambda key:'fake-test-secret')); print(gate_page(service,{'name':'手机访问验证与长名称测试'*5,'human_check':True,'passcode_required':False}).body.decode())"
],{cwd:root,encoding:'utf8',env:{...process.env,PYTHONIOENCODING:'utf-8'}});
assert.equal(generated.status,0,generated.stderr);
const html=generated.stdout;
const script="window.turnstile={reset(){window.resets=(window.resets||0)+1}};const w=document.querySelector('.cf-turnstile');const b=document.createElement('div');b.style.cssText='height:65px;width:'+(w.dataset.size==='compact'?'150':'300')+'px;background:#eee';w.append(b);";
(async()=>{
  const browser=await chromium.launch({headless:true,channel:'msedge'});
  try{
    for(const width of [320,390,760,1440]){
      const page=await browser.newPage({viewport:{width,height:900}}),errors=[];
      let requests=0,loads=0;
      page.on('pageerror',e=>errors.push(e.message));
      await page.addInitScript(()=>{Object.defineProperty(AbortSignal,'timeout',{value:undefined})});
      await page.route('**/*',route=>{
        const url=new URL(route.request().url());
        if(url.hostname==='challenges.cloudflare.com')return route.fulfill({contentType:'application/javascript',body:script});
        if(url.pathname==='/.lanbridge/verify'){
          requests++;assert.equal(JSON.parse(route.request().postData()).token,requests===1?'initial-token':'fresh-token');
          return requests===1?route.fulfill({status:503,body:''}):route.fulfill({json:{verified:true}});
        }
        loads++;return route.fulfill({contentType:'text/html',body:html});
      });
      await page.goto('https://visitor.preview/deep/path?keep=1');
      await page.waitForFunction(()=>window.turnstile);
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      assert.equal(await page.locator('.cf-turnstile').getAttribute('data-size'),width===320?'compact':null);
      await page.evaluate(()=>window.onHumanVerified('initial-token'));
      await page.locator('#error').filter({hasText:'有效结果'}).waitFor();
      assert.equal(requests,1);assert.equal(await page.locator('#continue').isEnabled(),true);
      await page.locator('#continue').click();assert.equal(requests,1);
      await page.evaluate(()=>window.onHumanVerified('fresh-token'));
      await page.locator('#verify-status').filter({hasText:'点击重试验证'}).waitFor();
      assert.equal(requests,1,'reset must not cause an automatic request loop');
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      fs.mkdirSync(path.join(root,'.test-artifacts/ui'),{recursive:true});
      await page.screenshot({path:path.join(root,`.test-artifacts/ui/visitor-verification-${width}.png`)});
      await page.locator('#continue').click();
      await page.waitForFunction(()=>!document.querySelector('#verify-status').textContent);
      assert.equal(requests,2);assert.equal(loads,2);assert.deepEqual(errors,[]);
      await page.close();
    }
    const page=await browser.newPage({viewport:{width:390,height:900}}),errors=[];
    let loads=0,requests=0;
    page.on('pageerror',e=>errors.push(e.message));
    await page.route('**/*',route=>{
      if(new URL(route.request().url()).hostname==='challenges.cloudflare.com')return route.abort();
      if(route.request().method()==='POST')requests++;
      loads++;return route.fulfill({contentType:'text/html',body:html});
    });
    await page.goto('https://visitor.preview/');
    await page.locator('#error').filter({hasText:'组件加载失败'}).waitFor();
    await page.locator('#continue').click();await page.waitForLoadState('load');
    assert.equal(loads,2);assert.equal(requests,0);assert.deepEqual(errors,[]);await page.close();
    console.log('PASS: visitor gate at 320/390/760/1440px, empty response, fresh-token retry, no automatic retry loop, old timeout API and blocked widget script');
  }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
