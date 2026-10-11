const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const root=path.join(__dirname,'..');
const source=fs.readFileSync(path.join(root,'ui/app.js'),'utf8');
test('late Cloudflare onboarding does not reset inputs in a different session',async()=>{
  let finish;
  const form={elements:{cf_read_token:{value:''}},reset(){throw Error('New session inputs must not be reset');}};
  const nodes={'#api-token-zone-select':{value:'0'},'#credentials-form [name=cf_write_token]':{value:'test-token',form},
    '#api-token-discovery-feedback':{},'#api-token-connect':{dataset:{}},'#settings-form':{dataset:{}}};
  const context={csrf:'old-session',state:{},tokenDiscoveries:{'api-token':{revision:'r',zones:[{zone_id:'z',account_id:'a'}]}},
    $:key=>nodes[key],lockForm(){},unlockForm(){},renderSetup(){},renderTokenManager(){},api:()=>new Promise(resolve=>finish=resolve)};
  vm.createContext(context);
  const start=source.indexOf('async function connectTokenAccess(');
  vm.runInContext(source.slice(start,source.indexOf("for(const kind of ['api-token','authority-token'])",start)),context);
  const pending=context.connectTokenAccess('api-token');context.csrf='new-session';finish({});await pending;
  assert.equal(nodes['#credentials-form [name=cf_write_token]'].value,'test-token');
  assert.notEqual(nodes['#api-token-discovery-feedback'].textContent,'API Token、账户与域名已保存');
});
test('a stale log page cannot restore pruned records or older settings after refresh',async()=>{
  let finish;const nodes={'#audit-load-more':{},'#audit-storage-feedback':{}};
  const context={csrf:'session',appliedStateSequence:1,state:{audit:[{id:120},{id:110}],audit_storage:{oldest_id:1,limit_mb:10}},
    api:()=>new Promise(resolve=>finish=resolve),renderAudit(){},$:key=>nodes[key]};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf("$('#audit-load-more').onclick="),source.indexOf('function clearTemporaryToken')),context);
  const pending=nodes['#audit-load-more'].onclick();
  context.appliedStateSequence=2;context.state.audit=[{id:125},{id:120}];context.state.audit_storage={oldest_id:100,limit_mb:5};
  finish({records:[{id:109},{id:90}],storage:{oldest_id:1,limit_mb:10}});await pending;
  assert.deepEqual(Array.from(context.state.audit,row=>row.id),[125,120,109]);
  assert.equal(context.state.audit_storage.limit_mb,5);
  assert.equal(nodes['#audit-load-more'].disabled,false);
});
test('state reads have a deadline and a readable timeout without limiting writes',async()=>{
  const requests=[],deadlines=[];
  const context={csrf:'session',remoteAccess:false,AbortSignal:{timeout:ms=>{deadlines.push(ms);return 'test-signal';}},
    fetch:async(url,options)=>{requests.push([url,options]);return {ok:true,json:async()=>({saved:true})};}};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('async function api('),source.indexOf('function setLoginMethod')),context);
  await context.api('state');assert.equal(requests[0][1].signal,'test-signal');assert.deepEqual(deadlines,[15000]);
  await context.api('sites',{name:'test'});assert.equal(requests[1][1].signal,undefined);
  context.fetch=async()=>{throw Object.assign(Error('timeout'),{name:'TimeoutError'});};
  await assert.rejects(context.api('state'),/状态读取超时/);
});
test('automatic polling skips hidden pages and overlapping requests',async()=>{
  let finish,calls=0;
  const context={csrf:'session',document:{hidden:false},loadState:()=>{calls++;return new Promise(resolve=>finish=resolve);}};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('let automaticStateRefresh='),source.indexOf('setInterval(pollState,')),context);
  context.pollState();context.pollState();assert.equal(calls,1);
  finish();await new Promise(resolve=>setImmediate(resolve));
  context.document.hidden=true;context.pollState();assert.equal(calls,1);
  context.document.hidden=false;context.pollState();assert.equal(calls,2);
  finish();
});
test('older temporary-token list responses cannot restore a revoked token',async()=>{
  const pending=[],nodes={};
  const context={csrf:'session',state:{},revealedTemporaryTokens:new Map(),temporaryPermissionText:()=>'',esc:String,
    api:()=>new Promise(resolve=>pending.push(resolve)),$:key=>nodes[key]||=( {textContent:'',innerHTML:''} )};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('let temporaryTokenSequence='),source.indexOf('const temporaryHoursInput=')),context);
  const first=context.loadTemporaryTokens(),second=context.loadTemporaryTokens();
  pending[1]({tokens:[]});await second;
  const current=nodes['#temporary-token-list'].innerHTML;
  pending[0]({tokens:[{id:'old',name:'old',expires:Date.now()/1000+300,revealable:true}]});await first;
  assert.equal(nodes['#temporary-token-list'].innerHTML,current);
  assert.equal(nodes['#temporary-token-count'].textContent,'0 个');
});
test('token creation response after logout cannot put its secret back into the page',async()=>{
  let finish;const result={value:'',hidden:true},feedback={textContent:'',classList:{remove(){},add(){}}};
  const form={elements:{name:{value:''},hours:{value:'24'}},querySelectorAll:()=>[{value:'sites'}]};
  const nodes={'#temporary-token-form':{},'#temporary-token-feedback':feedback,'#temporary-token-value':result,'#temporary-token-result':result};
  const context={csrf:'owner-session',$:key=>nodes[key],clearTemporaryToken(){},lockForm(){},unlockForm(){},
    api:()=>new Promise(resolve=>finish=resolve),loadTemporaryTokens:()=>{throw Error('Must not reload after logout');}};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf("$('#temporary-token-form').onsubmit="),source.indexOf('async function copyVisibleTemporaryToken')),context);
  const operation=nodes['#temporary-token-form'].onsubmit({preventDefault(){},currentTarget:form});
  context.csrf='';finish({token:'sensitive-token',name:'old-session-token'});await operation;
  assert.equal(result.value,'');assert.equal(result.hidden,true);assert.equal(feedback.textContent,'');
});
test('protocol form handles independent choices and legacy site defaults',()=>{
  const context={};vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function selectedProtocols('),source.indexOf('function editSite(')),context);
  const form={elements:{protocol_http:{checked:false},protocol_websocket:{checked:true}}};
  assert.deepEqual(Array.from(context.selectedProtocols(form)),['websocket']);
  form.elements.protocol_http.checked=true;assert.deepEqual(Array.from(context.selectedProtocols(form)),['http','websocket']);
  form.elements.protocol_http.checked=false;form.elements.protocol_websocket.checked=false;
  assert.deepEqual(Array.from(context.selectedProtocols(form)),[]);
  const html=fs.readFileSync(path.join(root,'ui/index.html'),'utf8');
  assert(html.includes('name="protocol_http"'));assert(html.includes('name="protocol_websocket"'));
  assert(source.includes("site?.protocols||['http','websocket']"));
});
function harness(state){
  const nodes={};
  const context={state,publicationRetryPending:false,currentView:'overview',esc:value=>String(value).replaceAll('<','&lt;'),$:selector=>nodes[selector]||=({dataset:{}})};
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
test('permission failure does not spread to website management or history tabs',()=>{
  const state=ready();state.cloudflare_permission_issues=[{detail:'Cloudflare API HTTP 403：创建 Tunnel失败'}];
  const {context}=harness(state);assert.deepEqual(Array.from(marked(context)),['settings']);
});

test('website publication status distinguishes pending publication, removal and verification',()=>{
  const context={};vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function publicationStatus('),source.indexOf('function pauseNotice(')),context);
  const site={hostname:'app.example.com',enabled:true},current={published_hosts:[]};
  assert.equal(context.publicationStatus(site,current).label,'待发布');
  current.published_hosts=[site.hostname];assert.equal(context.publicationStatus(site,current).label,'已发布');
  current.publication_needs_review=true;assert.equal(context.publicationStatus(site,current).label,'待核验');
  site.paused=true;assert.equal(context.publicationStatus(site,current).label,'已暂停');
  site.paused=false;
  site.enabled=false;assert.equal(context.publicationStatus(site,current).label,'待停用');
  current.published_hosts=[];assert.equal(context.publicationStatus(site,current).label,'已停用');
});
test('route changes and incomplete publication belong only to website management',()=>{
  const state=ready();state.sites=[{id:'s',enabled:true,hostname:'app.example.com',human_check:false}];state.publication_needs_review=true;
  const {context}=harness(state);assert.deepEqual(Array.from(marked(context)),['sites']);
});
test('source warning is tied to the checked origin; changed or disabled sites remove it',()=>{
  const state=ready();state.sites=[{id:'s',name:'LAN',enabled:true,hostname:'app.example.com',origin:'http://127.0.0.1:9300'}];state.published_hosts=['app.example.com'];state.site_probes={s:{reachable:false,origin:'http://127.0.0.1:9300'}};
  const {context}=harness(state);assert.deepEqual(Array.from(marked(context)),['sites']);
  state.sites[0].origin='http://127.0.0.1:9400';assert.deepEqual(Array.from(marked(context)),[]);
});
test('only published enabled human-check sites report missing Turnstile credentials',()=>{
  const state=ready();state.sites=[{id:'s',enabled:true,hostname:'app.example.com',human_check:true}];state.published_hosts=['app.example.com'];state.credentials.turnstile_secret=false;
  const {context}=harness(state);assert.deepEqual(Array.from(marked(context)),['settings']);state.credentials.turnstile_secret=true;assert.deepEqual(Array.from(marked(context)),[]);
});
test('publication badge resets and combined forwarding navigation has no duplicate connector entry',()=>{
  const state=ready();state.publication_needs_review=true;
  const {context,nodes}=harness(state);context.renderNavigationIssues();assert.equal(nodes['#sites-nav-status'].hidden,false);assert.equal(nodes['#overview-nav-status'].hidden,true);
  state.publication_needs_review=false;context.currentView='settings';context.renderNavigationIssues();assert.equal(nodes['#sites-nav-status'].hidden,true);
  const html=fs.readFileSync(path.join(root,'ui/index.html'),'utf8');assert(!html.includes('data-view="connector"'));assert(!html.includes('id="view-connector"'));assert(html.includes('网站转发'));
});
test('settings is the last list item without bottom anchoring',()=>{
  const html=fs.readFileSync(path.join(root,'ui/index.html'),'utf8'),css=fs.readFileSync(path.join(root,'ui/style.css'),'utf8');
  const nav=html.match(/<nav>(.*?)<\/nav>/s)[1];assert.equal([...nav.matchAll(/data-view="([^"]+)"/g)].at(-1)[1],'settings');assert(!css.includes('aside nav{flex:1}'));assert(!css.includes('button[data-view=settings]{margin-top:auto}'));
});

test('unpublished default human-check option is preparation, not a security failure',()=>{
  const state=ready();state.settings.tunnel_id='';state.settings.turnstile_sitekey='';state.credentials.turnstile_secret=false;state.credentials.tunnel_token=false;state.connector.running=false;state.sites=[{id:'s',enabled:true,hostname:'app.example.com',human_check:true}];
  const {context,nodes}=harness(state);context.renderNavigationIssues();assert.equal(nodes['#settings-nav-status'].hidden,true);assert.deepEqual(Array.from(marked(context)),['sites']);
});

test('historical failures after credential replacement do not mark navigation as needing action',()=>{
  const state=ready();state.cloudflare_permission_issues=[{status:'needs_recheck',detail:'Old 403'}];
  const {context,nodes}=harness(state);context.renderNavigationIssues();assert.equal(nodes['#settings-nav-status'].hidden,true);assert.equal(nodes['#cloudflare-permission-alert'].hidden,true);assert.deepEqual(Array.from(marked(context)),[]);
});

test('permission and missing tunnel actions remain available in the combined forwarding page',()=>{
  const state=ready();state.settings.tunnel_id='';state.connector.running=false;state.credentials.tunnel_token=false;state.cloudflare_permission_issues=[{status:'last_failure',detail:'Cloudflare API HTTP 403：创建 Tunnel失败'}];
  state.sites=[{id:'s',enabled:true,hostname:'app.example.com',human_check:false}];
  const {context,nodes}=harness(state);context.renderNavigationIssues();assert.equal(nodes['#settings-nav-status'].textContent,'权限待检查');
  state.cloudflare_permission_issues=[];context.renderNavigationIssues();assert.equal(nodes['#settings-nav-status'].hidden,true);assert.equal(nodes['#sites-nav-status'].hidden,false);
  state.settings.tunnel_id='t';state.published_hosts=['app.example.com'];context.renderNavigationIssues();assert.equal(nodes['#sites-nav-status'].hidden,false);
  state.credentials.tunnel_token=true;state.connector.installed=false;context.renderNavigationIssues();assert.equal(nodes['#sites-nav-status'].hidden,false);
  state.connector.installed=true;state.connector.running=true;context.renderNavigationIssues();assert.equal(nodes['#sites-nav-status'].hidden,true);
});

function asyncHarness(overrides={}){
  const context={csrf:'session',currentView:'overview',state:{credentials:{cf_write_token:false}},stateSequence:0,appliedStateSequence:0,render(){},renderActionAvailability(){},$:()=>({}),toast(){},...overrides};
  vm.createContext(context);
  vm.runInContext(source.match(/^async function loadState\(\).*$/m)[0]+ '\n'+ source.match(/^async function action\(.*$/m)[0],context);
  return context;
}
test('successful writes remain successful when refreshing state fails',async()=>{
  const toasts=[];const context=asyncHarness({api:async(path)=>{if(path==='state')throw Error('refresh unavailable');return {saved:true,credentials:{cf_write_token:true}};},toast:message=>toasts.push(message)});
  const button={dataset:{},disabled:false};const result=await context.action(button,'credentials','已保存',{});
  assert.equal(result.saved,true);assert.equal(context.state.credentials.cf_write_token,true);assert.equal(button.disabled,false);assert(toasts.some(message=>message.includes('勿重复提交')));
});
test('late mutation results cannot overwrite a different session state',async()=>{
  let finish;const messages=[];let reads=0;
  const context=asyncHarness({api:path=>{if(path==='state'){reads++;return Promise.resolve({});}return new Promise(resolve=>finish=resolve);},toast:message=>messages.push(message)});
  const button={dataset:{},disabled:false};const operation=context.action(button,'credentials','已保存',{});
  context.csrf='new-session';context.state={credentials:{cf_write_token:false}};
  finish({credentials:{cf_write_token:true}});
  await assert.rejects(operation,/登录状态已变化/);
  assert.equal(context.state.credentials.cf_write_token,false);
  assert.deepEqual(messages,[]);assert.equal(reads,0);assert.equal(button.disabled,false);
});
test('out-of-order responses never overwrite newer state',async()=>{
  const pending=[];const context=asyncHarness({api:()=>new Promise(resolve=>pending.push(resolve))});
  const first=context.loadState(),second=context.loadState();pending[1]({version:2});await second;pending[0]({version:1});await first;assert.equal(context.state.version,2);
});
test('a pending refresh cannot restore state after logout or a session change',async()=>{
  let resolve;const context=asyncHarness({api:()=>new Promise(done=>resolve=done)});const pending=context.loadState();context.csrf='new-session';context.state=null;resolve({oldSession:true});await pending;assert.equal(context.state,null);
});

test('refresh keeps loaded history while removing records pruned by retention',async()=>{
  const records=Array.from({length:125},(_,n)=>({id:125-n}));
  const context=asyncHarness({currentView:'audit',state:{audit:records},api:async()=>({audit:[{id:126},...records.slice(0,99)],audit_storage:{oldest_id:15}})});
  await context.loadState();
  const ids=Array.from(context.state.audit,row=>row.id);
  assert.equal(ids[0],126);assert.equal(ids.at(-1),15);assert.equal(ids.length,112);assert.equal(new Set(ids).size,112);
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

test('tunnel actions distinguish creation, recovery, retrieval and completed configuration',()=>{
  const state=ready();state.connector.running=false;state.settings.account_id='account';state.credentials.cf_write_token=true;
  const {context,nodes}=harness(state);
  state.settings.tunnel_id='';state.credentials.tunnel_token=false;context.renderActionAvailability();
  assert.equal(nodes['#create-tunnel'].textContent,'创建隧道');assert.equal(nodes['#create-tunnel'].disabled,false);
  state.tunnel_pending=true;context.renderActionAvailability();assert.equal(nodes['#create-tunnel'].textContent,'核对并恢复隧道');
  state.settings.tunnel_id='existing';context.renderActionAvailability();assert.equal(nodes['#create-tunnel'].textContent,'获取隧道令牌 →');
  assert.equal(nodes['#tunnel-maintenance'].open,true);assert.equal(nodes['#account-config-details'].open,true);
  assert.equal(nodes['#tunnel-token-state'].textContent,'待配置');
  state.credentials.tunnel_token=true;context.renderActionAvailability();
  assert.equal(nodes['#tunnel-maintenance'].hidden,false);
  assert.equal(nodes['#tunnel-maintenance'].open,false);
  assert.equal(nodes['#tunnel-token-state'].textContent,'已配置，无需操作');
  assert.equal(nodes['#create-tunnel'].hidden,true);assert.equal(nodes['#create-tunnel'].disabled,true);
  assert.equal(nodes['#connector-start'].hidden,false);assert.equal(nodes['#connector-stop'].hidden,true);
  state.connector.running=true;context.renderActionAvailability();
  assert.equal(nodes['#connector-start'].hidden,true);assert.equal(nodes['#connector-stop'].hidden,false);
  assert.equal(nodes['#tunnel-maintenance'].hidden,false);assert.equal(nodes['#tunnel-token-refresh'].disabled,false);
});

test('running connector is not mislabeled as unconfigured when management credentials are missing',()=>{
  const state=ready();state.cloudflare_setup.ready=false;state.credentials.cf_write_token=false;
  const {context,nodes}=harness(state);context.renderActionAvailability();
  assert.equal(context.connectorReadiness(state).label,'运行中');
  assert.equal(nodes['#connector-start'].textContent,'启动连接器');assert.equal(nodes['#connector-start'].hidden,true);assert.equal(nodes['#connector-start'].disabled,true);
});

test('browser shortcut opens settings and starts authorization; repair remains a separate action',async()=>{
  const calls=[],status={};
  const context={csrf:'session',go:view=>calls.push(view),$:selector=>selector==='#browser-authorize'?{scrollIntoView:()=>calls.push('scroll')}:status,action:async(button,endpoint,message,body)=>calls.push(endpoint)};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('async function startBrowserAuthorization('),source.indexOf('let browserAuthPoll;')),context);
  await context.startBrowserAuthorization({dataset:{}});
  assert.deepEqual(calls,['settings','scroll','cloudflare/browser-authorize']);
  const html=fs.readFileSync(path.join(root,'ui/index.html'),'utf8');
  const alert=html.slice(html.indexOf('id="cloudflare-permission-alert"'),html.indexOf('id="state-refresh-warning"'));
  assert(alert.includes('id="permission-retry"'));
  assert(!alert.includes('id="permission-authorize"'));
  const account=html.slice(html.indexOf('id="account-permission-notice"'),html.indexOf('class="account-domain-heading"'));
  assert(account.includes('id="permission-authorize"'));
  assert(account.includes('id="permission-repair"'));
  assert(!alert.includes('手动编辑 Cloudflare'));
});

test('browser authorization launch failure stays visible next to its entry',async()=>{
  const status={};const context={csrf:'session',go(){},$:selector=>selector==='#browser-authorize'?{scrollIntoView(){}}:status,action:async()=>{throw Error('账户未配置');}};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('async function startBrowserAuthorization('),source.indexOf('let browserAuthPoll;')),context);
  await context.startBrowserAuthorization({dataset:{}});
  assert.equal(status.textContent,'账户未配置');assert.equal(status.className,'form-feedback error');
});

test('authorization progress shows the wait limit and expired callback recovery',()=>{
  const context={Date:{now:()=>1000000},Number,Math};vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function browserAuthMessage('),source.indexOf('function renderBrowserAuth(')),context);
  assert.match(context.browserAuthMessage({phase:'authorizing',updated_at:940,message:'授权中'}),/60 秒/);
  assert.match(context.browserAuthMessage({phase:'authorizing',updated_at:800,message:'授权中'}),/重新打开授权页或取消/);
  assert.equal(context.browserAuthMessage({phase:'error',message:'已超时'}),'已超时');
  context.browserAuthInteraction=false;
  assert.equal(context.browserAuthMessage({phase:'done',message:'旧成功提示'}),'');
  assert.equal(context.browserAuthMessage({phase:'done',authorization_saved:true,message:'旧自动配置完成'}),'');
  context.browserAuthInteraction=true;
  assert.match(context.browserAuthMessage({phase:'done'}),/本次授权已完成/);
  assert.equal(context.browserAuthMessage({phase:'done',authorization_saved:true,message:'本次自动配置完成'}),'本次自动配置完成');
});

test('browser connection has no mandatory authority-token fallback',()=>{
  const html=fs.readFileSync(path.join(root,'ui/index.html'),'utf8');
  assert(!html.includes('id="browser-auth-fallback"'));
  assert(!source.includes("$('#browser-auth-fallback')"));
  const browserPanel=html.slice(html.indexOf('class="browser-auth-panel"'),html.indexOf('<details id="advanced-token-management"'));
  assert(browserPanel.includes('id="browser-authorize"'));
  assert(!browserPanel.includes('name="authority"'));
});


test('closed browser can restart or cancel without waiting for expiry',()=>{
  const nodes={};const context={state:{browser_auth:{phase:'authorizing',message:'等待确认',updated_at:Date.now()/1000},settings:{}},$:selector=>nodes[selector]||=({dataset:{}}),document:{querySelectorAll:()=>[]},Number,Math,Date,setInterval:()=>1,clearInterval(){}};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('let browserAuthPoll;'),source.indexOf('$("#browser-authorize").onclick')),context);
  context.renderBrowserAuth();
  for(const id of ['restart','cancel']){assert.equal(nodes['#browser-authorize-'+id].hidden,false);assert.equal(nodes['#browser-authorize-'+id].disabled,false);}
  nodes['#browser-authorize-cancel'].dataset.busy='1';context.renderBrowserAuth();assert.equal(nodes['#browser-authorize-restart'].disabled,true);
  nodes['#browser-authorize-cancel'].dataset.busy='';context.state.browser_auth.phase='cancelling';context.renderBrowserAuth();assert.equal(nodes['#browser-authorize-restart'].disabled,true);
  context.state.browser_auth.phase='cancelled';context.renderBrowserAuth();assert.equal(nodes['#browser-authorize-restart'].hidden,true);
});


function progressHarness(state){
  const {context}=harness(state);
  vm.runInContext(source.slice(source.indexOf('function setupProgress('),source.indexOf('function renderSetup(')),context);
  return context.setupProgress(state);
}
test('setup only offers adding the first website when none exists',()=>{
  const state=ready();assert.equal(progressHarness(state).next,'add');
  state.sites=[{enabled:true,hostname:'app.example.com',human_check:false}];state.settings.tunnel_id='';
  const progress=progressHarness(state);assert.equal(progress.title,'已登记 1 个网站');assert.equal(progress.next,'sites');assert.equal(progress.label,'创建隧道 →');assert(!progress.detail.includes('登记公网域名'));
});
test('existing websites lead to the remaining verification or publishing step',()=>{
  const state=ready();state.sites=[{enabled:true,hostname:'app.example.com',human_check:true}];state.settings.turnstile_sitekey='';
  assert.equal(progressHarness(state).next,'verification');
  state.settings.turnstile_sitekey='key';assert.equal(progressHarness(state).label,'查看网站发布');assert.equal(progressHarness(state).next,'sites');
  state.published_hosts=['app.example.com'];assert.equal(progressHarness(state).next,'sites');
  state.connector.running=false;assert.equal(progressHarness(state).label,'完成连接器配置 →');
});

test('overview prioritizes missing connection token and uncertain tunnel recovery',()=>{
  const state=ready();state.sites=[{enabled:true,hostname:'app.example.com',human_check:false}];state.credentials.tunnel_token=false;
  assert.equal(progressHarness(state).label,'获取隧道令牌 →');
  state.settings.tunnel_id='';state.tunnel_pending=true;
  assert.equal(progressHarness(state).label,'核对并恢复隧道 →');
});
test('disabled existing websites still lead to management rather than a new website dialog',()=>{
  const state=ready();state.sites=[{enabled:false,hostname:'app.example.com'}];
  assert.equal(progressHarness(state).next,'sites');assert.equal(progressHarness(state).label,'管理网站');
});
test('progress button follows the displayed action without opening an unintended add dialog',()=>{
  let added=0,navigated;const button={dataset:{next:'connector'}};
  const context={$:selector=>selector==='#setup-progress-add'?button:{onclick:()=>added++},go:view=>navigated=view};vm.createContext(context);
  vm.runInContext(source.match(/^\$\('#setup-progress-add'\)\.onclick=.*$/m)[0],context);
  button.onclick();assert.equal(navigated,'connector');assert.equal(added,0);
  button.dataset.next='add';button.onclick();assert.equal(added,1);
});


test('overall setup progress appears only in overview, not account settings',()=>{
  const html=fs.readFileSync(path.join(root,'ui/index.html'),'utf8');
  const overview=html.match(/<section id="view-overview"[^>]*>(.*?)<\/section>/s)[1];
  const settings=html.match(/<section id="view-settings"[^>]*>(.*?)<\/section>/s)[1];
  assert(overview.includes('id="setup-progress"'));assert(overview.includes('id="setup-progress-add"'));
  assert(!settings.includes('setup-progress'));assert(settings.includes('Cloudflare 账户与域名'));
});

test('verification setup lists only enabled protected domains and leaves policies unchanged',()=>{
  const nodes={};const context={$:selector=>nodes[selector]||=({})};vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function renderHumanVerification('),source.indexOf('function renderCredentialsGuide(')),context);
  const current={settings:{turnstile_sitekey:'public-key'},credentials:{turnstile_secret:true},sites:[{hostname:'protected.example.com',enabled:true,human_check:true},{hostname:'public.example.com',enabled:true,human_check:false},{hostname:'disabled.example.com',enabled:false,human_check:true}]};
  const before=JSON.stringify(current);context.renderHumanVerification(current);
  assert(nodes['#widget-scope'].textContent.includes('protected.example.com'));
  assert(!nodes['#widget-scope'].textContent.includes('public.example.com'));
  assert(!nodes['#widget-scope'].textContent.includes('disabled.example.com'));
  assert.equal(nodes['#widget-create'].hidden,true);assert.equal(JSON.stringify(current),before);
});


test('shortcuts reveal optional credential sections before focusing their fields',()=>{
  const context={};vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function revealControl('),source.indexOf('function updateSiteControls(')),context);
  const outer={tagName:'DETAILS',open:false,parentElement:null};
  const inner={tagName:'DETAILS',open:false,parentElement:outer};
  context.revealControl({parentElement:{tagName:'DIV',parentElement:inner}});
  assert.equal(inner.open,true);assert.equal(outer.open,true);
});

test('site editor shows passcode only when enabled and avoids revalidation warning for public sites',()=>{
  const form={dataset:{},elements:{origin:{value:'http://127.0.0.1:8080'},id:{value:'existing'},human_check:{checked:false},passcode_required:{checked:false}}};
  const nodes={'#site-form':form,'#site-forward-lanbridge':{checked:false},'#site-origin-hint':{},'#site-passcode-field':{},'#site-human-mode-field':{},'#policy-save-hint':{}};
  const context={$:key=>nodes[key]};vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function updateSiteControls('),source.indexOf('function selectedProtocols(')),context);
  context.updateSiteControls();assert.equal(nodes['#site-passcode-field'].hidden,true);assert.equal(nodes['#policy-save-hint'].hidden,true);
  form.elements.passcode_required.checked=true;context.updateSiteControls();assert.equal(nodes['#site-passcode-field'].hidden,false);assert.equal(nodes['#policy-save-hint'].hidden,false);
  form.elements.id.value='';context.updateSiteControls();assert.equal(nodes['#policy-save-hint'].hidden,true);
});



test('account configuration reports saved state independently of past authorization attempts',()=>{
 const context={};vm.createContext(context);
 vm.runInContext(source.slice(source.indexOf('function configuredZones('),source.indexOf('function renderAccountConfiguration(')),context);
 const current={settings:{},credentials:{},browser_auth:{phase:'cancelled'}};
 assert.equal(context.accountConfiguration(current).label,'未配置');
 current.settings={account_id:'a',zone_id:'b',zone_name:'example.com'};
 assert.equal(context.accountConfiguration(current).label,'配置不完整');
 current.credentials.cf_write_token=true;
 assert.equal(context.accountConfiguration(current).label,'已配置');
 assert.equal(context.accountConfiguration(current).detail,'已接入 1 个域名');current.settings.zones=[{zone_id:'b',zone_name:'example.com'},{zone_id:'c',zone_name:'other.com'}];assert.equal(context.accountConfiguration(current).detail,'已接入 2 个域名');
 current.cloudflare_permission_issues=[{credential:'cf_read_token'}];
 assert.equal(context.accountConfiguration(current).label,'已配置');
 current.cloudflare_permission_issues=[{credential:'cf_write_token'}];
 assert.equal(context.accountConfiguration(current).label,'凭据待检查');
});


test('overview shows actionable site states first without exposing management controls',()=>{
 const context={esc:value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;')};vm.createContext(context);
 vm.runInContext(source.slice(source.indexOf('function publicationStatus('),source.indexOf('function pauseNotice('))+source.slice(source.indexOf('function overviewSites('),source.indexOf('function render(){')),context);
 const site=(id,extra={})=>({id,name:id,hostname:id+'.example.com',origin:'http://192.168.1.20:8080',enabled:true,...extra});
 const state={settings:{admin_port:8890},sites:[site('normal'),site('paused',{paused:true}),site('pending'),site('disabled',{enabled:false}),site('platform',{target:'lanbridge',name:'<img onerror=x>'})],published_hosts:['normal.example.com','paused.example.com','platform.example.com']};
 const before=JSON.stringify(state),html=context.overviewSites(state);
 assert(html.indexOf('pending.example.com')<html.indexOf('paused.example.com'));
 assert(html.indexOf('paused.example.com')<html.indexOf('normal.example.com'));
 assert(html.includes('已暂停'));assert(html.includes('已停用'));assert(html.includes('127.0.0.1:8890'));
 assert(html.includes('&lt;img'));assert(!html.includes('<img'));assert(!html.includes('data-edit'));assert(!html.includes('data-pause-site'));
 assert.equal(JSON.stringify(state),before);
 state.sites=Array.from({length:8},(_,i)=>site('site'+i));
 const limited=context.overviewSites(state);assert.equal((limited.match(/class="overview-site"/g)||[]).length,6);assert(limited.includes('共 8 个'));
 state.sites=[];assert(context.overviewSites(state).includes('暂无网站'));
});

test('normal running overview has no remaining setup action',()=>{
 const state=ready();state.sites=[{enabled:true,hostname:'app.example.com',human_check:false}];state.published_hosts=['app.example.com'];
 assert.equal(progressHarness(state).complete,true);
 state.connector.running=false;assert.notEqual(progressHarness(state).complete,true);
});

function localSettingHarness(kind, api, loadState){
  const field=kind==='audit'?'limit_mb':'port',selector=kind==='audit'?'#audit-storage-form':'#gateway-port-form';
  const form={dataset:{dirty:'true'},elements:{[field]:{value:kind==='audit'?'5':'8892'}}};
  const feedback={textContent:'',className:'small'},nodes={[selector]:form,[kind==='audit'?'#audit-storage-feedback':'#gateway-port-feedback']:feedback};
  let unlocks=0;
  const context={csrf:'old-session',remoteAccess:false,api,loadState,$:key=>nodes[key],lockForm(){},unlockForm(){unlocks++;}};
  vm.createContext(context);
  const helper=source.indexOf('async function saveLocalSetting(');
  if(helper>=0)vm.runInContext(source.slice(helper,source.indexOf("$('#audit-storage-form').onsubmit=",helper)),context);
  const start=source.indexOf("$('"+selector+"').onsubmit="),end=source.indexOf(kind==='audit'?"$('#audit-load-more').onclick=":'function overviewSites(',start);
  vm.runInContext(source.slice(start,end),context);
  return {context,form,feedback,submit:()=>form.onsubmit({preventDefault(){},currentTarget:form}),unlocks:()=>unlocks};
}
for(const kind of ['audit','gateway']){
  test(kind+' save remains successful when follow-up state read fails',async()=>{
    let reads=0;
    const h=localSettingHarness(kind,async()=>({restart_required:true}),async()=>{reads++;throw Error('状态读取超时，请重试');});
    await h.submit();
    assert.match(h.feedback.textContent,/已保存/);
    assert.match(h.feedback.textContent,/刷新/);
    assert(!h.feedback.className.includes('error'));
    assert.equal(h.form.dataset.dirty,'');
    assert.equal(reads,1);assert.equal(h.unlocks(),1);
  });
  test(kind+' late save cannot clear new-session edits or feedback',async()=>{
    let finish,reads=0;
    const h=localSettingHarness(kind,()=>new Promise(resolve=>finish=resolve),async()=>{reads++;});
    const pending=h.submit();h.context.csrf='new-session';h.form.dataset.dirty='new-edit';h.feedback.textContent='new-session-feedback';
    finish({restart_required:true});await pending;
    assert.equal(h.form.dataset.dirty,'new-edit');
    assert.equal(h.feedback.textContent,'new-session-feedback');
    assert.equal(reads,0);assert.equal(h.unlocks(),1);
  });
  test(kind+' late refresh failure cannot overwrite new-session feedback',async()=>{
    let fail;
    const h=localSettingHarness(kind,async()=>({restart_required:true}),()=>new Promise((resolve,reject)=>fail=reject));
    const pending=h.submit();await new Promise(resolve=>setImmediate(resolve));
    h.context.csrf='new-session';h.feedback.textContent='new-session-feedback';
    fail(Error('old-session-timeout'));await pending;
    assert.equal(h.feedback.textContent,'new-session-feedback');
    assert.equal(h.unlocks(),1);
  });
}

test('all read-only API requests have a deadline while writes keep their existing behavior',async()=>{
  const requests=[];
  const context={csrf:'session',remoteAccess:false,AbortSignal:{timeout:ms=>({milliseconds:ms})},
    fetch:async(url,options)=>{requests.push({url,options});return {ok:true,json:async()=>({})};}};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('async function api('),source.indexOf('function setLoginMethod')),context);
  for(const route of ['bootstrap','temporary-tokens','audit?before=26','local-login/browsers','local-login/request/test']){
    await context.api(route);
    assert.equal(requests.at(-1).options.signal?.milliseconds,15000,route);
  }
  await context.api('audit/settings',{limit_mb:5});assert.equal(requests.at(-1).options.signal,undefined);
  context.fetch=async()=>{throw Object.assign(Error('timeout'),{name:'TimeoutError'});};
  await assert.rejects(context.api('temporary-tokens'),/读取超时/);
});


test('inline operation errors refresh state without duplicate global feedback',async()=>{
  let notices=0;
  const context=asyncHarness({api:async(path)=>{if(path==='state')return {failed:true};throw Error('授权续期失败');},toast:()=>notices++});
  const button={dataset:{},disabled:false};
  await assert.rejects(context.action(button,'cloudflare/zones',null,{},{inlineError:true}),/续期失败/);
  assert.equal(notices,0);assert.equal(context.state.failed,true);assert.equal(button.disabled,false);
  await assert.rejects(context.action(button,'cloudflare/zones',null,{}),/续期失败/);
  assert.equal(notices,1);
});


test('permission recovery follows OAuth, failed operation and updated credentials',()=>{
 const context={};vm.createContext(context);
 vm.runInContext(source.slice(source.indexOf('function permissionRecovery('),source.indexOf('function renderPermissionRecovery(')),context);
 const state={token_management:{managed:{kind:'oauth'}},cloudflare_permission_issues:[{credential:'cf_write_token',detail:'Cloudflare API HTTP 403：创建域名区域失败',status:'last_failure'}]};
 let result=context.permissionRecovery(state);assert.equal(result.oauth,true);assert.equal(result.repair,false);assert.equal(result.replace,false);assert.equal(result.domain,true);
 state.cloudflare_permission_issues[0].status='needs_recheck';assert.match(context.permissionRecovery(state).guide,/不代表当前授权仍然失败/);
 state.cloudflare_permission_issues[0].credential='cf_read_token';result=context.permissionRecovery(state);assert.equal(result.oauth,false);assert.equal(result.credential,'cf_read_token');assert.equal(result.repair,true);
 state.token_management.managed.kind='account';assert.equal(context.permissionRecovery(state).repair,false);
});


test('switching browser restarts only the active consent in the newly selected browser',async()=>{
 const select={value:'chrome',disabled:false},restart={},calls=[];
 const context={state:{browser_auth:{phase:'authorizing',browser:'default'}},localStorage:{setItem(){}},$:id=>id==='#cloudflare-browser'?select:restart,
 recoverBrowserAuthorization:async(button,operation)=>{assert.equal(select.disabled,true);calls.push([button,operation,select.value]);},renderBrowserAuth:()=>{select.disabled=false;}};
 vm.createContext(context);vm.runInContext(source.slice(source.indexOf('async function changeAuthorizationBrowser('),source.indexOf("$('#cloudflare-browser').onchange=")),context);
 await context.changeAuthorizationBrowser();assert.equal(calls.length,1);assert.deepEqual(calls[0],[restart,'restart','chrome']);assert.equal(select.disabled,false);
 context.state.browser_auth.browser='chrome';await context.changeAuthorizationBrowser();assert.equal(calls.length,1);
 context.state.browser_auth.phase='done';select.value='default';await context.changeAuthorizationBrowser();assert.equal(calls.length,1);
});

test('an unconfirmed domain flags settings without marking existing websites',()=>{
  const state=ready();state.domain_onboarding={domain:'new.example.net',phase:'preview'};
  const {context}=harness(state);assert.deepEqual(Array.from(marked(context)),['settings']);
  state.domain_onboarding.phase='done';assert.deepEqual(Array.from(marked(context)),[]);
});

test('background publication is progress rather than an instruction to publish again',()=>{
  const state=ready();state.sites=[{id:'pending',hostname:'app.example.com',enabled:true,human_check:false}];state.published_hosts=[];state.publication_needs_review=true;
  state.site_publication={phase:'publishing'};
  const {context}=harness(state);assert.deepEqual(Array.from(context.navigationIssues(state).sites),[]);
  state.site_publication.phase='failed';assert(context.navigationIssues(state).sites.length>0);
});

for(const operation of ['start','restart','select','create','token'])for(const success of [false,true]){
 test('late '+operation+' callback cannot rewrite a newer session; success='+success,async()=>{
  let finish,fail;const nodes={};
  const node=key=>nodes[key]||(nodes[key]={dataset:{},value:'z',textContent:'',className:'',scrollIntoView(){},focus(){}});
  const context={csrf:'old',state:{settings:{tunnel_id:''},connector:{running:false}},$:node,go(){},loadState:async()=>{},action:()=>new Promise((resolve,reject)=>{finish=resolve;fail=reject;})};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('async function startBrowserAuthorization('),source.indexOf('let browserAuthPoll;')),context);
  vm.runInContext(source.slice(source.indexOf("$('#browser-zone-confirm').onclick"),source.indexOf('function renderTokenOperation(')),context);
  vm.runInContext(source.slice(source.indexOf('function locateTunnelToken('),source.indexOf('function permissionRecovery(')),context);
  const button={dataset:{}},pending=operation==='start'?context.startBrowserAuthorization(button):operation==='restart'?context.recoverBrowserAuthorization(button,'restart'):node(operation==='select'?'#browser-zone-confirm':operation==='create'?'#create-tunnel':'#tunnel-token-refresh').onclick({currentTarget:button});
  const feedback=node(['create','token'].includes(operation)?operation==='create'?'#tunnel-feedback':'#tunnel-token-feedback':'#browser-auth-status');
  context.csrf='new';context.state=null;feedback.textContent='当前会话提示';feedback.className='current';
  if(success)finish({});else fail(Error('旧请求失败'));
  await pending;assert.equal(feedback.textContent,'当前会话提示');assert.equal(feedback.className,'current');
 });
}

for(const kind of ['settings','credentials','verification','password','site'])for(const success of [false,true]){
 test('late '+kind+' save preserves newer-session input and feedback; success='+success,async()=>{
  let finish,fail,requests=0;const nodes={};
  const node=key=>nodes[key]||(nodes[key]={dataset:{},textContent:'',className:'',open:true});
  const form={dataset:{},elements:{turnstile_sitekey:{value:'public-key'},turnstile_secret:{value:'fake-test-secret'},passcode:{value:'new-input'},enabled:{checked:true},human_check:{checked:false},passcode_required:{checked:false}},reset(){throw Error('Old save cleared current input');}};
  const values={cf_read_token:'test-read',allowed_countries:'',allowed_ips:'',requests_per_minute:'180',session_minutes:'60'};
  const pendingApi=()=>{requests++;return new Promise((resolve,reject)=>{finish=resolve;fail=reject;});};
  const context={csrf:'old',replacingWriteToken:false,state:{settings:{turnstile_sitekey:'public-key'},credentials:{turnstile_secret:true},cloudflare_setup:{ready:true}},$:node,FormData:class{constructor(){}[Symbol.iterator](){return Object.entries(values)[Symbol.iterator]();}},action:pendingApi,api:pendingApi,selectedProtocols:()=>['http'],lockForm(){},unlockForm(){},renderSetup(){},renderActionAvailability(){},loadState:async()=>{},invalidatePlan(){throw Error('Old save invalidated current session');},toast(){},fillSettings(){},setLocalSecurity(){},showAuth(){throw Error('Old password save logged out current session');}};
  vm.createContext(context);
  const selector=kind==='verification'?'verification-credentials-form':kind+'-form';
  const start=source.indexOf("$('#"+selector+"').onsubmit");
  const end=kind==='verification'?source.indexOf("$('#password-form').onsubmit",start):source.indexOf('\n',start);
  vm.runInContext(source.slice(start,end),context);
  const pending=node('#'+selector).onsubmit({preventDefault(){},target:form,currentTarget:form,submitter:{dataset:{}}});
  const feedback=node(kind==='site'?'#site-error':kind==='verification'?'#verification-feedback':'#'+kind+'-feedback');
  context.csrf='new';feedback.textContent='当前会话提示';
  if(success)finish({});else fail(Error('旧请求失败'));
  await pending;assert.equal(feedback.textContent,'当前会话提示');assert.equal(form.elements.passcode.value,'new-input');assert.equal(form.elements.turnstile_secret.value,'fake-test-secret');assert.equal(requests,1);
 });
}

for(const kind of ['zones','attach','retry-publication','preview','read'])for(const success of [false,true]){
 test('late '+kind+' result neither mutates current inputs nor starts another write; success='+success,async()=>{
  let finish,fail,requests=0;const nodes={};
  const node=key=>nodes[key]||(nodes[key]={dataset:{},value:'current-input',textContent:'',className:'',elements:{cf_read_token:{value:'current-input'}}});
  const pendingApi=()=>{requests++;return new Promise((resolve,reject)=>{finish=resolve;fail=reject;});};
  const context={csrf:'old',state:{settings:{}},$:node,api:pendingApi,action:pendingApi,loadState:async()=>{},renderActionAvailability(){},invalidatePlan(){},configuredZones:()=>[],esc:x=>x,publicationRetryPending:false};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf("$('#read-token-save').onclick="),source.indexOf("$('#sidebar-restart').onclick=")),context);
  vm.runInContext(source.slice(source.indexOf("$('#publication-retry').onclick="),source.indexOf("api('bootstrap').then")),context);
  const input={value:'current-input'},button={dataset:{},form:{elements:{cf_read_token:input}}};
  const feedback=node(['retry-publication','preview'].includes(kind)?'#publish-feedback':kind==='read'?'#read-token-feedback':'#zones-feedback');
  const pending=kind==='attach'?context.attachZone(button,{zone_name:'example.com'}):node(kind==='zones'?'#zones-discover':kind==='read'?'#read-token-save':kind==='preview'?'#preview':'#publication-retry').onclick({currentTarget:button});
  context.csrf='new';feedback.textContent='当前会话提示';
  if(success)finish({zones:[],revision:'old',routes_changed:false,dns:[]});else fail(Error('旧请求失败'));
  await pending;assert.equal(feedback.textContent,'当前会话提示');assert.equal(input.value,'current-input');assert.equal(requests,1);
 });
}


test('configuration actions refresh state once, including inline failures',async()=>{
 for(const fails of [false,true]){
  const calls=[];const context=asyncHarness({api:async(path)=>{calls.push(path);if(path==='state')return {settings:{},credentials:{},cloudflare_setup:{}};if(fails)throw Error('failed');return {saved:true};}});
  const button={dataset:{},disabled:false};
  if(fails)await assert.rejects(context.action(button,'settings',null,{},{inlineError:true}),/failed/);
  else await context.action(button,'settings',null,{});
  assert.deepEqual(calls,['settings','state']);
 }
 for(const [start,end] of [["$('#settings-form').onsubmit=","$('#credentials-form').onsubmit="],["$('#tunnel-token-refresh').onclick=",'function permissionRecovery('],["$('#token-manager-form').onsubmit=","$('#token-manager-form').oninput="]]){
  const block=source.slice(source.indexOf(start),source.indexOf(end,source.indexOf(start)));
  assert(block.includes('await action('));assert(!block.includes('await loadState('),start);
 }
});

for(const changed of [false,true])test(`remote verification response respects login generation changed=${changed}`,async()=>{
 let finish,reloads=0,auths=0;
 const context={csrf:'old-session',remoteAccess:true,AbortSignal:{timeout:()=>null},
  fetch:()=>new Promise(resolve=>finish=resolve),location:{reload(){reloads++}},showAuth(){auths++}};
 vm.createContext(context);
 vm.runInContext(source.slice(source.indexOf('async function api('),source.indexOf('function setLoginMethod(')),context);
 const pending=context.api('state');if(changed)context.csrf='new-session';
 finish({ok:false,status:401,json:async()=>({verification_required:true,detail:'verification needed'})});
 await assert.rejects(pending);assert.equal(reloads,changed?0:1);assert.equal(auths,0);
});
