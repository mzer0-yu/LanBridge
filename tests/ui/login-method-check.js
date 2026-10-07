// Run from any directory; all API responses are isolated test fixtures.
const fs=require('fs'),path=require('path'),assert=require('assert'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'../..'),work=path.resolve(__dirname,'../../.test-artifacts/ui');
const sandbox={fixture:require('./fixture').state,fixtureState:require('./fixture').state};
fs.mkdirSync(path.resolve(__dirname,'../../.test-artifacts/ui'),{recursive:true});
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});try{
for(const width of [1440,900,390]){
const page=await browser.newPage({viewport:{width,height:900}}),errors=[];let initialized=true,attempts=0;page.on('pageerror',e=>errors.push(e.message));
await page.route('http://lb.preview/**',route=>{const p=new URL(route.request().url()).pathname;
if(!p.startsWith('/api/'))return route.fulfill({path:path.join(root,'ui',p==='/'?'index.html':p.slice(1))});
if(p==='/api/bootstrap')return route.fulfill({json:{initialized,authenticated:false,csrf:''}});
if(p==='/api/token-login'){attempts++;if(route.request().postDataJSON().token==='invalid')return route.fulfill({status:401,json:{detail:'令牌无效或已到期'}});assert.equal(route.request().postDataJSON().token,'lb_tmp_TEST_ONLY');return route.fulfill({json:{csrf:'fixture',permissions:['sites']}});}
if(p==='/api/state')return route.fulfill({json:{...sandbox.fixtureState,access_scope:'sites',access_permissions:['sites']}});
return route.fulfill({json:{}});});
await page.goto('http://lb.preview/');await page.locator('#login-methods').waitFor();
assert(await page.locator('#auth-form').isVisible());assert(!await page.locator('#temporary-login').isVisible());
const headingTop=await page.locator('#auth-title').evaluate(el=>el.getBoundingClientRect().top);assert.equal(await page.locator('#login-admin-tab').textContent(),'账号密码登录');assert.equal(await page.locator('#login-token-tab').textContent(),'令牌登录');await page.locator('#login-token-tab').click();assert.equal(await page.locator('#auth-title').evaluate(el=>el.getBoundingClientRect().top),headingTop);assert(!await page.locator('#auth-form').isVisible());assert(await page.locator('#temporary-login').isVisible());assert(!await page.locator('.local-login-entry').isVisible());
await page.locator('#login-token-tab').press('ArrowLeft');assert(await page.locator('#auth-form').isVisible());assert.equal(await page.locator('#login-admin-tab').getAttribute('aria-selected'),'true');
assert.equal(await page.locator('#temporary-login').evaluate(el=>getComputedStyle(el).opacity),'0');assert.equal(await page.locator('#temporary-login button').evaluate(el=>getComputedStyle(el).visibility),'hidden');
await page.screenshot({path:path.join(work,'login-method-admin-'+width+'.png')});
await page.locator('#login-token-tab').click();assert.equal(await page.locator('#login-token-tab').getAttribute('aria-selected'),'true');await page.screenshot({path:path.join(work,'login-method-token-'+width+'.png')});
assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
await page.locator('#temporary-login-form input').fill('invalid');await page.locator('#temporary-login-form button').click();await page.getByText('令牌无效或已到期',{exact:true}).waitFor();assert(await page.locator('#temporary-login').isVisible());assert.equal(await page.locator('#login-token-tab').getAttribute('aria-selected'),'true');assert.equal(await page.locator('#temporary-login-form input').inputValue(),'invalid');
await page.locator('#temporary-login-form input').fill('lb_tmp_TEST_ONLY');await page.locator('#temporary-login-form button').click();await page.locator('#shell').waitFor();assert.equal(attempts,2);
initialized=false;await page.reload();await page.locator('#auth-form').waitFor();assert(!await page.locator('#login-methods').isVisible());assert(!await page.locator('#temporary-login').isVisible());
assert.deepEqual(errors,[]);await page.close();}
console.log('PASS: desktop/mobile login switching, keyboard navigation, Token login, first-time setup, no overflow or JS errors');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
