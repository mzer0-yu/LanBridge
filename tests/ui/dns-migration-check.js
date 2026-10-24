const path=require('node:path'),fs=require('node:fs'),assert=require('node:assert/strict'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'../..'),fixture=require('./fixture').state;
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});try{for(const width of [320,390,1440]){
 const state=structuredClone(fixture),writes=[],errors=[];let finishWrite;
 const conflict={repairable:true,detail:'域名仍指向同账户隧道“Old tunnel <img src=x onerror=alert(1)>”（已离线）。迁移将备份原记录并切换到 LanBridge，原入口将被替换。'};
 state.site_publication={id:'job-1',phase:'failed',message:'配置已保存，发布未完成：app.example.com 已有冲突 DNS，平台不会覆盖',dns_conflict:conflict};
 const page=await browser.newPage({viewport:{width,height:900}});page.on('pageerror',e=>errors.push(e.message));
 await page.route('http://lb.preview/**',async r=>{const p=new URL(r.request().url()).pathname;
  if(!p.startsWith('/api/'))return r.fulfill({path:path.join(root,'ui',p==='/'?'index.html':p.slice(1))});
  if(p==='/api/sites'&&r.request().method()==='POST'){
   writes.push(r.request().postDataJSON());await new Promise(resolve=>finishWrite=resolve);state.site_publication={id:'job-2',phase:'publishing',message:'正在迁移并发布'};
   return r.fulfill({json:{saved:true,publication:{status:'queued'},site_publication:state.site_publication}});
  }
  return r.fulfill({json:p==='/api/bootstrap'?{initialized:true,authenticated:true,csrf:'test'}:p==='/api/state'?state:{}});
 });
 await page.goto('http://lb.preview/');await page.locator('#site-publication-repair').waitFor({state:'visible'});
 assert.equal(await page.locator('#site-publication-detail img').count(),0);
 assert((await page.locator('#site-publication-detail').textContent()).includes('原入口将被替换'));
 const button=await page.locator('#site-publication-repair').boundingBox();assert(button.height>=36);
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 fs.mkdirSync(path.join(root,'.test-artifacts/ui'),{recursive:true});await page.screenshot({path:path.join(root,`.test-artifacts/ui/dns-migration-${width}.png`),fullPage:true});
 await page.locator('#site-publication-repair').click();
 await page.waitForFunction(()=>!!document.querySelector('#site-publication-repair').dataset.busy);
 await page.evaluate(()=>loadState());assert(await page.locator('#site-publication-repair').isDisabled());assert(await page.locator('#site-publication-retry').isDisabled());
 await page.evaluate(()=>{for(const id of ['site-publication-repair','site-publication-retry'])document.getElementById(id).dispatchEvent(new MouseEvent('click',{bubbles:true}));});
 assert.equal(writes.length,1);finishWrite();await page.waitForFunction(()=>document.querySelector('#site-publication-message').textContent.includes('正在迁移'));
 assert.deepEqual(writes,[{background:true,retry_publication:true,repair_dns_conflict:'job-1'}]);assert(await page.locator('#site-publication-repair').isHidden());
 state.site_publication={id:'job-3',phase:'failed',message:'DNS 冲突',dns_conflict:{...conflict,repairable:false,detail:'原隧道仍在运行，暂不提供迁移'}};await page.evaluate(()=>loadState());assert(await page.locator('#site-publication-repair').isHidden());assert(await page.locator('#site-publication-retry').isVisible());
 state.site_publication.dns_conflict.repairable=true;state.access_scope='sites';await page.evaluate(()=>loadState());assert(await page.locator('#site-publication-repair').isHidden());
 state.access_scope='admin';state.site_publication={phase:'succeeded',message:'发布完成'};await page.evaluate(()=>loadState());assert(await page.locator('#site-publication-notice').isHidden());
 assert.deepEqual(errors,[]);await page.close();
}console.log('PASS DNS conflict details, escaped text, checked migration payload, grouped request lock across refresh, busy/healthy/temporary/success states at 320/390/1440px')}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});
