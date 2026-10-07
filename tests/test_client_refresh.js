const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../ui/client.js'),'utf8');
function harness(fetch){
  const nodes={};
  const node=id=>nodes[id]||={disabled:false,hidden:true,textContent:'',children:[],replaceChildren(){this.children=[];},append(child){this.children.push(child);}};
  const controllers=[];
  const context={document:{getElementById:node,createElement:()=>({})},fetch,Date,
    AbortSignal:{timeout(ms){const controller=new AbortController();controllers.push({ms,controller});return controller.signal;}},
    setInterval(){}};
  vm.createContext(context);vm.runInContext(source,context);
  return {node,controllers};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));

test('stalled routes request times out and manual refresh can recover',async()=>{
  let calls=0;
  const h=harness((url,options)=>{
    calls++;
    if(calls===1)return new Promise((resolve,reject)=>options.signal?.addEventListener('abort',()=>reject(Error('timed out'))));
    return Promise.resolve({ok:true,json:async()=>({routes:[],updated_at:1})});
  });
  assert(h.node('refresh').disabled);
  assert.equal(h.controllers.length,1);
  assert.equal(h.controllers[0].ms,15000);
  h.controllers[0].controller.abort();await settle();
  assert(!h.node('refresh').disabled);assert(!h.node('error').hidden);
  await h.node('refresh').onclick();
  assert.equal(calls,2);assert(h.node('error').hidden);assert(!h.node('refresh').disabled);
});

test('failed route refresh keeps displayed routes and allows another attempt',async()=>{
  const h=harness(()=>Promise.reject(Error('network unavailable')));
  h.node('routes').children.push({name:'existing route'});
  await settle();
  assert.equal(h.node('routes').children.length,1);
  assert(!h.node('refresh').disabled);
  assert(!h.node('error').hidden);
});
