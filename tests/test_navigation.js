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
test('permission failure does not spread to website management or history tabs',()=>{
  const state=ready();state.cloudflare_permission_issues=[{detail:'Cloudflare API HTTP 403：创建 Tunnel失败'}];
  const {context}=harness(state);assert.deepEqual(Array.from(marked(context)),['settings']);
});

test('website publication status distinguishes pending publication, removal and verification',()=>{
  const context={};vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function publicationStatus('),source.indexOf('function table(')),context);
  const site={hostname:'app.example.com',enabled:true},current={published_hosts:[]};
  assert.equal(context.publicationStatus(site,current).label,'待发布');
  current.published_hosts=[site.hostname];assert.equal(context.publicationStatus(site,current).label,'已发布');
  current.publication_needs_review=true;assert.equal(context.publicationStatus(site,current).label,'待核验');
  site.paused=true;assert.equal(context.publicationStatus(site,current).label,'已暂停');
  site.paused=false;
  site.enabled=false;assert.equal(context.publicationStatus(site,current).label,'待停用');
  current.published_hosts=[];assert.equal(context.publicationStatus(site,current).label,'已停用');
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
  const {context}=harness(state);assert.deepEqual(Array.from(marked(context)),['settings']);state.credentials.turnstile_secret=true;assert.deepEqual(Array.from(marked(context)),[]);
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
  const {context,nodes}=harness(state);context.renderNavigationIssues();assert.equal(nodes['#settings-nav-status'].hidden,true);assert.deepEqual(Array.from(marked(context)),['connector']);
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
  assert.equal(nodes['#connector-nav-status'].textContent,'待创建隧道');
  state.cloudflare_permission_issues=[];context.renderNavigationIssues();assert.equal(nodes['#settings-nav-status'].hidden,true);assert.equal(nodes['#connector-nav-status'].textContent,'待创建隧道');
  state.settings.tunnel_id='t';context.renderNavigationIssues();assert.equal(nodes['#connector-nav-status'].textContent,'待获取连接令牌');
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

test('tunnel actions distinguish creation, recovery, retrieval and completed configuration',()=>{
  const state=ready();state.connector.running=false;state.settings.account_id='account';state.credentials.cf_write_token=true;
  const {context,nodes}=harness(state);
  state.settings.tunnel_id='';state.credentials.tunnel_token=false;context.renderActionAvailability();
  assert.equal(nodes['#create-tunnel'].textContent,'创建隧道');assert.equal(nodes['#create-tunnel'].disabled,false);
  state.tunnel_pending=true;context.renderActionAvailability();assert.equal(nodes['#create-tunnel'].textContent,'核对并恢复隧道');
  state.settings.tunnel_id='existing';context.renderActionAvailability();assert.equal(nodes['#create-tunnel'].textContent,'获取连接令牌');
  state.credentials.tunnel_token=true;context.renderActionAvailability();
  assert.equal(nodes['#create-tunnel'].textContent,'隧道已配置');assert.equal(nodes['#create-tunnel'].disabled,true);
  assert.equal(nodes['#tunnel-maintenance'].hidden,false);assert.equal(nodes['#tunnel-token-refresh'].disabled,false);
});

test('running connector is not mislabeled as unconfigured when management credentials are missing',()=>{
  const state=ready();state.cloudflare_setup.ready=false;state.credentials.cf_write_token=false;
  const {context,nodes}=harness(state);context.renderActionAvailability();
  assert.equal(context.connectorReadiness(state).label,'运行中');
  assert.equal(nodes['#connector-start'].textContent,'连接器运行中');assert.equal(nodes['#connector-start'].disabled,true);
});

test('browser shortcut opens settings and starts authorization; repair remains a separate action',async()=>{
  const calls=[],status={};
  const context={go:view=>calls.push(view),$:selector=>selector==='#browser-authorize'?{scrollIntoView:()=>calls.push('scroll')}:status,action:async(button,endpoint,message,body)=>calls.push(endpoint)};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('async function startBrowserAuthorization('),source.indexOf('let browserAuthPoll;')),context);
  await context.startBrowserAuthorization({dataset:{}});
  assert.deepEqual(calls,['settings','scroll','cloudflare/browser-authorize']);
  const html=fs.readFileSync(path.join(root,'ui/index.html'),'utf8');
  const alert=html.slice(html.indexOf('id="cloudflare-permission-alert"'),html.indexOf('id="state-refresh-warning"'));
  assert(alert.includes('data-browser-authorize="true"'));
  assert(alert.includes('修复当前令牌权限'));
  assert(alert.includes('data-token-manager="true"'));
  assert(!alert.includes('手动编辑 Cloudflare'));
});

test('browser authorization launch failure stays visible next to its entry',async()=>{
  const status={};const context={go(){},$:selector=>selector==='#browser-authorize'?{scrollIntoView(){}}:status,action:async()=>{throw Error('账户未配置');}};
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
});

test('browser connection has no mandatory authority-token fallback',()=>{
  const html=fs.readFileSync(path.join(root,'ui/index.html'),'utf8');
  assert(!html.includes('id="browser-auth-fallback"'));
  assert(!source.includes("$('#browser-auth-fallback')"));
  const browserPanel=html.slice(html.indexOf('class="browser-auth-panel"'),html.indexOf('<h3>高级：'));
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
  const progress=progressHarness(state);assert.equal(progress.title,'已登记 1 个网站');assert.equal(progress.next,'connector');assert.equal(progress.label,'创建隧道 →');assert(!progress.detail.includes('登记公网域名'));
});
test('existing websites lead to the remaining verification or publishing step',()=>{
  const state=ready();state.sites=[{enabled:true,hostname:'app.example.com',human_check:true}];state.settings.turnstile_sitekey='';
  assert.equal(progressHarness(state).next,'verification');
  state.settings.turnstile_sitekey='key';assert.equal(progressHarness(state).label,'发布网站映射 →');
  state.published_hosts=['app.example.com'];assert.equal(progressHarness(state).next,'sites');
  state.connector.running=false;assert.equal(progressHarness(state).label,'完成连接器配置 →');
});

test('overview prioritizes missing connection token and uncertain tunnel recovery',()=>{
  const state=ready();state.sites=[{enabled:true,hostname:'app.example.com',human_check:false}];state.credentials.tunnel_token=false;
  assert.equal(progressHarness(state).label,'获取连接令牌 →');
  state.settings.tunnel_id='';state.tunnel_pending=true;
  assert.equal(progressHarness(state).label,'核对并恢复隧道 →');
});
test('disabled existing websites still lead to management rather than a new website dialog',()=>{
  const state=ready();state.sites=[{enabled:false,hostname:'app.example.com'}];
  assert.equal(progressHarness(state).next,'sites');assert.equal(progressHarness(state).label,'管理网站 →');
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
