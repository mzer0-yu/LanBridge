// Run from any directory; all API responses are isolated test fixtures.
const fs=require('fs'),path=require('path'),assert=require('assert'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'../..'),work=path.resolve(__dirname,'../../.test-artifacts/ui');
const site={id:'preview',name:'电机控制',hostname:'mc.example.com',origin:'http://192.168.1.20:8088',enabled:true,human_check:true,passcode_required:false,allowed_countries:['CN','HK','JP','US'],allowed_ips:[],requests_per_minute:180,session_minutes:60};
const state={settings:{account_id:'a'.repeat(32),zone_id:'b'.repeat(32),zone_name:'example.com',tunnel_id:'t',turnstile_sitekey:'public-key',gateway_port:8891,admin_port:8890,tunnel_name:'LanBridge',cloudflared_path:'cloudflared.exe'},connector:{installed:true,running:true},credentials:{cf_write_token:true,tunnel_token:true,turnstile_secret:true},cloudflare_setup:{ready:true,missing:[]},cloudflare_permission_issues:[],sites:[site],published_hosts:[site.hostname],site_probes:{},token_management:{managed:{kind:'oauth'}},browser_auth:{phase:'done',message:'已连接'},audit:[]};
fs.mkdirSync(path.resolve(__dirname,'../../.test-artifacts/ui'),{recursive:true});
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[],writes=[];page.on('pageerror',e=>errors.push(e.message));await page.route('http://lb.preview/**',route=>{const p=new URL(route.request().url()).pathname;if(p.startsWith('/api/')){if(route.request().method()==='POST')writes.push({path:p,data:route.request().postDataJSON()});return route.fulfill({json:p==='/api/bootstrap'?{initialized:true,authenticated:true,csrf:'preview'}:state});}const file=p==='/'?'index.html':p.slice(1);return route.fulfill({path:path.join(root,'ui',file)})});await page.goto('http://lb.preview/');await page.locator('#sites-table tr').last().waitFor({state:'attached'});assert.equal(await page.locator('[data-view=security]').count(),0);await page.locator('[data-view=sites]').click();assert.equal(await page.locator('#page-title').textContent(),'网站转发');assert((await page.locator('#sites-table').textContent()).includes('已发布'));assert.equal(await page.locator('#sites-table th').first().evaluate(el=>getComputedStyle(el).backgroundColor),'rgba(0, 0, 0, 0)');await page.screenshot({path:path.join(work,'website-management.png')});await page.locator('#sites-table [data-edit]').click();assert.equal(await page.locator('#site-dialog .site-edit-section h3').count(),2);assert.equal(await page.locator('#site-form [name=hostname]').inputValue(),site.hostname);assert.equal(await page.locator('#site-form [name=human_check]').isChecked(),true);await page.screenshot({path:path.join(work,'website-editor.png')});await page.locator('#cancel-site').click();await page.locator('[data-view=settings]').click();assert.equal(await page.locator('#human-verification-panel').isVisible(),true);
for(const width of [1440,390]){
 await page.setViewportSize({width,height:1000});
 await page.locator('#verification-advanced').evaluate(el=>el.open=true);
 const key=page.locator('#verification-credentials-form [name=turnstile_sitekey]'),secret=page.locator('#verification-credentials-form [name=turnstile_secret]'),feedback=page.locator('#verification-feedback');
 assert.equal(await feedback.textContent(),'');
 assert.equal(await secret.evaluate(el=>el.tagName),'TEXTAREA');
 assert.equal(await page.locator('#verification-credentials-form input[type=password]').count(),0);
 assert.equal(await secret.evaluate(el=>getComputedStyle(el).webkitTextSecurity),'disc');
 assert.equal(await secret.getAttribute('autocomplete'),'off');
 // A browser may emit input/change without changing the saved value.
 await key.dispatchEvent('input');await key.dispatchEvent('change');assert.equal(await feedback.textContent(),'');
 await key.fill('  public-key  ');assert.equal(await feedback.textContent(),'');
 await key.fill('changed-public-key');assert((await feedback.textContent()).includes('未保存'));
 await page.locator('#refresh').click();await page.waitForFunction(()=>document.querySelector('#refresh').dataset.refreshState==='success');
 assert.equal(await key.inputValue(),'changed-public-key');
 await key.fill('public-key');assert.equal(await feedback.textContent(),'');
 await secret.fill('test-only-new-secret');assert.equal(await feedback.textContent(),'有未保存的修改');
 await page.locator('#human-verification-panel').screenshot({path:path.join(work,`verification-masked-${width}.png`)});
 await page.locator('#refresh').click();await page.waitForFunction(()=>document.querySelector('#refresh').dataset.refreshState==='success');assert.equal(await secret.inputValue(),'test-only-new-secret');
 await secret.fill('');assert.equal(await feedback.textContent(),'');
 await secret.fill('   ');assert.equal(await feedback.textContent(),'');await secret.fill('');
 await secret.fill('test-only-\nnew-secret\n');assert.equal(await secret.inputValue(),'test-only-new-secret');await secret.fill('');assert.equal(await feedback.textContent(),'');
 await page.locator('#verification-advanced').evaluate(el=>el.open=false);
 await page.locator('#verification-advanced>summary').click();assert.equal(await feedback.textContent(),'');
 assert.equal(await page.locator('#verification-credentials-form').getAttribute('data-dirty'),'');
 await page.locator('#human-verification-panel').screenshot({path:path.join(work,`verification-dirty-${width}.png`)});
}
// The masked key still submits correctly and is cleared after saving.
const secret=page.locator('#verification-credentials-form [name=turnstile_secret]');
await secret.fill('test-only-saved-secret');await secret.press('Enter');
await page.waitForFunction(()=>document.querySelector('#verification-feedback').textContent==='人类验证配置已保存');
assert.equal(await secret.inputValue(),'');
assert.equal(writes.filter(item=>item.path==='/api/credentials').length,0);
assert.deepEqual(writes.find(item=>item.path==='/api/settings').data,{turnstile_sitekey:'public-key',turnstile_secret:'test-only-saved-secret'});
assert.equal(writes.find(item=>item.path==='/api/settings').data.turnstile_sitekey,'public-key');
await page.setViewportSize({width:390,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);assert.deepEqual(errors,[]);console.log('浏览器实测：网站管理、编辑分区、密钥遮罩输入、编辑/保存反馈及手机布局通过；仅使用模拟 API');await browser.close()})().catch(e=>{console.error(e);process.exit(1)});
