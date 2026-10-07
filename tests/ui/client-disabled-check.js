const fs=require('fs'),path=require('path'),assert=require('node:assert/strict'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'../..'),work=path.join(root,'.test-artifacts/ui');fs.mkdirSync(work,{recursive:true});
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});try{
for(const width of [1440,390]){
 const page=await browser.newPage({viewport:{width,height:900}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('http://lb.preview/**',route=>new URL(route.request().url()).pathname==='/admin'?route.fulfill({contentType:'text/html; charset=utf-8',body:'<h1>管理台</h1>'}):route.fulfill({status:404,path:path.join(root,'ui/client-disabled.html'),headers:{'Content-Type':'text/html; charset=utf-8','Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"}}));
 const response=await page.goto('http://lb.preview/client');assert.equal(response.status(),404);
 assert.equal(await page.locator('h1').textContent(),'转发列表未启用');assert.equal(await page.locator('main p').textContent(),'此页面已由管理员关闭。');
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:path.join(work,'client-disabled-'+width+'.png')});
 await page.locator('a').focus();assert(await page.locator('a').evaluate(el=>el===document.activeElement));
 await page.locator('a').click();assert.equal(new URL(page.url()).pathname,'/admin');assert.deepEqual(errors,[]);await page.close();
}
console.log('PASS: disabled client page UTF-8 text, desktop/mobile layout and keyboard-accessible admin link');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
