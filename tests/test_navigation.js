const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const root=path.join(__dirname,'..');
const source=fs.readFileSync(path.join(root,'ui/app.js'),'utf8');
function harness(state){
  const nodes={};
  const context={state,currentView:'overview',esc:value=>String(value).replaceAll('<','&lt;'),$:selector=>nodes[selector]||=({dataset:{}})};
  vm.createContext(context);
  const readiness=source.slice(source.lastIndexOf('function connectorReadiness'),source.lastIndexOf('function renderConnectorReadiness'));
  vm.runInContext(readiness+source.slice(source.lastIndexOf('function navigationIssues')),context);
  return {context,nodes};
}
function ready(){return {settings:{tunnel_id:'t',turnstile_sitekey:'key'},connector:{installed:true,running:true},credentials:{tunnel_token:true,turnstile_secret:true},cloudflare_setup:{ready:true},cloudflare_permission_issues:[],sites:[],published_hosts:[],site_probes:{},token_management:{}};}
function marked(context){const issues=context.navigationIssues(context.state);return Object.keys(issues).filter(key=>key!=='overview'&&issues[key].length);}
test('missing account configuration belongs only to settings; empty unused features are not errors',()=>{
  const state=ready();state.settings.tunnel_id='';state.connector.running=false;state.credentials.tunnel_token=false;state.cloudflare_setup={ready:false,missing:['API Token']};
  const {context}=harness(state);assert.deepEqual(Array.from(marked(context)),['settings']);
});
test('permission failure does not spread to mapping, security or history tabs',()=>{
  const state=ready();state.cloudflare_permission_issues=[{detail:'Cloudflare API HTTP 403：创建 Tunnel失败'}];
  const {context}=harness(state);assert.deepEqual(Array.from(marked(context)),['settings']);
});
test('route changes and incomplete publication belong only to connector',()=>{
  const state=ready();state.sites=[{id:'s',enabled:true,hostname:'app.example.com',human_check:false}];state.publication_needs_review=true;
  const {context}=harness(state);assert.deepEqual(Array.from(marked(context)),['connector']);
});
test('source warning is tied to the checked origin; changed or disabled sites remove it',()=>{
  const state=ready();state.sites=[{id:'s',name:'LAN',enabled:true,hostname:'app.example.com',origin:'http://127.0.0.1:9300'}];state.published_hosts=['app.example.com'];state.site_probes={s:{reachable:false,origin:'http://127.0.0.1:9300'}};
  const {context}=harness(state);assert.deepEqual(Array.from(marked(context)),['sites']);
  state.sites[0].origin='http://127.0.0.1:9400';assert.deepEqual(Array.from(marked(context)),[]);
});
test('only published enabled human-check sites report missing Turnstile credentials',()=>{
  const state=ready();state.sites=[{id:'s',enabled:true,hostname:'app.example.com',human_check:true}];state.published_hosts=['app.example.com'];state.credentials.turnstile_secret=false;
  const {context}=harness(state);assert.deepEqual(Array.from(marked(context)),['security']);state.credentials.turnstile_secret=true;assert.deepEqual(Array.from(marked(context)),[]);
});
test('connector badge resets after recovery and view changes; overview summary has no pending badge',()=>{
  const state=ready();state.publication_needs_review=true;
  const {context,nodes}=harness(state);context.renderNavigationIssues();assert.equal(nodes['#connector-nav-status'].textContent,'待处理');assert.equal(nodes['#overview-nav-status'].hidden,true);
  state.publication_needs_review=false;context.currentView='settings';context.renderNavigationIssues();assert.equal(nodes['#connector-nav-status'].textContent,'运行中');assert.equal(nodes['#connector-nav-status'].className,'nav-status success');assert.equal(nodes['#audit-nav-status'].hidden,true);
});
test('settings is the last list item without bottom anchoring',()=>{
  const html=fs.readFileSync(path.join(root,'ui/index.html'),'utf8'),css=fs.readFileSync(path.join(root,'ui/style.css'),'utf8');
  const nav=html.match(/<nav>(.*?)<\/nav>/s)[1];assert.equal([...nav.matchAll(/data-view="([^"]+)"/g)].at(-1)[1],'settings');assert(!css.includes('aside nav{flex:1}'));assert(!css.includes('button[data-view=settings]{margin-top:auto}'));
});

test('unpublished default human-check option is preparation, not a security failure',()=>{
  const state=ready();state.settings.tunnel_id='';state.settings.turnstile_sitekey='';state.credentials.turnstile_secret=false;state.credentials.tunnel_token=false;state.connector.running=false;state.sites=[{id:'s',enabled:true,hostname:'app.example.com',human_check:true}];
  const {context,nodes}=harness(state);context.renderNavigationIssues();assert.equal(nodes['#security-nav-status'].hidden,true);assert.deepEqual(Array.from(marked(context)),['connector']);
});

test('a replaced credential is awaiting verification instead of a current permission failure',()=>{
  const state=ready();state.cloudflare_permission_issues=[{status:'needs_recheck',detail:'Old 403'}];
  const {context,nodes}=harness(state);context.renderNavigationIssues();assert.equal(nodes['#settings-nav-status'].textContent,'权限待核验');assert.equal(nodes['#settings-nav-status'].className,'nav-status neutral');assert.deepEqual(Array.from(marked(context)),[]);
});

test('permission and missing tunnel badges name distinct actions and recover independently',()=>{
  const state=ready();state.settings.tunnel_id='';state.connector.running=false;state.credentials.tunnel_token=false;state.cloudflare_permission_issues=[{status:'last_failure',detail:'Cloudflare API HTTP 403：创建 Tunnel失败'}];
  state.sites=[{id:'s',enabled:true,hostname:'app.example.com',human_check:false}];
  const {context,nodes}=harness(state);context.renderNavigationIssues();
  assert.equal(nodes['#settings-nav-status'].textContent,'权限待核验');assert.equal(nodes['#settings-nav-status'].hidden,false);
  assert.equal(nodes['#connector-nav-status'].textContent,'待创建 Tunnel');
  state.cloudflare_permission_issues=[];context.renderNavigationIssues();assert.equal(nodes['#settings-nav-status'].hidden,true);assert.equal(nodes['#connector-nav-status'].textContent,'待创建 Tunnel');
  state.settings.tunnel_id='t';context.renderNavigationIssues();assert.equal(nodes['#connector-nav-status'].textContent,'待获取令牌');
  state.credentials.tunnel_token=true;state.connector.installed=false;context.renderNavigationIssues();assert.equal(nodes['#connector-nav-status'].textContent,'待安装连接器');
  state.connector.installed=true;state.connector.running=true;state.published_hosts=['app.example.com'];context.renderNavigationIssues();assert.equal(nodes['#connector-nav-status'].textContent,'运行中');
});

function asyncHarness(overrides={}){
  const context={csrf:'session',state:{credentials:{cf_write_token:false}},stateSequence:0,appliedStateSequence:0,render(){},renderActionAvailability(){},$:()=>({}),toast(){},...overrides};
  vm.createContext(context);
  vm.runInContext(source.match(/^async function loadState\(\).*$/m)[0]+ '\n'+ source.match(/^async function action\(.*$/m)[0],context);
  return context;
}
test('successful writes remain successful when refreshing state fails',async()=>{
  const toasts=[];const context=asyncHarness({api:async(path)=>{if(path==='state')throw Error('refresh unavailable');return {saved:true,credentials:{cf_write_token:true}};},toast:message=>toasts.push(message)});
  const button={dataset:{},disabled:false};const result=await context.action(button,'credentials','已保存',{});
  assert.equal(result.saved,true);assert.equal(context.state.credentials.cf_write_token,true);assert.equal(button.disabled,false);assert(toasts.some(message=>message.includes('勿重复提交')));
});
test('out-of-order responses never overwrite newer state',async()=>{
  const pending=[];const context=asyncHarness({api:()=>new Promise(resolve=>pending.push(resolve))});
  const first=context.loadState(),second=context.loadState();pending[1]({version:2});await second;pending[0]({version:1});await first;assert.equal(context.state.version,2);
});
test('a pending refresh cannot restore state after logout or a session change',async()=>{
  let resolve;const context=asyncHarness({api:()=>new Promise(done=>resolve=done)});const pending=context.loadState();context.csrf='new-session';context.state=null;resolve({oldSession:true});await pending;assert.equal(context.state,null);
});
test('a failed mutation refreshes persisted failure state and permits retry',async()=>{
  const context=asyncHarness({api:async(path)=>{if(path==='state')return {failed:true};throw Error('Cloudflare 403');}});const button={dataset:{},disabled:false};await assert.rejects(context.action(button,'create-tunnel',null,{}),/403/);assert.equal(context.state.failed,true);assert.equal(button.disabled,false);assert.equal(button.dataset.busy,'');
});

test('unavailable connector actions are disabled until prerequisites are met',()=>{
  const state=ready();state.settings.tunnel_id='';state.credentials.tunnel_token=false;state.credentials.cf_write_token=false;state.cloudflare_setup.ready=false;state.connector.running=false;
  const {context,nodes}=harness(state);context.renderActionAvailability();
  for(const id of ['connector-start','connector-stop','cf-check','preview'])assert.equal(nodes['#'+id].disabled,true);
  assert.equal(nodes['#create-tunnel'].disabled,true);state.credentials.cf_write_token=true;state.cloudflare_setup.ready=true;context.renderActionAvailability();assert.equal(nodes['#create-tunnel'].disabled,false);
  state.settings.tunnel_id='t';state.credentials.tunnel_token=true;context.renderActionAvailability();assert.equal(nodes['#connector-start'].disabled,false);
});
test('a running mutation cannot be submitted a second time',async()=>{
  let finish;let count=0;const context=asyncHarness({api:async(path)=>{if(path==='state')return ready();count++;return new Promise(resolve=>finish=resolve);}});const button={dataset:{},disabled:false};
  const first=context.action(button,'create-tunnel',null,{});await assert.rejects(context.action(button,'create-tunnel',null,{}),/正在进行/);finish({saved:true});await first;assert.equal(count,1);
});
