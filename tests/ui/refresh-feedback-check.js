const assert=require('node:assert/strict'),path=require('node:path');
const {chromium}=require('playwright');
const {state}=require('./fixture');
(async()=>{
  const browser=await chromium.launch({headless:true,channel:'msedge'});
  try{
    for(const width of [1440,390]){
      const page=await browser.newPage({viewport:{width,height:900}});
      let pending=null,requests=0;
      async function waitForRequest(){
        const deadline=Date.now()+5000;
        while(!pending&&Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,10));
        assert(pending,'A refresh request must arrive within five seconds');
      }
      await page.route('http://lb.preview/**',async route=>{
        const p=new URL(route.request().url()).pathname;
        if(!p.startsWith('/api/'))return route.fulfill({path:path.resolve(__dirname,'../../ui',p==='/'?'index.html':p.slice(1))});
        if(p==='/api/bootstrap')return route.fulfill({json:{initialized:true,authenticated:true,csrf:'test',remote:false}});
        if(p==='/api/state'){
          requests++;
          if(requests===3)return route.fulfill({json:state}); // Instant repeat refresh.
          if(requests>1){pending=route;return;}
          return route.fulfill({json:state});
        }
        return route.fulfill({json:{}});
      });
      await page.goto('http://lb.preview/');
      await page.locator('#last-refresh').filter({hasText:'更新于'}).waitFor();
      const button=page.locator('#refresh');
      const before=await button.boundingBox();
      await button.click();
      await page.waitForFunction(()=>document.querySelector('#refresh').dataset.refreshState==='loading');
      assert(await button.isDisabled());
      assert.equal(await button.getAttribute('aria-busy'),'true');
      assert.equal(await button.evaluate(el=>getComputedStyle(el).cursor),'progress');
      await waitForRequest();
      await pending.fulfill({json:state});pending=null;
      await page.waitForFunction(()=>document.querySelector('#refresh').dataset.refreshState==='success');
      assert.equal(await button.textContent(),'✓');
      assert(!(await button.isDisabled()));
      assert.deepEqual(await button.boundingBox(),before);
      assert(!(await page.locator('#operation-feedback').isVisible()));
      // A success indication must not impose a two-second cooldown.
      assert.equal(await button.evaluate(el=>getComputedStyle(el).cursor),'pointer');
      const repeatStart=Date.now();
      await button.click();
      await page.waitForFunction(()=>document.querySelector('#refresh').dataset.refreshState==='loading');
      assert.equal(await button.evaluate(el=>getComputedStyle(el).cursor),'progress');
      await page.waitForFunction(()=>document.querySelector('#refresh').dataset.refreshState==='success');
      assert(Date.now()-repeatStart>=450,'Even an instant response must visibly transition before the checkmark returns');
      assert.equal(requests,3,'Clicking the checkmark must send another state request');
      await page.waitForFunction(()=>document.querySelector('#refresh').dataset.refreshState==='idle');
      await button.click();
      await waitForRequest();
      await pending.fulfill({status:503,json:{detail:'测试服务不可用'}});pending=null;
      await page.waitForFunction(()=>document.querySelector('#refresh').dataset.refreshState==='error');
      assert(await page.locator('#state-refresh-warning').isVisible());
      assert.match(await page.locator('#state-refresh-warning').textContent(),/测试服务不可用/);
      assert(!(await page.locator('#operation-feedback').isVisible()));
      await button.click();
      await waitForRequest();
      await pending.fulfill({json:state});pending=null;
      await page.waitForFunction(()=>document.querySelector('#refresh').dataset.refreshState==='success');
      assert(!(await page.locator('#state-refresh-warning').isVisible()));
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      await page.close();
    }
    console.log('PASS: refresh loading, success/reset, failure/retry, stable desktop/mobile layout');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
