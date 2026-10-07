const fs=require('fs'),path=require('path'),assert=require('node:assert/strict'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'../..'),fixture=require('./fixture').state;
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});try{
for(const width of [1440,390])for(const authenticated of [true,false]){
 const page=await browser.newPage({viewport:{width,height:900}});let release;const pending=new Promise(resolve=>release=resolve),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.route('http://lb.preview/**',async route=>{
  const pathname=new URL(route.request().url()).pathname;
  if(!pathname.startsWith('/api/'))return route.fulfill({path:path.join(root,'ui',pathname==='/admin'?'index.html':pathname.slice(1))});
  if(pathname==='/api/bootstrap'){await pending;return route.fulfill({json:{initialized:true,authenticated,csrf:authenticated?'fixture':'',remote:true}});}
  if(pathname==='/api/state')return route.fulfill({json:fixture});
  return route.fulfill({json:{}});
 });
 await page.goto('http://lb.preview/admin',{waitUntil:'domcontentloaded'});
 await page.waitForTimeout(100);
 assert(await page.locator('body').evaluate(el=>el.classList.contains('auth-pending')));
 assert.equal(await page.locator('.auth-visual').evaluate(el=>getComputedStyle(el).visibility),'hidden','Login illustration must not flash while authentication is unknown');
 assert(!await page.locator('#auth-form').isVisible());
 assert(!await page.locator('#shell').isVisible());
 release();
 await page.waitForFunction(()=>!document.body.classList.contains('auth-pending'));
 if(authenticated){assert(await page.locator('#shell').isVisible());assert(!await page.locator('#auth').isVisible());}
 else{assert(await page.locator('#auth-form').isVisible());assert(!await page.locator('#shell').isVisible());assert.equal(await page.locator('#auth-title').textContent(),'欢迎回来');}
 assert.deepEqual(errors,[]);await page.close();
}
console.log('PASS: delayed authenticated/unauthenticated bootstrap has no login flash at desktop and mobile widths');
}finally{await browser.close();}})().catch(error=>{console.error(error);process.exit(1)});
