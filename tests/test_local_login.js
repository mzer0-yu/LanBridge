const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../ui/local-login.js'),'utf8');
function harness(api,hash=''){
  const nodes={},timers=[],handlers={};
  const node=id=>nodes[id]||=( {hidden:true,disabled:false,value:'default',textContent:'',
    setAttribute(){},replaceChildren(){},append(){},elements:{password:{value:''}}} );
  const context={api,csrf:'session',initialized:true,window:{addEventListener(name,handler){handlers[name]=handler;}},location:{hash},
    document:{getElementById:node,createElement:()=>({})},
    localStorage:{getItem:()=>null,setItem(){}},setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout(){}};
  vm.createContext(context);vm.runInContext(source,context);
  return {context,node,timers,handlers};
}
test('late browser discovery cannot restart local approval after another login',async()=>{
  let finish;const calls=[];
  const {context,node}=harness(path=>{calls.push(path);return new Promise(resolve=>finish=resolve);});
  const pending=node('local-login-start').onclick();
  context.window.localLoginUI.stop();finish({browsers:[{id:'default',name:'默认'}]});await pending;
  assert.deepEqual(calls,['local-login/browsers']);assert(node('local-login-options').hidden);
});
test('late local login creation is ignored after cancelling its UI',async()=>{
  let finish;
  const {context,node,timers}=harness(path=>path==='local-login/browsers'
    ?Promise.resolve({browsers:[{id:'default',name:'默认'}]}):new Promise(resolve=>finish=resolve));
  const pending=node('local-login-start').onclick();
  await new Promise(resolve=>setImmediate(resolve));
  context.window.localLoginUI.stop();
  finish({request_id:'old',code:'123456',browser_name:'默认',expires_at:Date.now()/1000+300});await pending;
  assert(node('local-login-wait').hidden);assert.equal(timers.length,0);
  assert.equal(node('local-login-code').textContent,'');
});
test('a stopped approval detail response cannot re-enable old confirmation buttons',async()=>{
  let finish;
  const {context,node}=harness(()=>new Promise(resolve=>finish=resolve),'#local-login='+'a'.repeat(43));
  const pending=context.window.localLoginUI.showApproval();context.window.localLoginUI.stop();
  finish({phase:'pending',code:'123456',expires_at:Date.now()/1000+300});await pending;
  assert(node('local-approval-actions').hidden);assert.equal(node('local-approval-code').textContent,'');
});
test('current local approval still displays its code and schedules polling',async()=>{
  const {node,timers}=harness(path=>Promise.resolve(path==='local-login/browsers'
    ?{browsers:[{id:'default',name:'默认'}]}
    :{request_id:'new',code:'123456',browser_name:'默认',expires_at:Date.now()/1000+300}));
  await node('local-login-start').onclick();assert(!node('local-login-wait').hidden);
  assert.equal(node('local-login-code').textContent,'123456');assert.equal(timers.length,1);
});

for(const failure of [false,true]){
  test('late approval '+(failure?'failure':'success')+' cannot change a newer confirmation',async()=>{
    const first='a'.repeat(43),second='b'.repeat(43);const pending=[];
    const h=harness(path=>{
      if(path==='bootstrap')return Promise.resolve({initialized:true,authenticated:true,csrf:'session'});
      if(path.startsWith('local-login/request/'))return Promise.resolve({phase:'pending',code:path.endsWith(first)?'123456':'654321',expires_at:Date.now()/1000+300});
      if(path==='local-login/approve')return new Promise((resolve,reject)=>pending.push({resolve,reject}));
      throw Error('Unexpected request: '+path);
    },'#local-login='+first);
    h.context.openShell=()=>h.context.window.localLoginUI.showApproval();
    await h.context.window.localLoginUI.showApproval();
    const old=h.node('local-approval-allow').onclick();
    h.context.location.hash='#local-login='+second;
    await h.handlers.hashchange();await new Promise(resolve=>setImmediate(resolve));
    assert.equal(h.node('local-approval-code').textContent,'654321');
    assert.equal(h.node('local-approval-allow').disabled,false);
    const current=h.node('local-approval-deny').onclick();
    const message=h.node('local-approval-status').textContent;
    if(failure)pending[0].reject(Error('old-request-error'));else pending[0].resolve({});
    await old;
    assert.equal(h.node('local-approval-status').textContent,message);
    assert.equal(h.node('local-approval-actions').hidden,false);
    assert.equal(h.node('local-approval-deny').disabled,true);
    pending[1].resolve({});await current;
    assert.match(h.node('local-approval-status').textContent,/已拒绝登录/);
    assert.equal(h.node('local-approval-actions').hidden,true);
    assert.equal(h.node('local-approval-deny').disabled,false);
  });
}
