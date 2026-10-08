const fs=require('fs'),path=require('path'),assert=require('assert'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'../..'),fixture=require('./fixture').state;
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});try{
for(const width of [320,390,760,900,1440,1920]){
 const page=await browser.newPage({viewport:{width,height:1000}}),errors=[];
 const domain='long-domain-for-layout-check.example.com';
 const job={id:'test-job',domain,phase:'preview',old_nameservers:['dns7.hichina.com','dns8.hichina.com'],new_nameservers:['ariadne.ns.cloudflare.com','pedro.ns.cloudflare.com'],records:[],message:'准备完成'};
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('http://lb.preview/**',r=>{const p=new URL(r.request().url()).pathname;
 if(!p.startsWith('/api/'))return r.fulfill({path:path.join(root,'ui',p==='/'?'index.html':p.slice(1))});
 return r.fulfill({json:p==='/api/bootstrap'?{initialized:true,authenticated:true,csrf:'test'}:p==='/api/state'?{...fixture,domain_onboarding:{domain,phase:job.phase}}:p==='/api/domain-onboarding'?{configured:true,auth_mode:'oauth',job}: {}});
 });
 await page.goto('http://lb.preview/');await page.locator('#domain-task-notice').waitFor();
 const notice=await page.locator('#domain-task-notice').evaluate(el=>{const rect=n=>n.getBoundingClientRect(),c=getComputedStyle(el),r=rect(el),title=rect(el.querySelector('strong')),p=rect(el.querySelector('p')),b=rect(el.querySelector('button'));return{padding:c.padding,titleGap:p.top-title.bottom,buttonGap:b.top-p.bottom,bottomGap:r.bottom-b.bottom,scroll:el.scrollWidth,client:el.clientWidth};});
 await page.locator('#domain-task-notice').screenshot({path:path.join(root,`.test-artifacts/ui/domain-task-notice-${width}.png`)});
 await page.locator('#domain-task-open').click();await page.locator('#domain-confirm').waitFor();
 const clipped=await page.locator('#domain-onboarding>.advanced-body').evaluate(el=>{const r=el.getBoundingClientRect(),c=getComputedStyle(el),right=r.right-parseFloat(c.paddingRight);return [...el.children].map(n=>({id:n.id||n.tagName,width:n.getBoundingClientRect().width,right:n.getBoundingClientRect().right-right})).filter(n=>n.right>1);});
 assert.deepEqual(clipped,[],`Clipped onboarding content at ${width}px`);
 assert(Math.abs(notice.titleGap-10)<1,`Title spacing at ${width}px: ${notice.titleGap}`);
 assert(Math.abs(notice.buttonGap-10)<1,`Button spacing at ${width}px: ${notice.buttonGap}`);
 assert.equal(notice.scroll,notice.client,`Notice overflow at ${width}px`);
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`Page overflow at ${width}px`);
 await page.locator('#domain-onboarding').screenshot({path:path.join(root,`.test-artifacts/ui/domain-task-expanded-${width}.png`)});
 // Preview, waiting and failure bodies must not accumulate element margins.
 for(const phase of ['preview','waiting','uncertain','blocked']){
  job.phase=phase;job.message=phase==='blocked'?'模拟迁移失败，请核对任务记录后重试。':'模拟等待状态';
  await page.reload();await page.locator('#domain-task-open').click();await page.locator('#domain-preview').waitFor();
  const gaps=await page.locator('#domain-preview>.advanced-body').evaluate(el=>{
   const children=[...el.children].filter(n=>n.getBoundingClientRect().height>0);
   return children.slice(1).map((n,i)=>n.getBoundingClientRect().top-children[i].getBoundingClientRect().bottom);
  });
  for(const gap of gaps)assert(Math.abs(gap-12)<1,`${phase} preview gap ${gap}px at ${width}px`);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${phase} overflow at ${width}px`);
  await page.locator('#domain-onboarding').screenshot({path:path.join(root,`.test-artifacts/ui/domain-task-${phase}-${width}.png`)});
 }
 await page.locator('nav [data-view=sites]').click();
 // A maximum-length DNS name must wrap within the global reminder.
 await page.locator('nav [data-view=sites]').click();
 await page.evaluate(()=>{state.domain_onboarding.domain=('a'.repeat(63)+'.').repeat(3)+'b'.repeat(61)+'.com';domainOnboardingUI.renderNotice();});
 assert(await page.locator('#domain-task-notice').evaluate(el=>el.scrollWidth===el.clientWidth),`Long-domain reminder overflow at ${width}px`);
 console.log(`PASS: domain reminder spacing, long name and expanded preview at ${width}px`);
 assert.deepEqual(errors,[]);await page.close();
}
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
