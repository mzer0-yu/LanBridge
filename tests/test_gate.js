const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../lanbridge/gateway.py'),'utf8').match(/<script>\r?\n([\s\S]*?)<\/script>/)[1];
function page(passcode=false,ok=true){
  const button={hidden:!passcode,disabled:false},form={reportValidity:()=>true},message={},error={};
  const widget={clientWidth:340,setAttribute(){}};
  const elements={'#verify':form,'#continue':button,'#verify-status':message,'#error':error,'#passcode':passcode?{value:'password'}:null,'.cf-turnstile':widget};
  const calls=[],controllers=[];
  const context={AbortController,setTimeout(callback,ms){const timer={ms,callback,cleared:false};controllers.push(timer);return timer;},clearTimeout(timer){timer.cleared=true;},document:{querySelector:s=>elements[s]},window:{turnstile:{reset:()=>calls.push('reset')}},location:{reload:()=>calls.push('reload')},fetch:async(url,options)=>{calls.push(JSON.parse(options.body));return {ok,status:ok?200:503,json:async()=>ok?{verified:true}:{detail:'服务暂不可用'}}}};
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
  p.controllers[0].callback();await settle();
  assert.equal(p.button.disabled,false);assert.equal(p.button.hidden,false);
  assert.equal(p.error.textContent,'验证请求超时，请重试。');assert.ok(p.calls.includes('reset'));
  assert.equal(p.controllers[0].cleared,true);
  p.context.fetch=async()=>({ok:true,json:async()=>({verified:true})});
  p.context.window.onHumanVerified('new-token');await settle();
  assert.ok(!p.calls.includes('reload'));p.form.onsubmit({preventDefault(){}});await settle();
  assert.ok(p.calls.includes('reload'));assert.equal(p.error.textContent,'');
  for(const [status,body] of [[408,''],[502,'<html>Bad gateway</html>'],[503,'{"detail":'],[429,''],[403,'']]){
    p=page();p.context.fetch=async()=>new Response(body,{status});
    p.context.window.onHumanVerified('failed-token');await settle();
    assert.ok(p.error.textContent.includes(status===429?'频繁':status===403?'未通过':status===408?'超时':'有效结果'));
    assert.ok(!p.error.textContent.includes('JSON'));assert.ok(!p.calls.includes('reload'));
    assert.equal(p.button.disabled,false);assert.equal(p.button.hidden,false);assert.ok(p.calls.includes('reset'));
    p.context.fetch=async(url,options)=>{p.calls.push(JSON.parse(options.body));return new Response('{"verified":true}')};
    p.context.window.onHumanVerified('fresh-token');await settle();
    assert.ok(!p.calls.includes('reload'));p.form.onsubmit({preventDefault(){}});await settle();
    assert.equal(p.calls.find(x=>typeof x==='object').token,'fresh-token');assert.ok(p.calls.includes('reload'));
  }
  for(const body of ['', '<html>Proxy response</html>', '{"verified":', '{"verified":false}']){
    p=page();p.context.fetch=async()=>new Response(body);
    p.context.window.onHumanVerified('token');await settle();
    assert.ok(p.error.textContent.includes('响应不完整'));assert.ok(!p.calls.includes('reload'));
  }
  p=page();p.context.fetch=async()=>{throw new TypeError('Failed to fetch')};
  p.context.window.onHumanVerified('token');await settle();
  assert.equal(p.error.textContent,'验证请求未能完成，请检查网络后重新验证。');assert.ok(p.calls.includes('reset'));
  p=page();p.context.fetch=async()=>({ok:false,status:503,json:async()=>{throw timeout}});
  p.context.window.onHumanVerified('token');await settle();
  assert.equal(p.error.textContent,'验证请求超时，请重试。');
  p=page();p.context.fetch=async()=>new Response('{"detail":{"untrusted":"object"}}',{status:503});
  p.context.window.onHumanVerified('token');await settle();
  assert.ok(p.error.textContent.includes('有效结果'));assert.ok(!p.error.textContent.includes('[object Object]'));
  p=page();p.form.onsubmit({preventDefault(){}});await settle();
  assert.equal(p.calls.filter(x=>typeof x==='object').length,0);assert.ok(p.error.textContent.includes('请重新完成'));
  p=page(true);p.context.window.onHumanVerified('expired-token');p.context.window.onHumanExpired();
  p.form.onsubmit({preventDefault(){}});await settle();assert.equal(p.calls.filter(x=>typeof x==='object').length,0);
  for(const callback of ['onHumanError','onHumanTimeout','onHumanScriptError']){
    p=page();p.context.window[callback]();assert.equal(p.button.hidden,false);assert.ok(p.error.textContent);
    p.context.window.onHumanVerified('recovered-token');await settle();assert.ok(p.calls.includes('reload'));assert.equal(p.error.textContent,'');
  }
  p=page();p.context.window.turnstile=undefined;p.context.window.onHumanScriptError();
  p.form.onsubmit({preventDefault(){}});await settle();assert.ok(p.calls.includes('reload'));assert.equal(p.controllers.length,0);
  p=page(false,false);p.context.window.turnstile.reset=()=>{throw Error('widget unavailable')};
  p.context.window.onHumanVerified('token');await settle();assert.equal(p.button.disabled,false);assert.ok(p.error.textContent.includes('刷新页面'));
  console.log('Verification flow tests passed: success, passcode, duplicate submission, timeout, malformed responses, network failure and fresh-token retry');
})().catch(error=>{console.error(error);process.exitCode=1});
