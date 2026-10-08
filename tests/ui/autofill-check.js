// Use an isolated Chrome profile: never read the user's saved passwords.
const assert=require('node:assert/strict'),path=require('node:path'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'../..'),fixture=require('./fixture').state;
(async()=>{const browser=await chromium.launch({headless:true,channel:'chrome'});try{
for(const width of [390,1440]){
 const page=await browser.newPage({viewport:{width,height:1000}}),errors=[],writes=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.route('http://lb.preview/**',route=>{const p=new URL(route.request().url()).pathname;
  if(p.startsWith('/api/')){if(route.request().method()==='POST')writes.push(p);return route.fulfill({json:p==='/api/bootstrap'?{initialized:true,authenticated:true,csrf:'fixture'}:p==='/api/temporary-tokens'?{tokens:[]}:fixture});}
  return route.fulfill({path:path.join(root,'ui',p==='/'?'index.html':p.slice(1))});
 });
 await page.goto('http://lb.preview/');await page.locator('[data-view=settings]').click();
 assert.deepEqual(await page.locator('input[type=password]').evaluateAll(nodes=>nodes.map(n=>({form:n.form.id,name:n.name,complete:n.autocomplete}))),[
  {form:'auth-form',name:'password',complete:'current-password'},
  {form:'password-form',name:'current',complete:'current-password'},
  {form:'password-form',name:'password',complete:'new-password'}]);
 assert.equal(await page.locator('#auth-form [name=username]').getAttribute('autocomplete'),'username');
 assert.deepEqual(await page.locator('form').evaluateAll(nodes=>nodes.filter(n=>!['auth-form','password-form'].includes(n.id)&&n.autocomplete!=='off').map(n=>n.id)),[]);
 const names=['token','cf_write_token','cf_read_token','authority','turnstile_secret','passcode','access_key_id','access_key_secret','security_token'];
 assert.equal(await page.locator('textarea.secret-input').count(),names.length);
 for(const name of names){
  const field=page.locator(`textarea[name=${name}]`);
  assert.equal(await field.getAttribute('autocomplete'),'off');
  assert.equal(await field.evaluate(n=>getComputedStyle(n).webkitTextSecurity),'disc');
  assert.equal(await field.evaluate(n=>n.spellcheck),false);
  // Use synthetic input here because some editors are intentionally hidden.
  await field.evaluate(n=>{n.value='test-only-\ncredential\r\n';n.dispatchEvent(new Event('input',{bubbles:true}));});
  assert.equal(await field.inputValue(),'test-only-credential');
  assert.equal(await field.evaluate(n=>new FormData(n.form).get(n.name)),'test-only-credential');
  await field.evaluate(n=>{n.value='';n.dispatchEvent(new Event('input',{bubbles:true}));});
 }
 assert.equal(await page.locator('#verification-feedback').textContent(),'');
 assert.equal(await page.locator('#credentials-feedback').textContent(),'');
 // Same-value events cannot dirty ordinary configuration, including its external path field.
 for(const name of ['account_id','zone_id','zone_name','tunnel_name','cloudflared_path']){
  const field=page.locator(`#settings-form [name=${name}],input[form=settings-form][name=${name}]`);
  const original=await field.inputValue();
  await field.dispatchEvent('input');await field.dispatchEvent('change');
  assert.equal(await page.locator('#settings-feedback').textContent(),'');
  await field.evaluate(n=>{n.value+='-edit';n.dispatchEvent(new Event('input',{bubbles:true}));});
  assert.equal(await page.locator('#settings-feedback').textContent(),'有未保存的修改');
  await field.evaluate((n,v)=>{n.value=v;n.dispatchEvent(new Event('change',{bubbles:true}));},original);
  assert.equal(await page.locator('#settings-feedback').textContent(),'');
 }
 // Opening an existing site's editor leaves the private replacement passcode empty.
 await page.locator('[data-view=sites]').click();await page.locator('#sites-table [data-edit]').first().click();
 await page.locator('#site-form [name=passcode_required]').check();
 assert.equal(await page.locator('#site-form [name=passcode]').inputValue(),'');
 await page.locator('#site-form [name=passcode]').fill('test-only-site-passcode');
 assert.equal(await page.locator('#site-form [name=passcode]').evaluate(n=>getComputedStyle(n).webkitTextSecurity),'disc');
 await page.locator('#site-form').evaluate(form=>form.addEventListener('submit',e=>{e.preventDefault();e.stopImmediatePropagation();window.testSubmitter=e.submitter?.id;},true));
 await page.locator('#site-form [name=passcode]').press('Enter');
 assert.equal(await page.evaluate(()=>window.testSubmitter),await page.locator('#site-form').evaluate(form=>[...form.elements].find(n=>n.type==='submit').id));
 await page.locator('#cancel-site').click();
 assert.deepEqual(writes,[],'Input changes must not submit credentials without an explicit save');
 assert.deepEqual(errors,[]);await page.close();
}
console.log('PASS: isolated Chrome credential field semantics/masking, FormData association, empty editors, same-value/restore feedback and no implicit writes');
}finally{await browser.close();}})().catch(error=>{console.error(error);process.exit(1)});
