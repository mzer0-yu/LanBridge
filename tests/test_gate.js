const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../lanbridge/gateway.py'),'utf8').match(/<script>\r?\n([\s\S]*?)<\/script>/)[1];
function page(passcode=false,ok=true){
  const button={hidden:!passcode,disabled:false},form={reportValidity:()=>true},message={},error={};
  const elements={'#verify':form,'#continue':button,'#verify-status':message,'#error':error,'#passcode':passcode?{value:'password'}:null};
  const calls=[];
  const context={document:{querySelector:s=>elements[s]},window:{turnstile:{reset:()=>calls.push('reset')}},location:{reload:()=>calls.push('reload')},fetch:async(url,options)=>{calls.push(JSON.parse(options.body));return {ok,json:async()=>({detail:'服务暂不可用'})}}};
  vm.createContext(context);vm.runInContext(source,context);
  return {context,button,form,message,error,calls};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  let p=page();p.context.window.onHumanVerified('valid-token');p.context.window.onHumanVerified('duplicate');await settle();
  assert.equal(p.calls.filter(x=>typeof x==='object').length,1);assert.equal(p.calls[0].token,'valid-token');assert.ok(p.calls.includes('reload'));
  p=page(true);p.context.window.onHumanVerified('valid-token');await settle();assert.equal(p.calls.length,0);
  p.form.onsubmit({preventDefault(){}});await settle();assert.equal(p.calls[0].passcode,'password');assert.ok(p.calls.includes('reload'));
  p=page(false,false);p.context.window.onHumanVerified('valid-token');await settle();assert.equal(p.error.textContent,'服务暂不可用');assert.equal(p.button.hidden,false);assert.equal(p.button.disabled,false);assert.ok(p.calls.includes('reset'));assert.ok(!p.calls.includes('reload'));
  console.log('3 verification flow tests passed');
})().catch(error=>{console.error(error);process.exitCode=1});
