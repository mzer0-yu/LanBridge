const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'../../ui');
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});try{
  for(const width of [320,390,1440]){
    const page=await browser.newPage({viewport:{width,height:900}}),errors=[];
    const state=structuredClone(require('./fixture').state);
    const first={zone_id:'b'.repeat(32),zone_name:'example.com'},second={zone_id:'c'.repeat(32),zone_name:'other.com'};
    state.settings.zones=[first,second];state.sites[0].hostname='app.other.com';state.sites[0].zone_id=second.zone_id;state.published_hosts=['app.other.com'];
    let blocked=true,defaultFailure=true;const writes=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.route('http://lb.preview/**',route=>{
      const p=new URL(route.request().url()).pathname;
      if(!p.startsWith('/api/'))return route.fulfill({path:path.join(root,(p==='/'||p==='/admin')?'index.html':p.slice(1))});
      if(p==='/api/bootstrap')return route.fulfill({json:{initialized:true,authenticated:true,csrf:'test'}});
      if(p==='/api/state')return route.fulfill({json:state});
      if(route.request().method()==='POST')writes.push(p);
      if(p.startsWith('/api/zones/')&&p.endsWith('/default')){
        if(defaultFailure)return route.fulfill({status:400,json:{detail:'域名不在已接入列表中，请刷新后重试'}});
        const zone=state.settings.zones.find(z=>z.zone_id===p.split('/')[3]);
        Object.assign(state.settings,zone);return route.fulfill({json:{settings:state.settings,saved:true}});
      }
      if(p.startsWith('/api/zones/')&&p.endsWith('/remove')){
        if(blocked)return route.fulfill({status:400,json:{detail:'域名仍被网站或已发布路由使用，不能移除：admin.example.com'}});
        const id=p.split('/')[3];state.settings.zones=state.settings.zones.filter(z=>z.zone_id!==id);
        Object.assign(state.settings,state.settings.zones[0]||{zone_id:'',zone_name:''});
        state.cloudflare_setup.ready=!!state.settings.zone_id;
        return route.fulfill({json:{settings:state.settings,saved:true}});
      }
      return route.fulfill({json:{}});
    });
    await page.goto('http://lb.preview/admin');await page.locator('[data-view="settings"]').click();
    const selector=`[data-remove-zone="${first.zone_id}"]`;
    await page.locator(selector).waitFor();assert.equal(await page.locator('#configured-zones [data-remove-zone]').count(),2);
    const setSecond=`[data-default-zone="${second.zone_id}"]`;
    assert.equal(await page.locator('#configured-zones [data-default-zone]').count(),1);
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    fs.mkdirSync(path.resolve(__dirname,'../../.test-artifacts/ui'),{recursive:true});
    await page.screenshot({path:path.resolve(__dirname,`../../.test-artifacts/ui/default-zone-choice-${width}.png`)});
    await page.locator(setSecond).click();await page.locator('#zones-feedback').filter({hasText:'请刷新后重试'}).waitFor();
    assert.equal(state.settings.zone_id,first.zone_id);
    defaultFailure=false;await page.locator(setSecond).click();
    await page.locator('#zones-feedback').filter({hasText:'默认域名已设为 other.com'}).waitFor();
    assert.equal(state.sites[0].zone_id,second.zone_id);assert.deepEqual(state.published_hosts,['app.other.com']);
    assert.equal(await page.locator(setSecond).count(),0);
    await page.locator(`[data-default-zone="${first.zone_id}"]`).click();
    await page.locator('#zones-feedback').filter({hasText:'默认域名已设为 example.com'}).waitFor();
    await page.locator(selector).click();await page.locator('#zones-feedback').filter({hasText:'admin.example.com'}).waitFor();
    assert.equal(await page.locator('#configured-zones .configured-zone').count(),2);
    assert.equal(await page.locator('#account-config-details').getAttribute('open'),null);
    blocked=false;await page.locator(selector).click();
    await page.locator('#zones-feedback').filter({hasText:'默认域名已改为 other.com'}).waitFor();
    assert.equal(state.sites.length,1);assert.equal(state.published_hosts.length,1);
    assert((await page.locator('#configured-zones').textContent()).includes('默认域名'));
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    fs.mkdirSync(path.resolve(__dirname,'../../.test-artifacts/ui'),{recursive:true});
    await page.screenshot({path:path.resolve(__dirname,`../../.test-artifacts/ui/default-zone-removal-${width}.png`)});
    // Simulate the user resolving the remaining site/routes before a final removal.
    state.sites=[];state.published_hosts=[];
    await page.locator(`[data-remove-zone="${second.zone_id}"]`).click();
    await page.locator('#zones-feedback').filter({hasText:'默认选择已清空'}).waitFor();
    assert.equal(await page.locator('#configured-zones [data-remove-zone]').count(),0);
    assert.deepEqual(writes,[`/api/zones/${second.zone_id}/default`,`/api/zones/${second.zone_id}/default`,`/api/zones/${first.zone_id}/default`,`/api/zones/${first.zone_id}/remove`,`/api/zones/${first.zone_id}/remove`,`/api/zones/${second.zone_id}/remove`]);
    assert.deepEqual(errors,[]);await page.close();
  }
  console.log('PASS: default zone selection/failure feedback, unchanged site associations, removal guards and desktop/mobile layout');
}finally{await browser.close()}})().catch(error=>{console.error(error);process.exitCode=1});
