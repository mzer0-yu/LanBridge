const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../lanbridge/gateway.py'),'utf8').match(/<script>\r?\n([\s\S]*?)<\/script>/)[1];
function page(passcode=false,ok=true){
  const button={hidden:!passcode,disabled:false},form={reportValidity:()=>true},message={},error={};
  const elements={'#verify':form,'#continue':button,'#verify-status':message,'#error':error,'#passcode':passcode?{value:'password'}:null};
  const calls=[],controllers=[];
  const context={AbortSignal:{timeout(ms){const controller=new AbortController();controllers.push({ms,controller});return controller.signal;}},document:{querySelector:s=>elements[s]},window:{turnstile:{reset:()=>calls.push('reset')}},location:{reload:()=>calls.push('reload')},fetch:async(url,options)=>{calls.push(JSON.parse(options.body));return {ok,json:async()=>({detail:'服务暂不可用'})}}};
  vm.createContext(context);vm.runInContext(source,context);
  return {context,button,form,message,error,calls,controllers};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  let p=page();p.context.window.onHumanVerified('valid-token');p.context.window.onHumanVerified('duplicate');await settle();
  assert.equal(p.calls.filter(x=>typeof x==='object').length,1);assert.equal(p.calls[0].token,'valid-token');assert.ok(p.calls.includes('reload'));
  p=page(true);p.context.window.onHumanVerified('valid-token');await settle();assert.equal(p.calls.length,0);
  p.form.onsubmit({preventDefault(){}});await settle();assert.equal(p.calls[0].passcode,'password');assert.ok(p.calls.includes('reload'));
  p=page(false,false);p.context.window.onHumanVerified('valid-token');await settle();assert.equal(p.error.textContent,'服务暂不可用');assert.equal(p.button.hidden,false);assert.equal(p.button.disabled,false);assert.ok(p.calls.includes('reset'));assert.ok(!p.calls.includes('reload'));
  p=page();
  p.context.fetch=(url,options)=>new Promise((resolve,reject)=>options.signal?.addEventListener('abort',()=>reject(options.signal.reason)));
  p.context.window.onHumanVerified('stalled-token');
  assert.equal(p.button.disabled,true);
  assert.equal(p.controllers.length,1);
  assert.equal(p.controllers[0].ms,30000);
  const timeout=Error('timeout');timeout.name='TimeoutError';
  p.controllers[0].controller.abort(timeout);await settle();
  assert.equal(p.button.disabled,false);assert.equal(p.button.hidden,false);
  assert.equal(p.error.textContent,'验证请求超时，请重试。');assert.ok(p.calls.includes('reset'));
  p.context.fetch=async()=>({ok:true});
  p.context.window.onHumanVerified('new-token');await settle();
  assert.ok(p.calls.includes('reload'));assert.equal(p.error.textContent,'');
  console.log('4 verification flow tests passed');
})().catch(error=>{console.error(error);process.exitCode=1});
